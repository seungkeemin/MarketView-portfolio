"""화면용 HTTP API (/api). 계산은 queries.py, 수집은 ingest.py."""

from __future__ import annotations

import math
import threading
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from . import backup, cloudsync, db, ingest, queries
from .collect.base import Fetched
from .config import layout
from .config.series import BY_KEY
from .config.settings import canonical_date, now_kst

router = APIRouter(prefix="/api")

# 수집 중 여부는 app.py 가 쥔 락을 공유한다 (app.py 가 주입)
collect_lock: threading.Lock = threading.Lock()
start_collect = None  # app.py 가 채운다: callable()


def _conn():
    return db.connect()


def _asof(raw: str | None) -> str | None:
    try:
        return canonical_date(raw) if raw else None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _view(conn, asof: str | None) -> tuple[str | None, dict | None]:
    """(직접 고른 기준일, 권역별 마감일). 날짜를 골랐으면 그 날짜가 우선, 아니면 권역 마감일(장중 값 미사용)."""
    day = _asof(asof)
    return day, (queries.common_close_dates(conn) if day is None else None)


@router.get("/meta")
def meta() -> dict:
    conn = _conn()
    try:
        return {
            "tabs": [{"id": k, "label": v["label"], "default_sel": v["default_sel"], "keys": queries.visible_keys(k),
                      "groups": [{"id": g["id"], "label": g["label"]} for g in v["groups"]]}
                     for k, v in layout.TABS.items()],
            "known_keys": queries.all_keys(),
            "curves": [{"id": c.id, "label": c.label, "unit": c.unit, "src": c.src} for c in layout.CURVES.values()],
            "last_obs": queries.last_obs_date(conn), "today": queries.default_today(),
            "closes": queries.common_close_dates(conn),
            # 수동 입력 기본 날짜: 국고채 3년의 최신 관측일 (스프레드는 같은 관측일끼리만 계산한다)
            "input_date": queries.latest_date(conn, "ktb_3y") or queries.default_today(),
        }
    finally:
        conn.close()


@router.get("/tiles")
def tiles(tab: str, asof: str | None = None) -> dict:
    if tab not in layout.TABS:
        raise HTTPException(404, "알 수 없는 탭")
    conn = _conn()
    try:
        day, closes = _view(conn, asof)
        return {**queries.tiles(conn, tab, day, closes), "closes": closes}
    finally:
        conn.close()


@router.get("/find")
def find(q: str = "", asof: str | None = None) -> dict:
    """화면의 지표 검색 (라벨·키·그룹·탭). 빈 검색어는 빈 결과."""
    conn = _conn()
    try:
        day, closes = _view(conn, asof)
        return {"q": q, "items": queries.search_series(conn, q[:60], day, closes=closes)}
    finally:
        conn.close()


@router.get("/series")
def series(keys: str = Query(..., description="쉼표 구분 series_key / 파생 키"), asof: str | None = None) -> dict:
    wanted = [k for k in keys.split(",") if k][:12]
    conn = _conn()
    try:
        day, closes = _view(conn, asof)
        return queries.series_many(conn, wanted, day, closes=closes)
    finally:
        conn.close()


@router.get("/curve")
def curve(family: str, dates: str = "", asof: str | None = None) -> dict:
    if family not in layout.CURVES:
        raise HTTPException(404, "알 수 없는 커브")
    conn = _conn()
    try:
        day, closes = _view(conn, asof)
        base = queries.asof_for(layout.CURVES[family].keys[0], day, closes)
        wanted = [canonical_date(d) for d in dates.split(",") if d] if dates else []
        default = queries.curve_default_date(conn, family, base)
        if not wanted:
            if default is None:
                return {**queries.curve(conn, family, []), "default_date": None}
            d0 = date.fromisoformat(default)
            wanted = [default, (d0 - timedelta(days=7)).isoformat(), (d0 - timedelta(days=30)).isoformat()]
        elif default and default not in wanted:
            wanted.insert(0, default)            # 비교일만 받아도 기준 커브를 함께 돌려준다 (화면이 요청 한 번으로 그린다)
        return {**queries.curve(conn, family, wanted), "default_date": default}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


class ManualIn(BaseModel):
    series_key: str
    obs_date: str
    value: float
    note: str | None = None


@router.put("/observation")
def put_observation(body: ManualIn) -> dict:
    """소스가 없는(수동) 항목만 직접 입력한다. 자동 수집 항목은 덮어쓰지 않는다."""
    spec = BY_KEY.get(body.series_key)
    if spec is None or spec.provider != "manual":
        raise HTTPException(400, "수동 입력 항목이 아닙니다")
    try:
        day = canonical_date(body.obs_date)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not math.isfinite(body.value):
        raise HTTPException(400, "값이 숫자가 아닙니다")
    if date.fromisoformat(day) > date.fromisoformat(queries.default_today()):
        raise HTTPException(400, "미래 날짜는 입력할 수 없습니다")
    conn = _conn()
    try:
        action = db.upsert_observation(
            conn, Fetched(body.series_key, body.value, date.fromisoformat(day), "user", note=body.note),
            "daily", "manual", now_kst())
        conn.commit()
        return {"saved": True, "action": action}
    finally:
        conn.close()


def data_versions(conn) -> dict:
    """패널별 데이터 버전 = 그 데이터가 바뀔 때마다 늘어나는 행 id 의 최댓값. 화면은 버전이 바뀐 패널만 다시 그린다
    (예전에는 15분마다 뉴스 회차가 끝날 때도 차트를 다시 만들어 확대 상태가 풀렸다). 시세는 값이 같으면 observation_log 에
    행이 생기지 않으므로 실제로 값이 들어오거나 바뀔 때만 오른다."""
    one = lambda sql: conn.execute(sql).fetchone()[0] or 0       # noqa: E731
    return {"obs": one("SELECT MAX(id) FROM observation_log"),
            "news": f'{one("SELECT MAX(id) FROM news")}.{one("SELECT MAX(id) FROM tg_message")}.{one("SELECT MAX(id) FROM tg_message_edit")}',
            "events": one("SELECT MAX(id) FROM event_log")}


@router.get("/status")
def status() -> dict:
    conn = _conn()
    try:
        runs = [dict(r) for r in conn.execute(
            "SELECT id, origin, kind, started_at, finished_at, status FROM ingest_run ORDER BY id DESC LIMIT 6")]
        last_ok = conn.execute("SELECT MAX(finished_at) FROM ingest_run WHERE status IN ('ok','partial')").fetchone()[0]
        failures = ingest.latest_failures(conn)
        rep = conn.execute("SELECT id, report_date, version, pdf_status, drive_status, message FROM report_run "
                           "ORDER BY id DESC LIMIT 1").fetchone()
        cloud = cloudsync.last_state(conn)
        remote = (cloud or {}).get("remote_status") or {}
        failures += [{"source": "cloud-merge", "series_key": "-", "message": p} for p in (cloud or {}).get("problems", [])]
        if remote.get("status") in ("failed", "partial"):           # 클라우드 회차의 실패도 같은 경고 목록에 보인다
            failures += [{"source": f"cloud:{f['source']}", "series_key": f["key"], "message": f["message"]}
                         for f in remote.get("failures", [])]
        return {"collecting": collect_lock.locked(), "last_refresh": last_ok, "recent_runs": runs,
                "failures": failures, "report": dict(rep) if rep else None, "backup": backup.last_status(conn),
                "versions": data_versions(conn),
                # 클라우드 마지막 동기화 = 클라우드 수집기가 마지막으로 돈 시각 (원격 status.json)
                "cloud_sync": remote.get("updated_at") if cloud and cloud["state"] == "ok" else None,
                "cloud": {"state": cloud["state"], "message": cloud["message"], "checked_at": cloud["at"],
                          "mode": remote.get("mode"), "status": remote.get("status"),
                          "added": cloud.get("added")} if cloud else None}
    finally:
        conn.close()


@router.post("/refresh")
def refresh() -> dict:
    if collect_lock.locked():
        return {"started": False, "reason": "수집 진행 중"}
    if start_collect is None:
        raise HTTPException(503, "수집기가 준비되지 않았습니다")
    start_collect()
    return {"started": True}
