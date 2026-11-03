"""사용자가 직접 쓰는 데이터: 포지션, 수동 시세(CSV). 변경은 모두 *_log 에 남는다 (append-only).

뷰 메모 기능은 2026-10-10 에 화면·API·리포트에서 뺐다. view_note / view_note_log 테이블과 기존 행은 그대로 둔다(이력 보존)."""

from __future__ import annotations

import csv
import io
import json
import math
import sqlite3
from datetime import date

from . import db, derived, queries
from .collect.base import Fetched
from .config.series import BY_KEY
from .config.settings import canonical_date, now_kst, today_kst

# --- 포지션 -----------------------------------------------------------------
def trackables() -> list[dict]:
    """포지션에 연결할 수 있는 항목 (수집되는 시세·파생값)."""
    out = []
    for key, s in BY_KEY.items():
        if s.provider == "manual" or s.unit == "contracts":
            continue
        m = queries.meta_of(key)
        out.append({"key": key, "label": m["label"], "unit": m["unit"], "decimals": m["decimals"]})
    for key in derived.REGISTRY:
        m = queries.meta_of(key)
        out.append({"key": key, "label": m["label"], "unit": m["unit"], "decimals": m["decimals"]})
    return out


def profit_sign(side: str, unit: str | None) -> int:
    """레벨이 오를 때 이익이면 +1, 내릴 때 이익이면 -1.
    금리(%)는 채권 관행: 롱 = 채권 롱 = 금리 하락 시 이익 (2026-10-15 사용자 결정. 국고 10년 '롱'에 목표를 진입보다 낮게 적었는데
    레벨 기준이라 금리가 오르자 이익으로 보였다). 스프레드(bp)·가격·지수·환율은 레벨 기준: 롱 = 레벨 상승 시 이익."""
    sign = 1 if side == "long" else -1
    return -sign if unit == "pct" else sign


def side_hint(side: str, unit: str | None) -> str:
    """'롱 — 금리 하락 시 이익' 같은 방향 설명 (화면 폼·경고 문구)."""
    up = profit_sign(side, unit) > 0
    what = "금리" if unit == "pct" else "레벨"
    return f"{'롱' if side == 'long' else '숏'} — {what} {'상승' if up else '하락'} 시 이익"


def pnl(side: str, entry: float, level: float, unit: str) -> tuple[float, str]:
    """손익 (방향은 profit_sign). 금리(%)는 bp, 스프레드(bp)는 bp, 원화 환율은 원, 지수·가격은 포인트."""
    diff = (level - entry) * profit_sign(side, unit)
    if unit == "pct":
        return diff * 100, "bp"
    if unit == "bp":
        return diff, "bp"
    if unit == "krw":
        return diff, "원"
    return diff, "pt"


def _snapshot(conn: sqlite3.Connection, pid: int) -> str:
    row = conn.execute("SELECT * FROM position WHERE id=?", (pid,)).fetchone()
    return json.dumps({k: row[k] for k in row.keys()}, ensure_ascii=False)


def _plog(conn: sqlite3.Connection, pid: int, action: str) -> None:
    conn.execute("INSERT INTO position_log (position_id,action,snapshot,logged_at) VALUES (?,?,?,?)",
                 (pid, action, _snapshot(conn, pid), now_kst()))


def _num(v, name: str, required: bool = False) -> float | None:
    if v in (None, ""):
        if required:
            raise ValueError(f"{name} 값을 입력하세요")
        return None
    x = float(v)
    if not math.isfinite(x):
        raise ValueError(f"{name} 값이 숫자가 아닙니다")
    return x


def _clean(f: dict) -> dict:
    product = (f.get("product") or "").strip()
    if not product:
        raise ValueError("상품을 입력하세요")
    key = f.get("series_key") or None
    if key and key not in BY_KEY and key not in derived.REGISTRY:
        raise ValueError("연결할 수 없는 항목입니다")
    if f.get("side") not in ("long", "short"):
        raise ValueError("방향은 long / short")
    entry_date = canonical_date(f["entry_date"])
    exit_date = canonical_date(f["exit_date"]) if f.get("exit_date") else None
    exit_level = _num(f.get("exit_level"), "청산 레벨")
    if exit_date and exit_date < entry_date:
        raise ValueError("청산일이 진입일보다 빠릅니다")
    if (exit_date is None) != (exit_level is None):
        raise ValueError("청산하려면 청산 레벨과 청산일을 둘 다 입력하세요")
    return {"product": product, "series_key": key, "side": f["side"], "entry_level": _num(f.get("entry_level"), "진입 레벨", True),
            "entry_date": entry_date, "target": _num(f.get("target"), "목표"), "stop": _num(f.get("stop"), "손절"),
            "rationale": (f.get("rationale") or "").strip() or None, "exit_level": exit_level, "exit_date": exit_date,
            "status": "closed" if exit_level is not None else "open"}


def warnings_for(f: dict) -> list[str]:
    """저장은 되지만 확인이 필요한 입력. 목표는 이익 쪽, 손절은 손실 쪽이어야 한다 (방향은 profit_sign: 금리 롱은 금리 하락이 이익).
    (이익을 지키려 손절을 진입 쪽으로 올리는 트레일링도 있어 막지는 않는다)"""
    c = _clean(f)
    unit = queries.meta_of(c["series_key"])["unit"] if c["series_key"] else None
    sign = profit_sign(c["side"], unit)
    hint = side_hint(c["side"], unit)
    out = []
    if c["target"] is not None and (c["target"] - c["entry_level"]) * sign <= 0:
        out.append(f"목표가 이익 방향이 아닙니다 ({hint}: 진입보다 {'높아야' if sign > 0 else '낮아야'} 함)")
    if c["stop"] is not None and (c["stop"] - c["entry_level"]) * sign >= 0:
        out.append(f"손절이 손실 방향이 아닙니다 ({hint}: 진입보다 {'낮아야' if sign > 0 else '높아야'} 함)")
    return out


def create_position(conn: sqlite3.Connection, f: dict) -> int:
    c, now = _clean(f), now_kst()
    cur = conn.execute(
        "INSERT INTO position (product,series_key,side,entry_level,entry_date,target,stop,rationale,status,exit_level,exit_date,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (c["product"], c["series_key"], c["side"], c["entry_level"], c["entry_date"], c["target"], c["stop"], c["rationale"],
         c["status"], c["exit_level"], c["exit_date"], now, now))
    _plog(conn, cur.lastrowid, "insert")
    conn.commit()
    return cur.lastrowid


def update_position(conn: sqlite3.Connection, pid: int, f: dict) -> None:
    if conn.execute("SELECT 1 FROM position WHERE id=? AND deleted=0", (pid,)).fetchone() is None:
        raise LookupError("포지션을 찾을 수 없습니다")
    c = _clean(f)
    was_open = conn.execute("SELECT status FROM position WHERE id=?", (pid,)).fetchone()["status"] == "open"
    conn.execute("UPDATE position SET product=?,series_key=?,side=?,entry_level=?,entry_date=?,target=?,stop=?,rationale=?,status=?,"
                 "exit_level=?,exit_date=?,updated_at=? WHERE id=?",
                 (c["product"], c["series_key"], c["side"], c["entry_level"], c["entry_date"], c["target"], c["stop"], c["rationale"],
                  c["status"], c["exit_level"], c["exit_date"], now_kst(), pid))
    _plog(conn, pid, "close" if (was_open and c["status"] == "closed") else "update")
    conn.commit()


def delete_position(conn: sqlite3.Connection, pid: int) -> None:
    if conn.execute("SELECT 1 FROM position WHERE id=? AND deleted=0", (pid,)).fetchone() is None:
        raise LookupError("포지션을 찾을 수 없습니다")
    conn.execute("UPDATE position SET deleted=1, updated_at=? WHERE id=?", (now_kst(), pid))
    _plog(conn, pid, "delete")
    conn.commit()


def list_positions(conn: sqlite3.Connection, include_closed: bool = False, asof: str | None = None) -> list[dict]:
    """asof 가 있으면 그 날짜 시점의 포지션: 진입일이 asof 이후인 것은 빼고, asof 이후에 청산된 것은 열린 것으로 보며,
    현재값·손익은 asof 까지의 일별 종가로 계산한다 (사후에 만드는 과거 날짜 리포트용)."""
    sql = "SELECT * FROM position WHERE deleted=0 ORDER BY status, entry_date DESC, id DESC"
    out = []
    closes = None
    for r in conn.execute(sql).fetchall():
        closed = r["status"] == "closed" and (asof is None or (r["exit_date"] or "") <= asof)
        if asof and r["entry_date"] > asof:
            continue
        if closed and not include_closed:
            continue
        d = {k: r[k] for k in r.keys()}
        if asof:
            d["status"] = "closed" if closed else "open"
        meta = queries.meta_of(r["series_key"]) if r["series_key"] else None
        d["unit"], d["decimals"] = (meta["unit"], meta["decimals"]) if meta else (None, 2)
        d["side_hint"] = side_hint(r["side"], d["unit"])
        d["current"], d["current_obs"], d["pnl"], d["pnl_unit"], d["pnl_kind"] = None, None, None, None, None
        if closed and meta:
            d["pnl"], d["pnl_unit"] = pnl(r["side"], r["entry_level"], r["exit_level"], meta["unit"])
            d["pnl_kind"] = "realized"
        elif meta:
            if asof is None and closes is None:
                closes = queries.common_close_dates(conn)      # 화면은 마감 확정값 기준: 현재값도 권역 마감일까지
            st = queries.tile_state(conn, r["series_key"], queries.asof_for(r["series_key"], asof, closes))
            if st:
                d["current"], d["current_obs"] = st["value"], st["obs_date"]
                d["pnl"], d["pnl_unit"] = pnl(r["side"], r["entry_level"], st["value"], meta["unit"])
                d["pnl_kind"] = "open"
        out.append(d)
    return out


# --- 수동 시세 CSV ------------------------------------------------------------
def import_manual_csv(conn: sqlite3.Connection, text: str) -> dict:
    """붙여넣은 CSV 를 수동 항목(소스 없음)에 저장한다. 한 줄이라도 잘못되면 아무것도 저장하지 않는다.

    형식 1 (긴 형식)  날짜,항목키,값           예) 2026-10-08,pubco_aaa_3y,3.52
    형식 2 (넓은 형식) date,항목키1,항목키2,...  예) 첫 줄이 'date,pubco_aaa_3y,bank_aaa_3y' 이고 아래는 날짜,값,값
    """
    body = text.strip()
    # 엑셀·웹 표에서 복사하면 탭 구분이고 값에 천 단위 쉼표가 들어 있다(1,385.20). 탭을 쉼표로 바꾸면 값이 쪼개져 1 만 저장되므로 구분자를 그대로 쓴다.
    rows = [r for r in csv.reader(io.StringIO(body), delimiter="\t" if "\t" in body else ",") if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("붙여넣은 내용이 없습니다")
    triples: list[tuple[str, str, float]] = []
    errors: list[str] = []
    head = [c.strip().lower() for c in rows[0]]
    wide = head[0] in ("date", "날짜") and len(head) > 1
    keys = [c.strip() for c in rows[0][1:]] if wide else []
    today = today_kst()
    for i, r in enumerate(rows[1:] if wide else rows, start=2 if wide else 1):
        try:
            cells = [c.strip() for c in r]
            day = canonical_date(cells[0].replace("/", "-").replace(".", "-"))
            if date.fromisoformat(day) > today:
                raise ValueError("미래 날짜")
            if any(cells[(1 + len(keys)) if wide else 3:]):      # 남는 칸이 있으면 값이 쪼개진 것이다 — 조용히 버리지 않고 거부한다
                raise ValueError("칸이 너무 많습니다 (값에 천 단위 쉼표가 있으면 따옴표로 감싸거나 쉼표를 빼세요)")
            pairs = list(zip(keys, cells[1:])) if wide else [(cells[1], cells[2])]
            for key, raw in pairs:
                if raw == "":
                    continue                                   # 빈 칸은 '미수집' — 0 으로 채우지 않는다
                spec = BY_KEY.get(key)
                if spec is None or spec.provider != "manual":
                    raise ValueError(f"수동 입력 항목이 아닙니다: {key}")
                try:
                    v = float(raw.replace(",", ""))
                except ValueError:
                    raise ValueError(f"숫자가 아닙니다: {raw}") from None
                if not math.isfinite(v):
                    raise ValueError(f"숫자가 아닙니다: {raw}")
                triples.append((day, key, v))
        except IndexError:
            errors.append(f"{i}행: 열이 모자랍니다 (날짜,항목키,값)")
        except ValueError as exc:
            errors.append(f"{i}행: {exc}")
    if errors:
        raise ValueError(" / ".join(errors[:6]) + (f" 외 {len(errors) - 6}건" if len(errors) > 6 else ""))
    if not triples:
        raise ValueError("저장할 값이 없습니다")
    saved = {"insert": 0, "update": 0, "same": 0}
    captured = now_kst()
    for day, key, v in triples:
        y, m, d = map(int, day.split("-"))
        saved[db.upsert_observation(conn, Fetched(key, v, date(y, m, d), "user", note="csv"), "daily", "manual", captured)] += 1
    conn.commit()
    return {"rows": len(triples), **saved}
