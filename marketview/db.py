"""SQLite 접속, 마이그레이션, 관측치 저장.

관측치는 (series_key, obs_date, kind, obs_ts) 가 키다. 같은 값은 건드리지 않고(멱등),
다른 값이 오면 갱신하되 이전 값을 observation_log 에 남긴다. 수동 입력은 자동 수집이 덮어쓰지 않는다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .collect.base import Fetched
from .config.series import SERIES
from .config.settings import MIGRATIONS_DIR, db_path, now_kst


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """migrations/NNN_*.sql 을 번호순으로 한 번씩 적용한다. 적용한 파일명을 돌려준다."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version "
                 "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    done = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
    applied: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in done:
            continue
        # 파일 하나를 한 트랜잭션으로: 중간 문장이 실패하면 앞 문장까지 되돌린다. 그냥 executescript 하면 문장마다 자동 커밋되어
        # 절반만 적용된 채 schema_version 이 비고, 다음 기동 때 '이미 있음' 오류로 앱이 뜨지 않는다.
        try:
            conn.executescript("BEGIN;\n" + path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_version VALUES (?, ?)", (path.name, now_kst()))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        applied.append(path.name)
    sync_series_defs(conn)
    return applied


def sync_series_defs(conn: sqlite3.Connection) -> None:
    """config/series.py 를 series_def 에 반영한다 (표시 순서 = 정의 순서)."""
    for order, s in enumerate(SERIES):
        conn.execute(
            """INSERT INTO series_def (series_key,label_ko,tab,group_key,tenor_months,unit,decimals,
                                       delta_unit,provider,status,note,display_order)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(series_key) DO UPDATE SET label_ko=excluded.label_ko, tab=excluded.tab,
                 group_key=excluded.group_key, tenor_months=excluded.tenor_months, unit=excluded.unit,
                 decimals=excluded.decimals, delta_unit=excluded.delta_unit, provider=excluded.provider,
                 status=excluded.status, note=excluded.note, display_order=excluded.display_order""",
            (s.key, s.label, s.tab, s.group, s.tenor_months, s.unit, s.decimals,
             s.delta_unit, s.provider, s.status, s.note, order),
        )
    conn.commit()


def upsert_observation(conn: sqlite3.Connection, f: Fetched, kind: str, origin: str,
                       captured_at: str | None = None) -> str:
    """'insert' | 'update' | 'same' | 'skipped'(수동 값 보호) 를 돌려준다. 커밋은 호출자가 한다."""
    captured_at = captured_at or now_kst()
    obs_date = f.obs_date.isoformat()
    obs_ts = f.obs_ts if kind == "intraday" else ""
    row = conn.execute(
        "SELECT value, provider, origin FROM observation "
        "WHERE series_key=? AND obs_date=? AND kind=? AND obs_ts=?",
        (f.series_key, obs_date, kind, obs_ts),
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO observation VALUES (?,?,?,?,?,?,?,?,?)",
            (f.series_key, obs_date, kind, obs_ts, f.value, f.provider, origin, captured_at, f.note),
        )
        _log(conn, f, kind, obs_ts, "insert", None, None)
        return "insert"
    if row["origin"] == "manual" and origin != "manual":
        return "skipped"
    if kind == "daily" and row["provider"] == "krx" and f.provider == "yahoo":
        return "skipped"          # 공식(KRX) 종가를 비공식(Yahoo)이 되돌리지 않는다. 반대(Yahoo → KRX)는 정상 갱신
    if abs(row["value"] - f.value) < 1e-12 and not (row["provider"] == "yahoo" and f.provider == "krx"):
        return "same"             # 값이 같아도 야후 → KRX 는 출처 표기를 바꾸려고 갱신한다
    conn.execute(
        "UPDATE observation SET value=?, provider=?, origin=?, captured_at=?, note=? "
        "WHERE series_key=? AND obs_date=? AND kind=? AND obs_ts=?",
        (f.value, f.provider, origin, captured_at, f.note, f.series_key, obs_date, kind, obs_ts),
    )
    _log(conn, f, kind, obs_ts, "update", row["value"], row["provider"])
    return "update"


def _log(conn: sqlite3.Connection, f: Fetched, kind: str, obs_ts: str, action: str,
         prev_value: float | None, prev_provider: str | None) -> None:
    conn.execute(
        "INSERT INTO observation_log (series_key,obs_date,kind,obs_ts,action,prev_value,new_value,"
        "prev_provider,new_provider,written_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f.series_key, f.obs_date.isoformat(), kind, obs_ts, action, prev_value, f.value,
         prev_provider, f.provider, now_kst()),
    )
