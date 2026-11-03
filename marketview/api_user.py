"""사용자 데이터 API: 이벤트 · 포지션 · 수동 시세 CSV."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db, events, userdata
from .config.settings import canonical_date, today_kst

router = APIRouter(prefix="/api")


def _conn():
    return db.connect()


def _bad(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc.args[0]) if exc.args else "찾을 수 없습니다")
    return HTTPException(400, str(exc))


# --- 이벤트 -------------------------------------------------------------------
class EventIn(BaseModel):
    title: str | None = None
    region: str | None = None
    importance: int | None = None
    local_date: str | None = None
    local_time: str | None = None
    tz: str | None = None
    consensus: str | None = None
    memo: str | None = None
    source_url: str | None = None
    kind: str | None = None


@router.get("/events")
def list_events(base: str | None = None, days: int = 28) -> dict:
    try:
        b = date.fromisoformat(canonical_date(base)) if base else today_kst()
    except ValueError as exc:
        raise _bad(exc)
    conn = _conn()
    try:
        return {"base": b.isoformat(), "days": days, "events": events.list_events(conn, b, max(1, min(days, 120)))}
    finally:
        conn.close()


@router.post("/events")
def create_event(body: EventIn) -> dict:
    conn = _conn()
    try:
        return {"id": events.create_manual(conn, body.model_dump(exclude_none=False))}
    except ValueError as exc:
        raise _bad(exc)
    except (KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(400, "필수 입력(제목·날짜·진입 레벨 등)을 확인하세요") from exc
    finally:
        conn.close()


@router.put("/events/{event_id}")
def update_event(event_id: int, body: EventIn) -> dict:
    conn = _conn()
    try:
        events.update(conn, event_id, body.model_dump(exclude_unset=True))
        return {"saved": True}
    except (ValueError, LookupError, KeyError, TypeError, AttributeError) as exc:
        raise _bad(exc)
    finally:
        conn.close()


@router.delete("/events/{event_id}")
def delete_event(event_id: int) -> dict:
    conn = _conn()
    try:
        events.delete(conn, event_id)
        return {"deleted": True}
    except LookupError as exc:
        raise _bad(exc)
    finally:
        conn.close()


# --- 포지션 -------------------------------------------------------------------
class PositionIn(BaseModel):
    product: str
    series_key: str | None = None
    side: str
    entry_level: float | str | None = None
    entry_date: str
    target: float | str | None = None
    stop: float | str | None = None
    rationale: str | None = None
    exit_level: float | str | None = None
    exit_date: str | None = None


@router.get("/positions")
def positions(closed: bool = False) -> dict:
    conn = _conn()
    try:
        return {"positions": userdata.list_positions(conn, closed)}
    finally:
        conn.close()


@router.get("/trackables")
def trackables() -> dict:
    return {"items": userdata.trackables()}


@router.post("/positions")
def create_position(body: PositionIn) -> dict:
    conn = _conn()
    try:
        data = body.model_dump()
        return {"id": userdata.create_position(conn, data), "warnings": userdata.warnings_for(data)}
    except ValueError as exc:
        raise _bad(exc)
    except (KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(400, "필수 입력(제목·날짜·진입 레벨 등)을 확인하세요") from exc
    finally:
        conn.close()


@router.put("/positions/{pid}")
def update_position(pid: int, body: PositionIn) -> dict:
    conn = _conn()
    try:
        data = body.model_dump()
        userdata.update_position(conn, pid, data)
        return {"saved": True, "warnings": userdata.warnings_for(data)}
    except (ValueError, LookupError, KeyError, TypeError, AttributeError) as exc:
        raise _bad(exc)
    finally:
        conn.close()


@router.delete("/positions/{pid}")
def delete_position(pid: int) -> dict:
    conn = _conn()
    try:
        userdata.delete_position(conn, pid)
        return {"deleted": True}
    except LookupError as exc:
        raise _bad(exc)
    finally:
        conn.close()


# --- 수동 시세 CSV -------------------------------------------------------------
class CsvIn(BaseModel):
    text: str


@router.post("/observations/csv")
def import_csv(body: CsvIn) -> dict:
    conn = _conn()
    try:
        return userdata.import_manual_csv(conn, body.text)
    except ValueError as exc:
        raise _bad(exc)
    finally:
        conn.close()
