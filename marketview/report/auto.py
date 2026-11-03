"""리포트 자동 생성과 꺼져 있던 날 따라잡기 (SPEC §9).

앱이 켜져 있는 동안 주기적으로(수집 사이클마다) `run_due` 가 불린다.

  - 오늘 리포트: 평일, KST AUTO_TIME 이후, 아직 오늘 리포트가 없으면 만든다 (origin='auto').
  - 꺼져 있던 날: 기능을 켠 날(`report_auto_since`) 이후의 평일 중 리포트가 없는 날을 날짜별로 만든다
    (origin='catchup'). 한 번에 최대 MAX_CATCHUP 일, 가장 최근 것부터. 값은 그 날짜까지의 관측만 쓴다.
  - 어떤 날짜든 report_run 행이 하나라도 있으면(수동 생성 포함) 이미 있는 것으로 보고 다시 만들지 않는다.
  - 한 날짜가 3번 연속 실패하면(예외) 그 날짜는 포기하고 사유를 남긴다. 수집·화면은 이와 무관하게 계속된다.
  - 평일만 만든다. 한국 공휴일 달력은 두지 않았다(휴장일 리포트는 값이 그대로일 뿐 해롭지 않다).
설정: app_setting.report_auto ('0' 이면 끔, 기본 켬).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta

from ..collect.base import scrub
from ..config.settings import KST, now_kst
from . import run

AUTO_TIME = time(7, 30)      # KST. 이 시각 전에는 오늘 리포트를 만들지 않는다 (아침 수집이 끝난 뒤의 뉴스를 담기 위해)
MAX_CATCHUP = 7
MAX_FAILS = 3


def enabled(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM app_setting WHERE key='report_auto'").fetchone()
    return row is None or row["value"] != "0"


def set_enabled(conn: sqlite3.Connection, on: bool, today: date | None = None) -> None:
    """켜면 그 날부터 센다 — 꺼져 있던 동안(기능을 끈 동안)의 날들을 몰아서 만들지 않는다."""
    conn.execute("INSERT INTO app_setting (key,value) VALUES ('report_auto',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if on else "0",))
    if on:
        _set_since(conn, (today or datetime.now(KST).date()).isoformat())
    conn.commit()


def _state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _put_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO sync_state (key,value,updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, value, now_kst()))


def _set_since(conn: sqlite3.Connection, day: str) -> None:
    _put_state(conn, "report_auto_since", day)


def due_days(conn: sqlite3.Connection, now: datetime | None = None) -> list[tuple[str, str]]:
    """만들어야 할 [(날짜, origin)] — 오래된 날짜부터. 부수효과로 기준일(since)을 처음 한 번 기록한다."""
    now = (now or datetime.now(KST)).astimezone(KST)
    today = now.date()
    since = _state(conn, "report_auto_since")
    if since is None:                                   # 이 기능을 처음 평가하는 날부터 센다
        _set_since(conn, today.isoformat())
        conn.commit()
        since = today.isoformat()
    have = {r[0] for r in conn.execute("SELECT DISTINCT report_date FROM report_run")}
    fails = conn.execute("SELECT key, value FROM sync_state WHERE key LIKE 'report_auto_fail:%'").fetchall()
    gave_up = {r["key"].split(":", 1)[1] for r in fails if int(r["value"]) >= MAX_FAILS}
    out: list[tuple[str, str]] = []
    d = date.fromisoformat(since)
    while d <= today:
        iso = d.isoformat()
        if d.weekday() < 5 and iso not in have and iso not in gave_up:
            if d < today:
                out.append((iso, "catchup"))
            elif now.time() >= AUTO_TIME:
                out.append((iso, "auto"))
        d += timedelta(days=1)
    past = [x for x in out if x[1] == "catchup"][-MAX_CATCHUP:]       # 가장 최근 MAX_CATCHUP 일만
    return past + [x for x in out if x[1] == "auto"]


def run_due(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    """대상 날짜의 리포트를 순서대로 만든다. 다른 생성(수동 버튼 포함)이 진행 중이면 이번 주기는 건너뛴다."""
    result: dict = {"at": now_kst(), "generated": [], "errors": []}
    if not enabled(conn):
        result["state"] = "off"
        return result
    days = due_days(conn, now)
    if not days:
        result["state"] = "idle"
        return result
    if not run.generate_lock.acquire(blocking=False):
        result["state"] = "busy"
        return result
    try:
        for day, origin in days:
            key = f"report_auto_fail:{day}"
            try:
                row = run.generate(conn, day, origin=origin)
                result["generated"].append({"date": day, "origin": origin, "version": row["version"],
                                            "pdf": row["pdf_status"], "drive": row["drive_status"]})
            except Exception as exc:  # noqa: BLE001 - 한 날짜의 실패가 다음 날짜와 수집·화면을 막지 않는다
                n = int(_state(conn, key) or 0) + 1
                _put_state(conn, key, str(n))
                conn.commit()
                result["errors"].append({"date": day, "attempt": n, "message": scrub(f"{type(exc).__name__}: {exc}")[:160]})
    finally:
        run.generate_lock.release()
    result["state"] = "ok" if not result["errors"] else "error"
    _put_state(conn, "report_auto", json.dumps(result, ensure_ascii=False))
    conn.commit()
    return result


def last_result(conn: sqlite3.Connection) -> dict | None:
    raw = _state(conn, "report_auto")
    return json.loads(raw) if raw else None
