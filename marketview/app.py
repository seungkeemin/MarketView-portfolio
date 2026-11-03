"""FastAPI 엔트리.

앱이 시작되면 응답을 막지 않고 백그라운드 스레드에서
  1) 일별 소스 백필(마지막 관측일 이후)  2) 장중 스냅샷  3) 이후 주기적 스냅샷
을 돌린다. 수집 실패는 서버를 멈추지 않는다 (ingest_result 에 남는다).
"""

from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api, api_news, api_report, api_user, backup, cloudsync, db, events, ingest, news
from .collect import telegram
from .report import auto as report_auto
from .collect.base import scrub
from .config.settings import today_kst

LOOP_INTERVAL_SEC = 15 * 60       # 주기 루프: 클라우드 병합·뉴스/텔레그램 수집·백업/리포트 점검 (옛 장중 스냅샷 주기에서 이름을 바꿨다)
DAILY_RETRY_SEC = 3600             # 일별 수집에 오류(failed·partial)가 있으면 이 시간 뒤 다시 시도한다
DAILY_REFRESH_SEC = 6 * 3600       # 켜 둔 채로 이 시간이 지나거나 날짜가 바뀌면 일별 소스를 다시 받는다

_lock = threading.Lock()          # 수집은 한 번에 하나만 (SQLite 쓰기 경합과 중복 호출 방지)
_stop = threading.Event()
_last_daily: dict = {"date": None, "at": 0.0}
_STATIC = Path(__file__).parent / "static"


def _sync_events(conn) -> None:
    """공식 일정·FRED 발표일을 맞춘다. 실패해도 시세 수집과 화면은 계속된다."""
    run = ingest.Run(conn, "events", "local")
    try:
        res = events.sync_all(conn, today_kst())
        run.record("official", "fomc,bok", "ok", res["official"], "공식 일정")
        for rid, msg in res["fred"].items():
            run.record("fred-events", f"release:{rid}", "error" if msg.startswith("error") else "ok", 0, msg)
    except Exception as exc:  # noqa: BLE001
        run.record("events", "sync", "error", 0, scrub(str(exc)))
    run.finish()


def _news_cycle(conn) -> None:
    """뉴스 RSS 와 (설정돼 있으면) 텔레그램을 수집한다. 피드·채널 하나가 실패해도 나머지는 계속된다."""
    run = ingest.Run(conn, "news", "local")
    try:
        for r in news.collect_rss(conn):
            run.record("rss", r["feed"], r["status"], r["new"], r["message"])
        for r in telegram.collect(conn):
            run.record("telegram", r["channel"], r["status"], r["new"], r["message"])
    except Exception as exc:  # noqa: BLE001
        run.record("news", "cycle", "error", 0, scrub(str(exc)))
    run.finish()


def _record_failure(kind: str, exc: Exception) -> None:
    """단계 밖에서 터진 예외도 ingest_run 에 남긴다(화면에서 이유를 볼 수 있게). 기록 자체가 실패하면 조용히 넘어간다."""
    try:
        conn = db.connect()
        try:
            run = ingest.Run(conn, kind, "local")
            run.record("app", kind, "error", 0, scrub(f"{type(exc).__name__}: {exc}"))
            run.finish()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass


def _mark_daily(ok: bool) -> None:
    """일별 수집 시도를 기록한다. 오류가 있었으면(failed·partial) DAILY_RETRY_SEC 뒤 다시 시도하게 한다."""
    now = time.monotonic()
    if ok:
        _last_daily["date"], _last_daily["at"] = today_kst(), now
    else:
        _last_daily["date"], _last_daily["at"] = today_kst(), now - DAILY_REFRESH_SEC + DAILY_RETRY_SEC


def _run_stage(stage) -> None:
    """수집 단계 하나를 자기 연결로 실행한다. 한 단계가 죽어도 다른 단계는 계속된다."""
    try:
        conn = db.connect()
        try:
            stage(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        _record_failure("stage", exc)


def _daily_stage(conn) -> None:
    try:
        status = ingest.run_daily(conn, log=lambda m: None)
    except Exception:
        _mark_daily(False)
        raise
    _mark_daily(status == "ok")          # partial(일부 소스 오류)도 DAILY_RETRY_SEC 뒤 다시 받는다 — 6시간을 기다리면 그 값이 반나절 비어 있다


def _collect(daily: bool = True) -> None:
    if not _lock.acquire(blocking=False):
        return
    try:
        conn = db.connect()
        try:
            cloudsync.sync(conn)                       # 클라우드 수집분 병합 (원격이 없거나 실패해도 조용히 넘어간다)
        finally:
            conn.close()
        # 일별·이벤트·스냅샷·뉴스는 서로 다른 표를 쓰고 대부분 네트워크 대기라 동시에 돌린다(연결은 단계마다 따로, WAL).
        stages = ([_daily_stage, _sync_events] if daily else []) + [
            _news_cycle]
        with ThreadPoolExecutor(max_workers=len(stages)) as pool:
            list(pool.map(_run_stage, stages))
    except Exception as exc:  # noqa: BLE001 - 수집 스레드는 죽지 않는다
        _record_failure("collect", exc)
    finally:
        _lock.release()


def _maintenance() -> None:
    """오늘 DB 백업이 없으면 만들고 주간 CSV 가 밀렸으면 만든다. 실패해도 수집·화면은 계속된다."""
    try:
        conn = db.connect()
        try:
            report_auto.run_due(conn)          # 오늘 리포트 + 꺼져 있던 날 따라잡기
            backup.run_due(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        _record_failure("maintenance", exc)


def daily_due() -> bool:
    """마지막 일별 수집 뒤 날짜가 바뀌었거나 DAILY_REFRESH_SEC 가 지났으면 True."""
    return _last_daily["date"] != today_kst() or time.monotonic() - _last_daily["at"] >= DAILY_REFRESH_SEC


def _worker() -> None:
    _collect(daily=True)
    _maintenance()
    while not _stop.wait(LOOP_INTERVAL_SEC):
        _collect(daily=daily_due())
        _maintenance()          # 자정을 넘겨 켜 둔 경우에도 새 날짜의 백업이 만들어진다


def _start_manual_collect() -> None:
    threading.Thread(target=_collect, kwargs={"daily": True}, daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    db.migrate(conn)
    ingest.abort_stale(conn)
    events.sync_official(conn)               # 네트워크 없이 공식 일정부터 채운다 (FRED 발표일은 수집 스레드가 이어서)
    conn.close()
    _stop.clear()
    threading.Thread(target=_worker, name="collector", daemon=True).start()
    yield
    _stop.set()


api.collect_lock = _lock
api.start_collect = _start_manual_collect

app = FastAPI(title="MarketView", lifespan=lifespan)
# 127.0.0.1 로만 연다: 다른 호스트 이름으로 들어오는 요청(DNS rebinding)을 거부한다. testserver 는 테스트 클라이언트용.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "testserver"])
app.add_middleware(GZipMiddleware, minimum_size=1024)      # 시계열 JSON(수십 KB)·app.js 전송량 절감. 이미 압축된 woff2 는 건드리지 않는다
_READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def reject_cross_site_writes(request, call_next):
    """다른 사이트의 페이지가 127.0.0.1:8790 으로 쓰기 요청(수집·리포트 생성·포지션·이벤트)을 보내지 못하게 한다.
    호스트 검사(TrustedHost)는 DNS rebinding 만 막는다. 브라우저는 교차 출처 POST 에도 Origin·Sec-Fetch-Site 를 붙이므로
    같은 출처(또는 헤더 없는 직접 호출)만 통과시킨다. 조회(GET)는 SOP 가 응답을 막아 주므로 건드리지 않는다."""
    if request.method not in _READ_ONLY_METHODS:
        site, origin = request.headers.get("sec-fetch-site"), request.headers.get("origin")
        if (site and site not in ("same-origin", "none")) or (origin and urlsplit(origin).netloc != request.headers.get("host")):
            return JSONResponse({"detail": "교차 출처 요청은 허용되지 않습니다"}, status_code=403)
    return await call_next(request)


app.include_router(api.router)
app.include_router(api_user.router)
app.include_router(api_news.router)
app.include_router(api_report.router)
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    # app.js·app.css 는 파일 수정 시각을 주소에 붙여, 코드를 고친 뒤에도 브라우저가 옛 파일을 계속 쓰지 않게 한다
    # (캐시된 옛 app.js 때문에 고친 화면 동작이 반영되지 않던 문제)
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("app.css", "app.js"):
        html = html.replace(f'/static/{name}"', f'/static/{name}?v={int((_STATIC / name).stat().st_mtime)}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
