"""RSS 후보를 실제로 호출해 확인하는 탐색 스크립트 (Phase 5). 결과는 config/news.py 에 확인일과 함께 고정한다.

  .venv\\Scripts\\python scripts\\discover_rss.py
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marketview.collect.base import get, scrub  # noqa: E402

GN_KO = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
GN_EN = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

CANDIDATES: list[tuple[str, str]] = [
    ("GN 채권(ko)", GN_KO.format(q=quote("국고채 OR 채권시장 OR 금통위 when:2d"))),
    ("GN 외환(ko)", GN_KO.format(q=quote("원달러 환율 OR 외환시장 when:2d"))),
    ("GN 신용(ko)", GN_KO.format(q=quote("회사채 OR 크레딧 OR 신용스프레드 OR 여전채 when:2d"))),
    ("GN 주식(ko)", GN_KO.format(q=quote("코스피 OR 증시 when:2d"))),
    ("GN rates(en)", GN_EN.format(q=quote("Treasury yields OR FOMC when:2d"))),
    ("한국경제 경제", "https://www.hankyung.com/feed/economy"),
    ("한국경제 증권", "https://www.hankyung.com/feed/finance"),
    ("매일경제 경제", "https://www.mk.co.kr/rss/30100041/"),
    ("매일경제 증권", "https://www.mk.co.kr/rss/50200011/"),
    ("연합뉴스 경제", "https://www.yna.co.kr/rss/economy.xml"),
    ("연합뉴스 마켓+", "https://www.yna.co.kr/rss/market.xml"),
    ("연합인포맥스", "https://news.einfomax.co.kr/rss/allArticle.xml"),
    ("이데일리", "http://rss.edaily.co.kr/edaily_news.xml"),
    ("서울경제", "https://www.sedaily.com/RSSFeed.xml"),
    ("머니투데이", "https://rss.mt.co.kr/mt_news.xml"),
    ("CNBC top", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("CNBC finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("CNBC bonds", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("MarketWatch top", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("MarketWatch markets", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("Fed press", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("ECB press", "https://www.ecb.europa.eu/rss/press.html"),
    ("Investing news", "https://www.investing.com/rss/news.rss"),
    ("BOK 보도자료", "https://www.bok.or.kr/portal/bbs/P0000559/news.rss?menuNo=200690"),
]


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def probe(name: str, url: str) -> str:
    try:
        r = get(url)
    except Exception as exc:  # noqa: BLE001
        return f"{name:16} FAIL {scrub(str(exc))}"
    ctype = r.headers.get("content-type", "")
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as exc:
        return f"{name:16} 200 but XML 파싱 실패 ({ctype}) {exc}"
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    if not items:
        return f"{name:16} 200 XML 인데 item 0 ({ctype}) root={root.tag}"
    first = items[0]
    title = _text(first.find("title")) or _text(first.find("{http://www.w3.org/2005/Atom}title"))
    pub = _text(first.find("pubDate")) or _text(first.find("{http://www.w3.org/2005/Atom}updated"))
    age = ""
    try:
        dt = parsedate_to_datetime(pub) if pub else None
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = f"{(datetime.now(timezone.utc) - dt).total_seconds() / 3600:.1f}h 전"
    except (TypeError, ValueError):
        age = f"pub={pub[:25]}"
    return f"{name:16} OK  items={len(items):3} 최신 {age:10} | {title[:46]}"


if __name__ == "__main__":
    for n, u in CANDIDATES:
        print(probe(n, u))
