"""이벤트: 현지 시각 → KST 변환(서머타임·날짜 넘김), 공식 일정·FRED 동기화, 수정 이력.

변경은 모두 event_log 에 '변경 후 전체 상태'로 남는다 (append-only).
사용자가 지운 이벤트(status='deleted')는 동기화가 되살리지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .collect.base import get_json, scrub
from .config import events as cfg
from .config.settings import KST, env, now_kst

EDITABLE_ON_SYNCED = {"consensus", "memo", "importance"}   # 공식·FRED 항목에서 사용자가 고칠 수 있는 칸
REGION_TZ = {"KR": "Asia/Seoul", "US": "America/New_York"}
WEEKDAY = "월화수목금토일"


def kst_view(local_date: str, local_time: str | None, tz: str, base: date) -> dict:
    """현지 날짜·시각을 KST 로 바꾼다. 시각이 없으면 날짜만 (추측하지 않는다)."""
    d = date.fromisoformat(local_date)
    if local_time:
        local = datetime.fromisoformat(f"{local_date}T{local_time}").replace(tzinfo=ZoneInfo(tz))
        k = local.astimezone(KST)
        kst_date, kst_time = k.date(), k.strftime("%H:%M")
    else:
        # 시각 미확인: 현지 날짜를 그대로 쓴다 (KR 항목은 그대로 KST 날짜)
        kst_date, kst_time = d, None
    return {"kst_date": kst_date.isoformat(), "kst_time": kst_time, "weekday": WEEKDAY[kst_date.weekday()],
            "d_day": (kst_date - base).days}


def _row_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def _log(conn: sqlite3.Connection, event_id: int, action: str) -> None:
    row = conn.execute("SELECT * FROM event WHERE id=?", (event_id,)).fetchone()
    conn.execute("INSERT INTO event_log (event_id, action, snapshot, logged_at) VALUES (?,?,?,?)",
                 (event_id, action, json.dumps(_row_dict(row), ensure_ascii=False), now_kst()))


def view(row: sqlite3.Row, base: date) -> dict:
    d = _row_dict(row)
    d.update(kst_view(d["local_date"], d["local_time"], d["tz"], base))
    d["time_known"] = d["local_time"] is not None
    d["editable_all"] = d["origin"] == "manual"
    return d


def list_events(conn: sqlite3.Connection, base: date, days: int = 28) -> list[dict]:
    """base 부터 days 일 안의 (KST 날짜 기준) 활성 이벤트. KST 로 날짜가 바뀌는 항목도 맞게 걸러진다."""
    lo = (base - timedelta(days=2)).isoformat()          # 현지 날짜와 KST 날짜는 최대 하루 차이
    hi = (base + timedelta(days=days + 2)).isoformat()
    rows = conn.execute("SELECT * FROM event WHERE status='active' AND local_date BETWEEN ? AND ?", (lo, hi)).fetchall()
    out = [view(r, base) for r in rows]
    out = [e for e in out if 0 <= e["d_day"] <= days]
    out.sort(key=lambda e: (e["kst_date"], e["kst_time"] or "", -e["importance"]))
    return out


def _validate(fields: dict) -> dict:
    f = dict(fields)
    f["title"] = (f.get("title") or "").strip()
    if not f["title"]:
        raise ValueError("제목을 입력하세요")
    if f.get("region") not in ("KR", "US", "OTHER"):
        raise ValueError("지역은 KR / US / OTHER")
    date.fromisoformat(f["local_date"])
    if f.get("local_date") != date.fromisoformat(f["local_date"]).isoformat():
        raise ValueError("날짜는 YYYY-MM-DD")
    t = f.get("local_time") or None
    if t:
        datetime.strptime(t, "%H:%M")
    f["local_time"] = t
    f["tz"] = f.get("tz") or REGION_TZ.get(f["region"], "Asia/Seoul")
    ZoneInfo(f["tz"])
    imp = int(f.get("importance") or 2)
    if imp not in (1, 2, 3):
        raise ValueError("중요도는 1~3")
    f["importance"] = imp
    return f


def create_manual(conn: sqlite3.Connection, fields: dict) -> int:
    f = _validate(fields)
    now = now_kst()
    cur = conn.execute(
        "INSERT INTO event (ext_key,kind,title,region,importance,local_date,local_time,tz,consensus,memo,source_url,verified_on,"
        "origin,status,created_at,updated_at) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,'manual','active',?,?)",
        (f.get("kind") or "other", f["title"], f["region"], f["importance"], f["local_date"], f["local_time"], f["tz"],
         f.get("consensus") or None, f.get("memo") or None, f.get("source_url") or None, f.get("verified_on") or None, now, now))
    _log(conn, cur.lastrowid, "insert")
    conn.commit()
    return cur.lastrowid


def update(conn: sqlite3.Connection, event_id: int, fields: dict) -> None:
    row = conn.execute("SELECT * FROM event WHERE id=? AND status!='deleted'", (event_id,)).fetchone()
    if row is None:
        raise LookupError("이벤트를 찾을 수 없습니다")
    if row["origin"] != "manual":
        blocked = set(fields) - EDITABLE_ON_SYNCED - {"id"}
        changed_blocked = {k for k in blocked if fields[k] != row[k] and k in row.keys()}
        if changed_blocked:
            raise ValueError("공식 일정의 날짜·시각·제목은 고칠 수 없습니다 (컨센서스·메모·중요도만 수정). 틀렸다면 삭제 후 직접 추가하세요")
        merged = {k: row[k] for k in row.keys()}
        merged.update({k: fields[k] for k in EDITABLE_ON_SYNCED if k in fields})
    else:
        merged = {k: row[k] for k in row.keys()}
        merged.update({k: v for k, v in fields.items() if v is not None or k not in ("importance", "kind")})   # None 이 중요도·종류를 기본값으로 되돌리지 않는다
        merged = {**merged, **_validate(merged)}
    conn.execute("UPDATE event SET kind=?, title=?, region=?, importance=?, local_date=?, local_time=?, tz=?, consensus=?, memo=?, "
                 "source_url=?, updated_at=? WHERE id=?",
                 (merged.get("kind") or "other", merged["title"], merged["region"], int(merged["importance"]), merged["local_date"], merged["local_time"],
                  merged["tz"], merged.get("consensus") or None, merged.get("memo") or None, merged.get("source_url") or None,
                  now_kst(), event_id))
    _log(conn, event_id, "update")
    conn.commit()


def delete(conn: sqlite3.Connection, event_id: int) -> None:
    row = conn.execute("SELECT 1 FROM event WHERE id=? AND status!='deleted'", (event_id,)).fetchone()
    if row is None:
        raise LookupError("이벤트를 찾을 수 없습니다")
    conn.execute("UPDATE event SET status='deleted', updated_at=? WHERE id=?", (now_kst(), event_id))
    _log(conn, event_id, "delete")
    conn.commit()


# --- 동기화 ------------------------------------------------------------------
_SOURCE_FIELDS = ("kind", "title", "region", "local_date", "local_time", "tz", "source_url", "verified_on")


def _upsert_synced(conn: sqlite3.Connection, ext_key: str, origin: str, f: dict) -> str:
    """출처가 주는 칸만 갱신하고 사용자가 쓴 컨센서스·메모·중요도는 건드리지 않는다. 'insert'|'update'|'same'|'skipped'."""
    now = now_kst()
    row = conn.execute("SELECT * FROM event WHERE ext_key=?", (ext_key,)).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO event (ext_key,kind,title,region,importance,local_date,local_time,tz,consensus,memo,source_url,verified_on,"
            "origin,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?,?,'active',?,?)",
            (ext_key, f["kind"], f["title"], f["region"], f["importance"], f["local_date"], f["local_time"], f["tz"],
             f.get("memo"), f["source_url"], f["verified_on"], origin, now, now))
        _log(conn, cur.lastrowid, "insert")
        return "insert"
    if row["status"] == "deleted":
        return "skipped"                                   # 사용자가 지운 것은 되살리지 않는다
    # verified_on(확인 날짜)만 바뀐 것은 변경으로 보지 않는다: FRED 동기화가 매일 확인 날짜만 바꿔 event_log 에 하루 11행씩
    # 내용 없는 'update' 가 쌓이고 이벤트 패널이 다시 그려졌다. 확인 날짜는 내용이 실제로 바뀐 날로 남는다.
    changed = any(row[k] != f[k] for k in _SOURCE_FIELDS if k != "verified_on")
    revive = row["status"] == "stale"
    if not changed and not revive:
        return "same"
    conn.execute("UPDATE event SET kind=?, title=?, region=?, local_date=?, local_time=?, tz=?, source_url=?, verified_on=?, "
                 "status='active', updated_at=? WHERE id=?",
                 (*[f[k] for k in _SOURCE_FIELDS], now, row["id"]))
    _log(conn, row["id"], "revive" if revive and not changed else "update")
    return "update"


def sync_official(conn: sqlite3.Connection) -> int:
    n = 0
    for e in cfg.official_events():
        res = _upsert_synced(conn, e.ext_key, "official", {
            "kind": e.kind, "title": e.title, "region": e.region, "importance": e.importance, "local_date": e.local_date,
            "local_time": e.local_time, "tz": e.tz, "source_url": e.source_url, "verified_on": e.verified_on, "memo": e.note or None})
        n += res in ("insert", "update")
    conn.commit()
    return n


def fetch_fred_dates(release_id: int, start: date, end: date) -> list[str]:
    key = env("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY 없음")
    data = get_json("https://api.stlouisfed.org/fred/release/dates", params={
        "api_key": key, "file_type": "json", "release_id": release_id,
        "realtime_start": "2026-01-01", "realtime_end": "9999-12-31",
        "include_release_dates_with_no_data": "true", "sort_order": "asc", "limit": 500})
    return sorted({x["date"] for x in data.get("release_dates", []) if start.isoformat() <= x["date"] <= end.isoformat()})


def sync_fred(conn: sqlite3.Connection, today: date, fetch=fetch_fred_dates) -> dict[int, str]:
    """FRED 발표일로 미국 지표 이벤트를 맞춘다. 출처 목록에서 사라진 미래 항목은 stale 로 표시한다. 릴리스별 결과 문구를 돌려준다."""
    start, end = today - timedelta(days=1), today + timedelta(days=cfg.FRED_WINDOW_DAYS)
    result: dict[int, str] = {}
    for rel in cfg.FRED_RELEASES:
        try:
            dates = fetch(rel.release_id, start, end)
        except Exception as exc:  # noqa: BLE001 - 한 릴리스가 실패해도 나머지는 계속
            result[rel.release_id] = "error: " + scrub(str(exc))
            continue
        keep = set()
        for d in dates:
            ext = f"fred:{rel.release_id}:{d}"
            keep.add(ext)
            _upsert_synced(conn, ext, "fred", {
                "kind": rel.kind, "title": rel.title, "region": "US", "importance": rel.importance, "local_date": d,
                "local_time": rel.local_time, "tz": "America/New_York", "source_url": rel.source_url,
                "verified_on": today.isoformat(), "memo": None})
        # 창 안의 이 릴리스 항목 중 FRED 가 더 이상 말하지 않는 것
        for row in conn.execute("SELECT id, ext_key FROM event WHERE ext_key LIKE ? AND status='active' AND local_date BETWEEN ? AND ?",
                                (f"fred:{rel.release_id}:%", start.isoformat(), end.isoformat())).fetchall():
            if row["ext_key"] not in keep:
                conn.execute("UPDATE event SET status='stale', updated_at=? WHERE id=?", (now_kst(), row["id"]))
                _log(conn, row["id"], "stale")
        conn.commit()          # 릴리스마다 커밋: 다음 릴리스를 받는 동안 쓰기 잠금을 쥐고 있으면 일별 수집 저장이 기다린다
        result[rel.release_id] = f"ok: {len(dates)}건"
    return result


def sync_all(conn: sqlite3.Connection, today: date) -> dict:
    out = {"official": sync_official(conn)}
    out["fred"] = sync_fred(conn, today)
    return out
