"""뉴스·텔레그램 API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db, news
from .collect import telegram
from .config import news as cfg
from .config import telegram as tg_cfg
from .config.settings import canonical_date

router = APIRouter(prefix="/api")


def telegram_state() -> dict:
    """네트워크 없이 확인할 수 있는 범위의 연결 상태. 화면의 '텔레그램 연결' 안내에 쓴다."""
    if not tg_cfg.CHANNELS:
        return {"state": "no_channels", "message": "config/telegram.py 의 CHANNELS 에 구독 채널을 적으세요"}
    try:
        telegram.credentials()
    except telegram.TelegramUnavailable as exc:
        return {"state": "no_credentials", "message": str(exc)}
    try:
        import telethon  # noqa: F401
    except ImportError:
        return {"state": "no_telethon", "message": "telethon 이 설치되어 있지 않습니다 (pip install telethon)"}
    if not tg_cfg.session_file().exists():
        return {"state": "no_session", "message": "텔레그램 로그인이 필요합니다: .venv\\Scripts\\python scripts\\telegram_login.py"}
    return {"state": "ok", "message": f"{len(tg_cfg.CHANNELS)}개 채널"}


@router.get("/news")
def get_news(tab: str | None = None, q: str = "", asof: str | None = None, limit: int = 80, source: str = "all") -> dict:
    if tab is not None and tab not in news.TABS:
        raise HTTPException(404, "알 수 없는 탭")
    if source not in ("all", "news", "telegram"):
        raise HTTPException(400, "source 는 all / news / telegram")
    try:
        base = canonical_date(asof) if asof else None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    conn = db.connect()
    try:
        items = news.feed(conn, tab, q, base, max(1, min(limit, 300)), source)
        return {"items": items, "keywords": [k for k in dict.fromkeys(cfg.HIGHLIGHT_KEYWORDS)], "telegram": telegram_state()}
    finally:
        conn.close()


class ReadIn(BaseModel):
    read: bool = True


@router.post("/news/{kind}/{item_id}/read")
def set_read(kind: str, item_id: int, body: ReadIn) -> dict:
    conn = db.connect()
    try:
        news.mark_read(conn, kind, item_id, body.read)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        conn.close()
