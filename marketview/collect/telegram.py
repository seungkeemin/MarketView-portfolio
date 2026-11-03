"""텔레그램 리서치 채널 수집 (Telethon, 읽기 전용, 로컬 전용).

이 파일은 메시지를 읽기만 한다. 전송·삭제·편집·읽음 처리 호출을 쓰지 않는다
(tests/test_news.py 가 이 파일과 scripts/telegram_*.py 에 금지 호출이 없는지 검사한다).
telethon 은 실제 클라이언트를 만들 때만 가져온다 → 설치·로그인 전에도 앱은 정상 동작하고 텔레그램만 '미연결'로 보인다.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from ..config import telegram as cfg
from ..config.settings import KST, env, now_kst
from ..news import classify


class TelegramUnavailable(RuntimeError):
    """설정·설치·로그인이 안 돼 있어 수집을 건너뛴다 (오류가 아니다). 메시지는 사용자가 할 일을 알려준다."""


@dataclass(frozen=True)
class RawMessage:
    channel: str
    channel_title: str
    msg_id: int
    body: str
    posted_at: str                 # ISO8601 +09:00
    links: tuple[str, ...] = ()
    attachments: tuple[dict, ...] = ()   # {name, mime, size, kind}
    edited_at: str | None = None


class Source(Protocol):
    def fetch(self, channel: cfg.Channel, min_id: int, limit: int, recheck_ids: list[int]) -> tuple[list[RawMessage], list[RawMessage]]:
        """(새 메시지, 다시 읽은 최근 메시지). 새 메시지는 msg_id 오름차순."""


def credentials() -> tuple[int, str]:
    api_id, api_hash = env("TELEGRAM_API_ID"), env("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise TelegramUnavailable("TELEGRAM_API_ID / TELEGRAM_API_HASH 가 .env 에 없습니다 (my.telegram.org 에서 발급)")
    try:
        return int(api_id), api_hash
    except ValueError:
        raise TelegramUnavailable("TELEGRAM_API_ID 는 숫자여야 합니다") from None


def _iso_kst(dt: datetime) -> str:
    return dt.astimezone(KST).isoformat(timespec="seconds")


def to_raw(m, channel: cfg.Channel, title: str) -> RawMessage:
    """Telethon Message → RawMessage. 쓰기 호출 없이 속성만 읽는다."""
    links: list[str] = []
    try:
        for ent, text in m.get_entities_text():
            url = getattr(ent, "url", None) or (text if type(ent).__name__ == "MessageEntityUrl" else None)
            if url and url not in links:
                links.append(url)
    except Exception:  # noqa: BLE001 - 엔티티를 못 읽어도 본문은 저장한다
        pass
    attachments: list[dict] = []
    doc = getattr(m, "document", None)
    if doc is not None:
        name = next((a.file_name for a in getattr(doc, "attributes", []) if hasattr(a, "file_name")), None) or "첨부"
        mime = getattr(doc, "mime_type", "") or ""
        kind = "pdf" if mime == "application/pdf" or name.lower().endswith(".pdf") else "file"
        attachments.append({"name": name, "mime": mime, "size": getattr(doc, "size", None), "kind": kind})
    elif getattr(m, "photo", None) is not None:
        attachments.append({"name": "이미지", "mime": "image/jpeg", "size": None, "kind": "photo"})
    edit = getattr(m, "edit_date", None)
    return RawMessage(channel.key, title, m.id, (m.message or "").strip(), _iso_kst(m.date), tuple(links), tuple(attachments),
                      _iso_kst(edit) if edit else None)


class TelethonSource:
    """실제 텔레그램에서 읽는다. 로그인(scripts/telegram_login.py)이 끝난 세션이 있어야 한다."""

    def fetch(self, channel: cfg.Channel, min_id: int, limit: int, recheck_ids: list[int]):
        import asyncio
        return asyncio.run(self._fetch(channel, min_id, limit, recheck_ids))

    async def _fetch(self, channel: cfg.Channel, min_id: int, limit: int, recheck_ids: list[int]):
        out = (await self._fetch_many([(channel, min_id, limit, recheck_ids)]))[0]
        if isinstance(out, Exception):
            raise out
        return out

    def fetch_many(self, items: list[tuple]) -> list:
        """채널 여러 개를 연결 한 번으로 동시에 읽는다. [(channel, min_id, limit, recheck_ids)] → 같은 순서의
        (new, again) 또는 그 채널의 예외. 채널마다 연결·해제하면 5채널에 ~10초가 걸렸다.
        세션 파일은 한 클라이언트만 쓸 수 있어서(여러 클라이언트면 'database is locked') 클라이언트를 하나로 두고 요청만 겹친다."""
        import asyncio
        return asyncio.run(self._fetch_many(items))

    async def _fetch_many(self, items: list[tuple]) -> list:
        import asyncio
        try:
            from telethon import TelegramClient
        except ImportError:
            raise TelegramUnavailable("telethon 이 설치되어 있지 않습니다 (pip install telethon)") from None
        if not cfg.session_file().exists():
            raise TelegramUnavailable("텔레그램 로그인이 필요합니다: .venv\\Scripts\\python scripts\\telegram_login.py")
        api_id, api_hash = credentials()
        client = TelegramClient(str(cfg.SESSION_PATH), api_id, api_hash)
        await client.connect()                           # start() 는 로그인 프롬프트를 띄우므로 쓰지 않는다
        try:
            if not await client.is_user_authorized():
                raise TelegramUnavailable("텔레그램 세션이 만료되었습니다: scripts\\telegram_login.py 를 다시 실행하세요")

            async def one(channel, min_id, limit, recheck_ids):
                try:
                    key = int(channel.key) if channel.key.lstrip("-").isdigit() else channel.key
                    entity = await client.get_entity(key)
                    title = getattr(entity, "title", None) or channel.name
                    new = []
                    async for m in client.iter_messages(entity, min_id=min_id, limit=limit, reverse=min_id > 0):
                        new.append(to_raw(m, channel, title))
                    new.sort(key=lambda r: r.msg_id)
                    again = [to_raw(m, channel, title) for m in await client.get_messages(entity, ids=recheck_ids) if m] if recheck_ids else []
                    return new, again
                except Exception as exc:  # noqa: BLE001 - 채널 하나의 실패가 나머지를 막지 않는다
                    return exc

            return list(await asyncio.gather(*(one(*it) for it in items)))
        finally:
            await client.disconnect()


# --- 저장 ----------------------------------------------------------------------
def _classify_msg(body: str, default_tab: str | None) -> str | None:
    first, _, rest = body.partition("\n")
    return classify(first[:140], rest, default_tab)


def store_messages(conn: sqlite3.Connection, channel: cfg.Channel, new: list[RawMessage], again: list[RawMessage]) -> dict:
    """새 메시지는 (채널, msg_id) 로 중복을 막고, 이미 저장된 메시지의 본문이 바뀌었으면 수정본을 따로 남긴다 (원본은 그대로)."""
    now = now_kst()
    inserted = edits = 0
    for r in new:
        cur = conn.execute(
            "INSERT OR IGNORE INTO tg_message (channel,channel_title,msg_id,body,posted_at,links_json,attachments_json,tab,fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (r.channel, r.channel_title, r.msg_id, r.body, r.posted_at, json.dumps(list(r.links), ensure_ascii=False),
             json.dumps(list(r.attachments), ensure_ascii=False), _classify_msg(r.body, channel.tab), now))
        inserted += cur.rowcount
    for r in new + again:
        row = conn.execute("SELECT body FROM tg_message WHERE channel=? AND msg_id=?", (r.channel, r.msg_id)).fetchone()
        if row is None or not r.edited_at:
            continue
        last = conn.execute("SELECT body FROM tg_message_edit WHERE channel=? AND msg_id=? ORDER BY id DESC LIMIT 1",
                            (r.channel, r.msg_id)).fetchone()
        latest = last["body"] if last else row["body"]
        if r.body != latest:
            conn.execute("INSERT INTO tg_message_edit (channel,msg_id,body,edited_at,seen_at) VALUES (?,?,?,?,?)",
                         (r.channel, r.msg_id, r.body, r.edited_at, now))
            edits += 1
    conn.commit()
    return {"new": inserted, "edits": edits}


def collect(conn: sqlite3.Connection, source: Source | None = None, channels=None) -> list[dict]:
    """채널마다 수집한다. 설정·설치·로그인이 안 돼 있으면 한 줄짜리 'skipped' 를 돌려준다 (오류로 세지 않는다)."""
    channels = cfg.CHANNELS if channels is None else channels
    if not channels:
        return [{"channel": "-", "status": "skipped", "message": "config/telegram.py 의 CHANNELS 에 구독 채널을 적으세요", "new": 0, "edits": 0}]
    if source is None:
        try:
            credentials()
        except TelegramUnavailable as exc:
            return [{"channel": "-", "status": "skipped", "message": str(exc), "new": 0, "edits": 0}]
        source = TelethonSource()
    out = []
    recheck_since = _iso_kst(datetime.now(KST) - timedelta(hours=cfg.EDIT_RECHECK_HOURS))
    prepared = []
    for ch in channels:
        last = conn.execute("SELECT COALESCE(MAX(msg_id),0) FROM tg_message WHERE channel=?", (ch.key,)).fetchone()[0]
        ids = [r[0] for r in conn.execute("SELECT msg_id FROM tg_message WHERE channel=? AND posted_at>=?", (ch.key, recheck_since))]
        prepared.append((ch, last, ids))
    if hasattr(source, "fetch_many"):                      # 실제 소스: 연결 한 번으로 동시에 (시험용 가짜 소스는 채널마다)
        try:
            fetched = source.fetch_many([(ch, last, cfg.FIRST_FETCH_LIMIT, ids) for ch, last, ids in prepared])
        except TelegramUnavailable as exc:
            return [{"channel": "-", "status": "skipped", "message": str(exc), "new": 0, "edits": 0}]
        except Exception as exc:  # noqa: BLE001
            return [{"channel": "-", "status": "error", "message": f"{type(exc).__name__}: {exc}"[:200], "new": 0, "edits": 0}]
    else:
        fetched = []
        for ch, last, ids in prepared:
            try:
                fetched.append(source.fetch(ch, last, cfg.FIRST_FETCH_LIMIT, ids))
            except Exception as exc:  # noqa: BLE001
                fetched.append(exc)
    for (ch, _, _), res in zip(prepared, fetched):
        if isinstance(res, TelegramUnavailable):
            out.append({"channel": ch.key, "status": "skipped", "message": str(res), "new": 0, "edits": 0})
            break                                          # 로그인 문제는 채널마다 반복해도 같다
        if isinstance(res, Exception):                     # 한 채널이 실패해도 나머지는 계속
            out.append({"channel": ch.key, "status": "error", "message": f"{type(res).__name__}: {res}"[:200], "new": 0, "edits": 0})
            continue
        try:
            new, again = res
            stored = store_messages(conn, ch, new, again)
            out.append({"channel": ch.key, "status": "ok" if new or again else "miss", "message": None, **stored})
        except Exception as exc:  # noqa: BLE001
            out.append({"channel": ch.key, "status": "error", "message": f"{type(exc).__name__}: {exc}"[:200], "new": 0, "edits": 0})
    return out
