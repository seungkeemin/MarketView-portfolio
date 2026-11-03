"""RSS/Atom 수집 (로컬 앱과 GitHub Actions 공용). 파싱만 하고 저장·분류는 news.py 가 한다.

실제 피드의 불규칙함은 2026-10-09 호출로 확인했다:
  - RFC822 이지만 '+09:00' 처럼 콜론이 있는 오프셋 (매일경제) → 표준 파서가 틀리게 읽는다
  - 시간대 없는 'YYYY-MM-DD HH:MM:SS' (연합인포맥스 = KST, Investing.com = UTC) → 피드 설정의 naive_tz 로 해석
  - '9 Oct 2026' 처럼 0 패딩 없는 날 (연합뉴스)
  - 제목이 이중으로 HTML 이스케이프됨 (머니투데이 &quot;), 요약이 HTML 조각
  - 구글 뉴스: 링크는 리다이렉트 URL, 제목 끝에 ' - 언론사', 요약은 관련 기사 목록
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx

from ..config.news import SUMMARY_MAX, Feed
from ..config.settings import KST
from .base import Failed, get, scrub

ATOM = "{http://www.w3.org/2005/Atom}"
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_OFFSET_COLON = re.compile(r"([+-]\d{2}):(\d{2})$")
_DROP_PARAMS = ("utm_", "fbclid", "gclid", "ref", "cmpid", "ocid")
MAX_FEED_BYTES = 5_000_000      # 비정상적으로 큰 응답은 파싱하지 않는다
_HTTP = re.compile(r"^https?://", re.I)


@dataclass(frozen=True)
class RawItem:
    feed_id: str
    link: str
    title: str
    summary: str | None
    source: str
    published_at: str        # ISO8601 +09:00


def clean_text(raw: str | None, limit: int | None = None) -> str:
    """HTML 이스케이프를 풀고 태그를 걷어낸다. 이중 이스케이프(&amp;quot;)도 푼다."""
    text = raw or ""
    for _ in range(2):
        if "&" not in text:
            break
        text = html.unescape(text)
    text = _WS.sub(" ", _TAG.sub(" ", text)).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def canonical_link(url: str) -> str:
    """추적 파라미터·프래그먼트를 지워 같은 기사를 같은 키로 만든다."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith(_DROP_PARAMS)]
    path = parts.path[:-1] if len(parts.path) > 1 and parts.path.endswith("/") else parts.path
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def parse_date(raw: str | None, naive_tz: str = "Asia/Seoul") -> datetime | None:
    """여러 날짜 형식을 시간대가 있는 datetime 으로. 해석 못 하면 None (현재 시각으로 대충 채우지 않는다)."""
    text = (raw or "").strip()
    if not text:
        return None
    fixed = _OFFSET_COLON.sub(r"\1\2", text)
    try:
        dt = parsedate_to_datetime(fixed)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        iso = text.replace("Z", "+00:00")
        for candidate in (iso, iso.replace(" ", "T", 1)):
            try:
                dt = datetime.fromisoformat(candidate)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(naive_tz))
    return dt


def _child(el: ET.Element, *names: str) -> str:
    for name in names:
        found = el.find(name)
        if found is not None:
            text = "".join(found.itertext()).strip()      # 이스케이프 없이 태그가 직접 들어간 요약도 텍스트를 읽는다
            if text:
                return text
    return ""


def parse_feed(content: bytes, feed: Feed, now: datetime | None = None) -> list[RawItem]:
    """피드 XML → RawItem. 제목·링크·날짜 중 하나라도 없는 항목은 건너뛴다. 미래 시각(시계 오류)은 현재로 눌러 둔다."""
    if len(content) > MAX_FEED_BYTES:
        raise ValueError("피드 응답이 너무 큼")
    if b"<!ENTITY" in content:                    # 엔티티 확장(billion laughs) 방지: 정상 RSS/Atom 은 엔티티를 선언하지 않는다
        raise ValueError("피드에 엔티티 선언이 있어 건너뜀")
    root = ET.fromstring(content)
    now = now or datetime.now(timezone.utc)
    items = root.findall(".//item") or root.findall(f".//{ATOM}entry")
    out: list[RawItem] = []
    for el in items:
        title = clean_text(_child(el, "title", f"{ATOM}title"))
        link = _child(el, "link")
        if not link:
            atom_link = el.find(f"{ATOM}link")
            link = (atom_link.get("href") or "").strip() if atom_link is not None else ""
        dt = parse_date(_child(el, "pubDate", "published", f"{ATOM}published", f"{ATOM}updated", "date"), feed.naive_tz)
        if not (title and link and dt and _HTTP.match(link.strip())):      # javascript: 같은 스킴은 링크로 쓰지 않는다
            continue
        source = feed.name
        src_el = el.find("source")
        if src_el is not None and (src_el.text or "").strip():
            source = src_el.text.strip()
            if title.endswith(" - " + source):                      # 구글 뉴스: 제목 끝의 ' - 언론사' 를 떼어낸다
                title = title[: -len(" - " + source)].rstrip()
        summary = None
        if feed.summary:
            s = clean_text(_child(el, "description", f"{ATOM}summary", f"{ATOM}content"), SUMMARY_MAX)
            summary = s if s and s != title else None
        if dt > now:
            dt = now
        out.append(RawItem(feed.id, canonical_link(link), title, summary, source, dt.astimezone(KST).isoformat(timespec="seconds")))
    return out


# 피드 서버가 가끔 15초 안에 못 답해 클라우드 회차마다 다른 소스가 타임아웃이 났다(2026-10-14). 읽기 25초 + 재시도 3번.
FEED_TIMEOUT = httpx.Timeout(25.0, connect=15.0)
FEED_RETRIES = 3


def fetch_feed(feed: Feed) -> list[RawItem] | Failed:
    try:
        timeout = httpx.Timeout(feed.read_timeout or FEED_TIMEOUT.read, connect=feed.connect_timeout or FEED_TIMEOUT.connect)
        retries = FEED_RETRIES if feed.retries is None else feed.retries
        return parse_feed(get(feed.url, timeout=timeout, retries=retries).content, feed)
    except ET.ParseError as exc:
        return Failed(feed.id, f"XML 파싱 실패: {exc}")
    except Exception as exc:  # noqa: BLE001
        return Failed(feed.id, scrub(str(exc)))
