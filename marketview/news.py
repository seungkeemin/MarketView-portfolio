"""뉴스·텔레그램 저장, 탭 분류, 조회·검색. 수집은 collect/rss.py · collect/telegram.py, 여기는 DB 쪽이다.

기사 본문은 저장하지 않는다 (제목·요약·링크). 같은 링크는 한 번만 저장한다(UNIQUE).
같은 기사가 여러 피드로 오면 화면에서는 title_key 로 한 번만 보여준다.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from .collect.base import Failed
from .collect.rss import RawItem, fetch_feed
from .config import news as cfg
from .config.settings import now_kst, today_kst

TABS = ("bond", "fx", "credit", "equity")
DUP_WINDOW = timedelta(hours=12)      # 같은 제목이 이 시간 안에 다시 나오면 같은 기사(재게시). 넘으면 다른 날 기사
DUP_FETCH_FACTOR = 5                  # 중복을 걷어내고도 limit 개가 남도록 넉넉히 읽는다
_KEY_STRIP = re.compile(r"[^0-9A-Za-z가-힣]+")


def title_key(title: str) -> str:
    return _KEY_STRIP.sub("", title).lower()[:60]


def classify(title: str, summary: str | None, default_tab: str | None) -> str | None:
    """키워드 점수(제목 3·요약 1·피드 기본 탭 +2)로 탭을 정한다. 단서가 부족하면 None (탭엔 안 보이고 검색엔 나온다)."""
    scores = dict.fromkeys(TABS, 0)
    low_t, low_s = title.lower(), (summary or "").lower()
    for tab, words in cfg.TAB_KEYWORDS.items():
        for w in words:
            lw = w.lower()
            if lw in low_t:
                scores[tab] += 3
            if lw in low_s:
                scores[tab] += 1
    if default_tab in scores:
        scores[default_tab] += 2
    best = max(cfg.TAB_PRIORITY, key=lambda t: (scores[t], -cfg.TAB_PRIORITY.index(t)))
    # 기본 탭이 없는 일반 피드는 제목에 단서가 있어야 한다 (요약에 '달러' 가 한 번 나왔다고 외환 기사가 되지는 않는다)
    need = 1 if default_tab in scores else cfg.MIN_SCORE_GENERAL
    return best if scores[best] >= need else None


def store_items(conn: sqlite3.Connection, items: list[RawItem], origin: str = "local", feed_tabs: dict[str, str | None] | None = None) -> int:
    """새 기사만 저장하고 개수를 돌려준다 (이미 있는 링크는 건너뜀 → 같은 입력을 몇 번 넣어도 결과가 같다)."""
    feed_tabs = feed_tabs or {f.id: f.tab for f in cfg.FEEDS}
    fetched, n = now_kst(), 0
    for it in items:
        tab = classify(it.title, it.summary, feed_tabs.get(it.feed_id))
        cur = conn.execute(
            "INSERT OR IGNORE INTO news (link,title,summary,source,feed_id,published_at,tab,title_key,fetched_at,origin) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (it.link, it.title, it.summary, it.source, it.feed_id, it.published_at, tab, title_key(it.title), fetched, origin))
        n += cur.rowcount
    conn.commit()
    return n


def collect_rss(conn: sqlite3.Connection, feeds=cfg.FEEDS, fetch: Callable = fetch_feed, workers: int = 8,
                origin: str = "local") -> list[dict]:
    """피드 전부를 병렬로 받아 저장한다. 피드별 결과 [{feed, status, fetched, new, message}] — 한 피드가 실패해도 나머지는 계속."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(fetch, feeds))
    out = []
    for feed, res in zip(feeds, results):          # DB 쓰기는 이 스레드 하나에서 (SQLite 연결 하나)
        if isinstance(res, Failed):
            out.append({"feed": feed.id, "status": "error", "fetched": 0, "new": 0, "message": res.message})
            continue
        new = store_items(conn, res, origin=origin, feed_tabs={feed.id: feed.tab})
        out.append({"feed": feed.id, "status": "ok" if res else "miss", "fetched": len(res), "new": new,
                    "message": None if res else "항목 없음"})
    return out


# --- 조회 ---------------------------------------------------------------------
def _fts_query(q: str) -> str:
    return '"' + q.replace('"', '""') + '"'


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _asof_end(asof: str | None) -> str | None:
    return f"{asof}T23:59:59+09:00" if asof else None


def _news_rows(conn: sqlite3.Connection, tab: str | None, q: str, asof: str | None, since: str | None, limit: int) -> list[dict]:
    where, args = ["1=1"], []
    if q:
        if len(q) >= 3:                                       # trigram FTS 는 3글자 이상부터
            where.append("n.id IN (SELECT rowid FROM news_fts WHERE news_fts MATCH ?)")
            args.append(_fts_query(q))
        else:
            where.append("(n.title LIKE ? ESCAPE '\\' OR n.summary LIKE ? ESCAPE '\\')")
            args += [_like(q), _like(q)]
    elif tab:                                                 # 검색은 탭을 가리지 않고 전체에서 찾는다
        where.append("n.tab = ?")
        args.append(tab)
    if asof:
        where.append("n.published_at <= ?")
        args.append(_asof_end(asof))
    if since and not q:
        where.append("n.published_at >= ?")
        args.append(since)
    sql = f"SELECT n.* FROM news n WHERE {' AND '.join(where)} ORDER BY n.published_at DESC LIMIT ?"
    return _dedupe([dict(r) for r in conn.execute(sql, [*args, limit * DUP_FETCH_FACTOR])], limit)


def _dedupe(rows: list[dict], limit: int) -> list[dict]:
    """같은 기사(title_key)가 여러 피드로 왔으면 먼저 저장된 것 하나만. 필터(탭·기간·검색)를 통과한 행끼리만 비교하고,
    발행 시각이 DUP_WINDOW 안일 때만 같은 기사로 본다 — 매일 같은 제목으로 나오는 시황 기사가 첫날 이후 사라지지 않게."""
    seen: dict[str, list[datetime]] = {}
    kept: list[dict] = []
    for r in sorted(rows, key=lambda r: r["id"]):
        when, key = datetime.fromisoformat(r["published_at"]), r["title_key"] or r["link"]
        if any(abs(when - o) < DUP_WINDOW for o in seen.get(key, ())):
            continue
        seen.setdefault(key, []).append(when)
        kept.append(r)
    kept.sort(key=lambda r: r["published_at"], reverse=True)
    return kept[:limit]


def _tg_rows(conn: sqlite3.Connection, tab: str | None, q: str, asof: str | None, since: str | None, limit: int) -> list[dict]:
    where, args = ["1=1"], []
    if q:
        if len(q) >= 3:
            where.append("t.id IN (SELECT rowid FROM tg_fts WHERE tg_fts MATCH ?)")
            args.append(_fts_query(q))
        else:
            where.append("t.body LIKE ? ESCAPE '\\'")
            args.append(_like(q))
    elif tab:
        where.append("t.tab = ?")
        args.append(tab)
    if asof:
        where.append("t.posted_at <= ?")
        args.append(_asof_end(asof))
    if since and not q:
        where.append("t.posted_at >= ?")
        args.append(since)
    rows = conn.execute(f"SELECT t.* FROM tg_message t WHERE {' AND '.join(where)} ORDER BY t.posted_at DESC LIMIT ?", [*args, limit])
    return [dict(r) for r in rows]


def _tg_link(channel: str, msg_id: int) -> str:
    """채널 키(@username 또는 숫자 id)로 텔레그램 메시지 링크를 만든다."""
    ch = channel.lstrip("@")
    return f"https://t.me/{ch}/{msg_id}" if not ch.lstrip("-").isdigit() else f"https://t.me/c/{ch.lstrip('-').removeprefix('100')}/{msg_id}"


def feed(conn: sqlite3.Connection, tab: str | None, q: str = "", asof: str | None = None, limit: int = 80, source: str = "all") -> list[dict]:
    """뉴스 + 텔레그램을 최신순으로 합쳐 돌려준다. 기본 14일, 검색은 기간 제한 없이 전체."""
    q = q.strip()
    base = date.fromisoformat(asof) if asof else today_kst()
    since_iso = (base - timedelta(days=cfg.KEEP_DAYS_VISIBLE)).isoformat()
    items: list[dict] = []
    if source in ("all", "news"):
        for r in _news_rows(conn, tab, q, asof, since_iso, limit):
            items.append({"kind": "news", "id": r["id"], "title": r["title"], "summary": r["summary"], "source": r["source"],
                          "published_at": r["published_at"], "link": r["link"], "tab": r["tab"], "is_read": bool(r["is_read"]),
                          "links": [], "attachments": []})
    if source in ("all", "telegram"):
        for r in _tg_rows(conn, tab, q, asof, since_iso, limit):
            edit = conn.execute("SELECT body FROM tg_message_edit WHERE channel=? AND msg_id=? ORDER BY id DESC LIMIT 1",
                                (r["channel"], r["msg_id"])).fetchone()
            body = (edit["body"] if edit else r["body"]) or ""          # 원본은 DB 에 그대로 두고, 보여줄 때는 최신 수정본을 쓴다
            attachments = json.loads(r["attachments_json"])
            first, _, rest = body.partition("\n")
            if not first.strip():                                  # 이미지·파일만 있는 메시지는 첨부 이름을 제목으로 쓴다
                a = attachments[0] if attachments else None
                if a is None:
                    first = "(내용 없음)"
                elif a["kind"] == "pdf":
                    first = "[PDF] " + a["name"]
                elif a["kind"] == "photo":
                    first = "[이미지]"
                else:
                    first = "[파일] " + a["name"]
            items.append({"kind": "telegram", "id": r["id"], "title": first[:140], "summary": rest.strip() or None,
                          "body": body, "edited": edit is not None, "source": f"텔레그램 · {r['channel_title'] or r['channel']}", "published_at": r["posted_at"],
                          "link": _tg_link(r["channel"], r["msg_id"]), "tab": r["tab"], "is_read": bool(r["is_read"]),
                          "links": json.loads(r["links_json"]), "attachments": attachments})
    items.sort(key=lambda x: x["published_at"], reverse=True)
    return items[:limit]


def mark_read(conn: sqlite3.Connection, kind: str, item_id: int, read: bool = True) -> None:
    table = {"news": "news", "telegram": "tg_message"}.get(kind)
    if table is None:
        raise ValueError("kind 는 news / telegram")
    cur = conn.execute(f"UPDATE {table} SET is_read=? WHERE id=?", (1 if read else 0, item_id))
    if cur.rowcount == 0:
        raise LookupError("항목을 찾을 수 없습니다")
    conn.commit()
