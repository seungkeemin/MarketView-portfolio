"""수집 오케스트레이터. 로컬 앱 시작 시 백필, 새로고침, GitHub Actions 가 모두 이 모듈을 쓴다.

  python -m marketview.ingest daily      # 일별 소스 전부 (마지막 관측일 - 겹침 ~ 오늘). 처음이면 과거 전체 백필

한 소스·한 항목이 실패해도 나머지는 계속 간다. 결과는 ingest_run / ingest_result 에 남는다.
수집 못 한 값은 행을 만들지 않는다 (0 으로 채우지 않는다).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from . import db
from .collect import ecos, fred, krx, smbs, yahoo
from .collect.base import Failed, Fetched, FetchResult, Missing, scrub
from .config import sources as srcs
from .config.settings import KST, now_kst, today_kst

# 처음 받을 과거 길이(년). KRX·smbs 는 영업일마다 한 번씩 호출해야 해서 짧게 시작한다.
HISTORY_YEARS = {"ecos": 3, "fred": 3, "yahoo": 3, "krx": 1, "smbs": 1}
# 소스별 동시 호출 수. 네트워크 대기가 대부분이라 순차로 돌리면 매 수집이 ~30초(+스냅샷 9초) 걸려 '수집 중' 이 길었다.
# 야후는 비공식이라 보수적으로, smbs 는 날짜별 페이지를 받아 1개씩. DB 쓰기는 호출한 스레드 하나에서만 한다(SQLite 연결 하나).
CONCURRENCY = {"ecos": 4, "fred": 8, "yahoo": 3, "krx": 3, "smbs": 1}
OVERLAP_DAYS = 7      # 마지막 관측일보다 이만큼 앞에서 다시 받는다 (정정·지연 게시 흡수)
# 관측일 사이가 이보다 길면 빈 구간으로 보고 다시 받는다. 가장 긴 연휴(2025 추석)가 8일 간격이었다 (실제 DB 확인 2026-10-15).
GAP_DAYS = 14
GAP_LOOKBACK_DAYS = 120   # 빈 구간은 끝이 마지막 관측일 기준 이 기간 안에 있는 것만 찾는다 (오래된 이력은 다시 훑지 않는다)


class Run:
    def __init__(self, conn: sqlite3.Connection, kind: str, origin: str) -> None:
        self.conn, self.origin = conn, origin
        self.id = conn.execute(
            "INSERT INTO ingest_run (origin,kind,started_at,status) VALUES (?,?,?,'running')",
            (origin, kind, now_kst())).lastrowid
        conn.commit()
        self.stats = {"ok": 0, "miss": 0, "error": 0}

    def record(self, source: str, key: str, status: str, rows: int = 0, message: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO ingest_result (run_id,source,series_key,status,rows,message) "
            "VALUES (?,?,?,?,?,?)", (self.id, source, key, status, rows, message))
        # 바로 커밋한다: 커밋하지 않은 채 다음 네트워크 호출(뉴스 단계의 텔레그램 등)로 넘어가면 그동안 DB 쓰기 잠금을 쥐어,
        # 이미 받은 일별·이벤트 단계가 저장하지 못하고 기다렸다 ('수집 중' 이 텔레그램만큼 길어짐, 2026-10-15 재현: 5초 → 30초)
        self.conn.commit()
        self.stats[status] = self.stats.get(status, 0) + 1

    def store(self, source: str, key: str, results: list[FetchResult], kind: str) -> None:
        """한 항목의 수집 결과를 저장하고 성공/없음/실패를 기록한다."""
        fetched = [r for r in results if isinstance(r, Fetched)]
        changed = 0
        captured = now_kst()
        for f in fetched:
            if db.upsert_observation(self.conn, f, kind, self.origin, captured) in {"insert", "update"}:
                changed += 1
        failed = [r for r in results if isinstance(r, Failed)]
        missing = [r for r in results if isinstance(r, Missing)]
        if fetched and failed:
            # 여러 날짜를 받는 소스(KRX·smbs)에서 일부 날짜만 실패: 받은 행은 저장하되 오류로 남겨 경고에 보이고 1시간 뒤 다시 받게 한다
            more = f" 외 {len(failed) - 1}건" if len(failed) > 1 else ""
            self.record(source, key, "error", changed, f"수신 {len(fetched)}행 · 일부 실패 {failed[0].message}{more}")
        elif fetched:
            self.record(source, key, "ok", changed, f"수신 {len(fetched)}행")
        elif failed:
            self.record(source, key, "error", 0, failed[0].message)
        else:
            self.record(source, key, "miss", 0, missing[0].reason if missing else "결과 없음")
        self.conn.commit()

    def finish(self) -> str:
        status = "ok" if not self.stats["error"] else ("failed" if not self.stats["ok"] else "partial")
        self.conn.execute("UPDATE ingest_run SET finished_at=?, status=? WHERE id=?", (now_kst(), status, self.id))
        self.conn.commit()
        return status


def abort_stale(conn: sqlite3.Connection) -> int:
    """앱이 시작될 때 'running' 으로 남은 수집 행(수집 중에 앱이 꺼진 흔적)을 'failed' 로 닫는다(스키마 CHECK 가 aborted 를 허용하지 않고 migrations 는 수정하지 않는다). 결과 행은 그대로 둔다."""
    cur = conn.execute("UPDATE ingest_run SET status='failed', finished_at=? WHERE status='running'", (now_kst(),))
    conn.commit()
    return cur.rowcount


def fetch_parallel(jobs: list[tuple[str, Callable]]) -> list:
    """[(source, fn)] 을 소스별 동시 호출 제한을 지키며 병렬로 실행해 같은 순서로 결과를 돌려준다.
    한 호출이 예외를 던져도 나머지는 계속되고, 그 자리에는 Failed 가 들어간다."""
    sems = {src: threading.Semaphore(n) for src, n in CONCURRENCY.items()}

    def call(source: str, fn: Callable):
        with sems.get(source, threading.Semaphore(1)):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - 경계에서 전부 잡는 것이 의도다
                return Failed(source, scrub(f"{type(exc).__name__}: {exc}"))

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(call, src, fn) for src, fn in jobs]
        return [f.result() for f in futures]


SOURCE_KINDS = ("daily", "events", "news")          # 소스별 결과를 남기는 수집 종류. 다음 회차가 이전 결과를 대체한다
APP_ERROR_KINDS = ("stage", "collect", "maintenance")   # app.py 가 단계 밖 예외를 남기는 종류. 성공 회차가 따로 없어 최근 것만 본다
APP_ERROR_WINDOW = timedelta(hours=6)


def latest_failures(conn: sqlite3.Connection) -> list[dict]:
    """종류별 가장 최근 수집의 실패 항목 + 최근 6시간 안의 앱 수준 오류 (화면 상태·리포트가 같은 기준을 쓴다)."""
    out: list[dict] = []
    for kind in SOURCE_KINDS:
        rid = conn.execute("SELECT MAX(id) FROM ingest_run WHERE kind=?", (kind,)).fetchone()[0]
        if rid:
            out += [dict(r) for r in conn.execute(
                "SELECT source, series_key, message FROM ingest_result WHERE run_id=? AND status='error' "
                "ORDER BY source, series_key", (rid,))]
    since = (datetime.now(KST) - APP_ERROR_WINDOW).isoformat(timespec="seconds")
    marks = ",".join("?" * len(APP_ERROR_KINDS))
    out += [dict(r) for r in conn.execute(
        f"SELECT r.source, r.series_key, r.message FROM ingest_result r JOIN ingest_run u ON u.id=r.run_id "
        f"WHERE u.kind IN ({marks}) AND u.started_at>=? AND r.status='error' ORDER BY r.run_id", (*APP_ERROR_KINDS, since))]
    return out


def _start_for(conn: sqlite3.Connection, key: str, provider: str, today: date) -> date:
    """이 항목·소스의 마지막 일별 관측일 - 겹침. 처음이면 소스별 과거 길이만큼 거슬러 간다.
    최근 GAP_LOOKBACK_DAYS 안에 GAP_DAYS 넘게 빈 구간이 있으면 그 앞부터 다시 받는다 — 앱이 오래 꺼져 있는 동안
    클라우드 병합이 최신 값만 먼저 채우면 '마지막 관측일 - 겹침' 이 그 빈 구간을 건너뛰어 영영 받지 않는다."""
    row = conn.execute("SELECT MAX(obs_date) FROM observation WHERE series_key=? AND kind='daily' AND provider=?",
                       (key, provider)).fetchone()
    if not (row and row[0]):
        return today - timedelta(days=365 * HISTORY_YEARS[provider])
    last = date.fromisoformat(row[0])
    start = last - timedelta(days=OVERLAP_DAYS)
    cutoff = (last - timedelta(days=GAP_LOOKBACK_DAYS)).isoformat()
    # 빈 구간은 출처와 무관하게 본다(KOSPI 처럼 KRX 가 야후 값을 대체한 날짜는 비어 있지 않다).
    # 기간 직전의 관측일도 넣는다: 앱을 이 기간보다 오래 꺼 두면 빈 구간의 시작이 기간 밖에 있다
    days = [date.fromisoformat(r[0]) for r in conn.execute(
        "SELECT obs_date FROM (SELECT MAX(obs_date) obs_date FROM observation WHERE series_key=? AND kind='daily' AND obs_date<?) "
        "WHERE obs_date IS NOT NULL UNION SELECT obs_date FROM observation WHERE series_key=? AND kind='daily' AND obs_date>=? "
        "ORDER BY obs_date", (key, cutoff, key, cutoff))]
    hole = next((a for a, b in zip(days, days[1:]) if (b - a).days > GAP_DAYS), None)     # 가장 오래된 빈 구간부터
    return min(start, hole) if hole else start


def run_daily(conn: sqlite3.Connection, today: date | None = None, origin: str = "local",
              only: set[str] | None = None, log: Callable[[str], None] = print,
              recent_days: int | None = None) -> str:
    """recent_days 가 있으면 DB 이력과 무관하게 오늘-recent_days 부터 받는다 (빈 DB 로 도는 클라우드 수집기용: 3년 백필 방지)."""
    today = today or today_kst()
    run = Run(conn, "daily", origin)

    def start_for(key: str, provider: str) -> date:
        if recent_days is not None:
            return today - timedelta(days=recent_days)
        return _start_for(conn, key, provider, today)

    def want(source: str) -> bool:
        return only is None or source in only

    # 1) 받을 것을 모은다 (시작일은 DB 를 보므로 이 스레드에서), 2) 병렬로 받는다, 3) 원래 순서대로 저장한다.
    jobs: list[tuple[str, Callable]] = []
    plan: list[tuple] = []          # (종류, 소스, 키, 시작일)
    if want("ecos"):
        for key in srcs.ECOS_SERIES:
            st = start_for(key, "ecos")
            jobs.append(("ecos", lambda key=key, st=st: ecos.fetch_range(key, st, today))); plan.append(("one", "ecos", key, st))
    if want("fred"):
        for key in srcs.FRED_SERIES:
            st = start_for(key, "fred")
            jobs.append(("fred", lambda key=key, st=st: fred.fetch_range(key, st, today))); plan.append(("one", "fred", key, st))
    if want("yahoo"):
        for key in srcs.YAHOO_SERIES:
            st = start_for(key, "yahoo")
            jobs.append(("yahoo", lambda key=key, st=st: yahoo.fetch_daily(key, st))); plan.append(("one", "yahoo", key, st))
    if want("krx"):
        st = start_for("ktbf_3y", "krx")
        jobs.append(("krx", lambda st=st: krx.fetch_range(st, today))); plan.append(("krx_fut", "krx", "ktbf", st))
        for key in srcs.KRX_INDEX_SERIES:
            st = start_for(key, "krx")
            jobs.append(("krx", lambda key=key, st=st: krx.fetch_index_range(key, st, today))); plan.append(("krx_idx", "krx", key, st))
    if want("smbs"):
        st = start_for("irs_3y", "smbs")
        jobs.append(("smbs", lambda st=st: smbs.fetch_range(st, today))); plan.append(("smbs", "smbs", "irs", st))

    for (kind, source, key, st), out in zip(plan, fetch_parallel(jobs)):
        if isinstance(out, Failed):               # 호출 자체가 예외로 끝난 경우
            run.store(source, key, [out], "daily")
        elif kind == "one":
            run.store(source, key, out, "daily")
        elif kind == "krx_fut":
            fetched, problems = out
            by_key: dict[str, list[FetchResult]] = {k: [] for k in srcs.KRX_SERIES}
            for f in fetched:
                by_key[f.series_key].append(f)
            # 날짜별 호출의 실패(problems)는 모든 항목에 함께 넘긴다 — 받은 행이 있어도 빠진 날짜가 있으면 오류로 보인다
            for k, results in by_key.items():
                run.store("krx", k, (results + problems) or [Missing(k, f"{st}~{today} 게시분 없음")], "daily")
        elif kind == "krx_idx":
            fetched, problems = out
            run.store("krx", key, (fetched + problems) or [Missing(key, f"{st}~{today} 게시분 없음")], "daily")
        else:                                      # smbs
            by_key = {}
            for r in out:
                if isinstance(r, Fetched):
                    by_key.setdefault(r.series_key, []).append(r)
            if not by_key:
                run.store("smbs", "irs", out, "daily")
            fails = [r for r in out if isinstance(r, Failed)]
            for k, rows in by_key.items():
                run.store("smbs", k, rows + fails, "daily")
    status = run.finish()
    log(f"daily run #{run.id} {status} · ok={run.stats['ok']} miss={run.stats['miss']} error={run.stats['error']}")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="marketview.ingest")
    parser.add_argument("command", choices=["daily"])
    parser.add_argument("--only", help="쉼표 구분 소스: ecos,fred,yahoo,krx,smbs")
    args = parser.parse_args(argv)
    conn = db.connect()
    db.migrate(conn)
    only = set(args.only.split(",")) if args.only else None
    run_daily(conn, only=only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
