"""뉴스 소스와 분류 규칙. 실제 호출로 동작이 확인된 피드만 둔다 (scripts/discover_rss.py, 확인 2026-10-09).

확인했지만 채택하지 않은 것:
  서울경제 https://www.sedaily.com/RSSFeed.xml      HTTP 404
  Yahoo Finance https://finance.yahoo.com/news/rssindex  HTTP 404
  MarketWatch marketpulse                            최신 항목이 1년 전 (사실상 중단)
  CNBC bonds URL                                     CNBC finance 와 같은 피드라 중복
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

VERIFIED_ON = "2026-10-09"
GN_KO = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
GN_EN = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


@dataclass(frozen=True)
class Feed:
    id: str
    name: str
    url: str
    tab: str | None = None          # 이 피드의 기본 탭 (키워드가 더 강하게 가리키면 키워드를 따른다)
    naive_tz: str = "Asia/Seoul"    # 시간대 없는 날짜의 기준 (확인: 인포맥스 = KST, Investing = UTC)
    summary: bool = True            # 요약을 저장할지 (구글 뉴스는 요약이 관련 기사 목록이라 저장하지 않는다)
    read_timeout: float | None = None   # 응답이 느린 피드만 읽기 제한(초)을 늘린다. 비우면 rss.FEED_TIMEOUT
    connect_timeout: float | None = None  # 연결이 자주 막히는 피드만 연결 제한(초)을 줄인다 — 실패 한 번이 회차 시간을 잡아먹지 않게
    retries: int | None = None          # 피드별 재시도 횟수. 비우면 rss.FEED_RETRIES
    cloud: bool = True                  # False 면 GitHub Actions 수집에서 뺀다 (로컬 앱은 계속 받는다)


def _gn(q: str, lang: str = "ko") -> str:
    return (GN_KO if lang == "ko" else GN_EN).format(q=quote(q))


FEEDS: tuple[Feed, ...] = (
    # 구글 뉴스 검색 (2일 이내). 탭별 쿼리
    Feed("gn_bond_ko", "구글뉴스", _gn("국고채 OR 채권시장 OR 금통위 OR 기준금리 when:2d"), "bond", summary=False),
    Feed("gn_fx_ko", "구글뉴스", _gn("원달러 환율 OR 외환시장 OR 달러인덱스 when:2d"), "fx", summary=False),
    Feed("gn_credit_ko", "구글뉴스", _gn('회사채 OR "크레딧 시장" OR 신용스프레드 OR 여전채 OR 은행채 when:2d'), "credit", summary=False),
    Feed("gn_equity_ko", "구글뉴스", _gn("코스피 OR 증시 OR 나스닥 when:2d"), "equity", summary=False),
    Feed("gn_rates_en", "Google News", _gn("Treasury yields OR FOMC OR Fed rate when:2d", "en"), "bond", summary=False),
    Feed("gn_fx_en", "Google News", _gn("dollar yen won currency when:2d", "en"), "fx", summary=False),
    Feed("gn_credit_en", "Google News", _gn("credit spreads OR high yield OR corporate bonds when:2d", "en"), "credit", summary=False),
    Feed("gn_equity_en", "Google News", _gn("stocks Wall Street S&P 500 when:2d", "en"), "equity", summary=False),
    # 국내 경제지·통신사 (탭은 키워드로 분류)
    Feed("hankyung_economy", "한국경제", "https://www.hankyung.com/feed/economy"),
    Feed("hankyung_finance", "한국경제", "https://www.hankyung.com/feed/finance", "equity"),
    Feed("mk_economy", "매일경제", "https://www.mk.co.kr/rss/30100041/"),
    Feed("mk_stock", "매일경제", "https://www.mk.co.kr/rss/50200011/", "equity"),
    Feed("yna_economy", "연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
    Feed("yna_market", "연합뉴스", "https://www.yna.co.kr/rss/market.xml", "equity"),
    Feed("einfomax", "연합인포맥스", "https://news.einfomax.co.kr/rss/allArticle.xml", "bond"),
    Feed("edaily", "이데일리", "http://rss.edaily.co.kr/edaily_news.xml"),
    # 클라우드(GitHub 러너)에서 연결 단계가 자주 막힌다(2026-10-15 확인): 뉴스 회차가 성공하면 5초, 실패하면 74~77초(= 연결 15초 × 4회 + 대기).
    # 읽기 제한(50초)을 늘려도 소용없었고, 연결 제한을 줄여도(6초·1회, 실패 17초) 계속 실패해서 클라우드 수집에서는 뺐다(2026-10-15).
    # 로컬 앱은 집 IP 로 0.1~0.3초에 받는다 → PC 가 꺼진 시간대의 머니투데이 기사만 비는 것을 감수한다.
    Feed("mt", "머니투데이", "https://rss.mt.co.kr/mt_news.xml", connect_timeout=6.0, retries=1, cloud=False),
    # 해외 매체·기관
    Feed("cnbc_top", "CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    Feed("cnbc_finance", "CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    Feed("mw_top", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    Feed("investing", "Investing.com", "https://www.investing.com/rss/news.rss", naive_tz="UTC"),
    Feed("fed_press", "연준 보도자료", "https://www.federalreserve.gov/feeds/press_all.xml", "bond"),
    Feed("ecb_press", "ECB 보도자료", "https://www.ecb.europa.eu/rss/press.html", "bond"),
    Feed("bok_press", "한국은행 보도자료", "https://www.bok.or.kr/portal/bbs/P0000559/news.rss?menuNo=200690", "bond"),
)

# 탭 분류 키워드 (제목 3점, 요약 1점, 피드 기본 탭 +2점). 동점이면 앞선 탭이 이긴다 (크레딧 > 외환 > 채권 > 주식).
TAB_PRIORITY = ("credit", "fx", "bond", "equity")
TAB_KEYWORDS: dict[str, tuple[str, ...]] = {
    "bond": ("국고채", "채권", "금리", "금통위", "기준금리", "국채", "FOMC", "연준", "한국은행", "한은", "수익률", "스티프닝", "플래트닝",
             "KOFR", "CD금리", "통화정책", "인플레이션", "CPI", "Treasury", "Treasuries", "yield", "bond", "Fed ", "rate cut", "rate hike",
             "inflation", "SOFR", "swap", "입찰"),
    "fx": ("환율", "원/달러", "원·달러", "원달러", "달러화", "달러-원", "달러/원", "달러 강세", "달러 약세", "엔화", "위안화", "외환", "FX", "dollar", "yen", "euro ", "yuan", "currency", "DXY",
           "달러인덱스", "NDF", "forex"),
    "credit": ("회사채", "크레딧 시장", "크레딧물", "크레딧 스프레드", "신용스프레드", "신용 스프레드", "여전채", "은행채", "카드채", "공사채", "신용등급",
               "CDS", "하이일드", "부도", "credit spread", "credit market", "high yield", "investment grade", "corporate bond", "junk bond",
               "debt default", "downgrade"),
    "equity": ("코스피", "코스닥", "증시", "주가", "나스닥", "S&P", "다우", "VIX", "주식", "stocks", "equities", "Nasdaq", "Dow ",
               "Russell", "Wall Street", "shares", "earnings"),
}

# 화면에서 하이라이트하는 키워드 (제목·요약·텔레그램 본문). 설정 파일이라 자유롭게 고친다
HIGHLIGHT_KEYWORDS: tuple[str, ...] = ("금통위", "FOMC", "입찰", "CPI", "의사록", "기준금리", "PCE", "고용", "국고채", "환율")

MIN_SCORE_GENERAL = 3        # 기본 탭이 없는 피드: 제목 키워드 1개(3점) 이상이어야 분류
SUMMARY_MAX = 400
KEEP_DAYS_VISIBLE = 14          # 화면 기본 조회 범위 (DB 에는 영구 보관)
