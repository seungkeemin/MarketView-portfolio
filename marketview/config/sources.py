"""실제 호출로 확인된 외부 소스 코드만 고정한다. (CLAUDE.md 불변 규칙 3)

확인일 표기:
  [A] ficc-desk 2026-08-11 확인 → MarketView 2026-10-09 재호출로 재확인
  [B] MarketView 2026-10-09 탐색 스크립트(scripts/discover.py)로 신규 확인

지연 특성 (2026-10-09 실측)
  ECOS 시장금리(일별)·환율   T+0~T+1 (END_TIME 20261008, KOFR·기준금리·콜 20261007)
  FRED H.15 국채(DGS*)       10-08 15:16 갱신 시 최신 관측일 10-07 → 지연 ~2일
  FRED SP500·NASDAQCOM       당일 저녁(KST 익일 새벽) 갱신 → 지연 ~1일
  FRED DCOILWTICO            10-07 갱신 시 최신 10-06 → 지연 ~3일 (08-11에는 8일이었다)
  KRX 국채선물               T+1 이상 (ficc-desk 2026-08-11 실측)
  smbs IRS                   2026-10-09 낮 조회에 2026.10.08 마감치
  Yahoo                      비공식. 장중 지연 시세. 언제든 깨질 수 있다
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EcosSeries:
    stat_code: str
    cycle: str
    item_code: str
    note: str = ""


@dataclass(frozen=True)
class FredSeries:
    series_id: str
    note: str = ""


@dataclass(frozen=True)
class KrxSeries:
    product_name: str       # 응답 PROD_NM 과 정확히 일치 (부분일치 금지)
    value_field: str


@dataclass(frozen=True)
class KrxIndexSeries:
    endpoint: str           # KRX_INDEX_ENDPOINTS 의 키
    index_name: str         # 응답 IDX_NM 과 정확히 일치
    value_field: str


@dataclass(frozen=True)
class YahooSeries:
    symbol: str
    note: str = ""


# ---------------------------------------------------------------------------
# ECOS [A] — 817Y002 시장금리(일별), 731Y003 환율, 722Y001 기준금리
# ---------------------------------------------------------------------------
ECOS_SERIES: dict[str, EcosSeries] = {
    "bok_base_rate": EcosSeries("722Y001", "D", "0101000"),
    "kofr": EcosSeries("817Y002", "D", "010901000", "수록 2021-11-25~"),
    "cd_91d": EcosSeries("817Y002", "D", "010502000"),
    "cp_91d": EcosSeries("817Y002", "D", "010503000"),
    "ktb_1y": EcosSeries("817Y002", "D", "010190000"),
    "ktb_3y": EcosSeries("817Y002", "D", "010200000"),
    # 5년 항목코드는 010200001 — 3년(010200000)과 마지막 한 자리만 다르다
    "ktb_5y": EcosSeries("817Y002", "D", "010200001"),
    "ktb_10y": EcosSeries("817Y002", "D", "010210000"),
    "ktb_20y": EcosSeries("817Y002", "D", "010220000"),
    "ktb_30y": EcosSeries("817Y002", "D", "010230000"),
    "corp_aa3_3y": EcosSeries("817Y002", "D", "010300000", "회사채(3년, AA-) 수익률"),
    "corp_bbb3_3y": EcosSeries("817Y002", "D", "010320000", "회사채(3년, BBB-) 수익률"),
    # 원/달러(종가 15:30). 매매기준율(731Y001)이 아니다
    "usdkrw": EcosSeries("731Y003", "D", "0000003"),
}

# ---------------------------------------------------------------------------
# FRED [A]+[B]
# ---------------------------------------------------------------------------
FRED_SERIES: dict[str, FredSeries] = {
    "sofr": FredSeries("SOFR"),
    "ust_1m": FredSeries("DGS1MO"),    # [B] 2026-10-09 최신 관측 10-07 = 4.07
    "ust_3m": FredSeries("DGS3MO"),
    "ust_6m": FredSeries("DGS6MO"),    # [B]
    "ust_1y": FredSeries("DGS1"),      # [B]
    "ust_2y": FredSeries("DGS2"),
    "ust_3y": FredSeries("DGS3"),      # [B]
    "ust_5y": FredSeries("DGS5"),
    "ust_7y": FredSeries("DGS7"),      # [B]
    "ust_10y": FredSeries("DGS10"),
    "ust_20y": FredSeries("DGS20"),    # [B]
    "ust_30y": FredSeries("DGS30"),
    # 단위가 bp 가 아니라 % (0.78 = 78bp). 원본 단위 그대로 저장한다
    "us_ig_oas": FredSeries("BAMLC0A0CM"),
    "us_hy_oas": FredSeries("BAMLH0A0HYM2"),
    "usdjpy": FredSeries("DEXJPUS", "1달러당 엔"),
    "eurusd": FredSeries("DEXUSEU", "1유로당 달러"),
    "vix": FredSeries("VIXCLS"),
    "sp500": FredSeries("SP500", "[B] 라이선스상 FRED 는 최근 10년만 제공"),
    "nasdaq": FredSeries("NASDAQCOM", "[B] NASDAQ Composite"),
    # [B] 현물(Cushing) 일별. 지연 ~3일. 장중·근월 선물은 wti_fut(Yahoo)
    "wti": FredSeries("DCOILWTICO"),
}
# 확인했지만 채택하지 않음: DTWEXBGS (Nominal Broad Dollar Index) — DXY 가 아니다.
#                           RU2000PR — FRED 에 없음 (HTTP 400, 2026-10-09)

# ---------------------------------------------------------------------------
# KRX [A] — GET data-dbg.krx.co.kr/svc/apis/drv/fut_bydd_trd?basDd=YYYYMMDD, Header AUTH_KEY
# ---------------------------------------------------------------------------
KRX_FUTURES_ENDPOINT = "drv/fut_bydd_trd"
KRX_SERIES: dict[str, KrxSeries] = {
    "ktbf_3y": KrxSeries("3년국채 선물", "TDD_CLSPRC"),
    "ktbf_10y": KrxSeries("10년국채 선물", "TDD_CLSPRC"),
    "ktbf_30y": KrxSeries("30년국채 선물", "TDD_CLSPRC"),
    "ktbf_3y_oi": KrxSeries("3년국채 선물", "ACC_OPNINT_QTY"),
    "ktbf_10y_oi": KrxSeries("10년국채 선물", "ACC_OPNINT_QTY"),
}
# KRX 지수 [A] — GET .../idx/{kospi,kosdaq}_dd_trd?basDd=YYYYMMDD (2026-10-14 활용신청 승인 후 200 확인).
#   응답 OutBlock_1 은 지수 54행. IDX_NM 이 정확히 '코스피'/'코스닥' 인 행의 CLSPRC_IDX 가 종가
#   ('코스피 (외국주포함)' 행은 값이 빈 문자열). 휴장일은 0행. idx/krx_dd_trd 는 미신청(401).
#   Yahoo ^KS11/^KQ11 은 같은 날 종가가 일치함을 확인(2026-10-14, 코스닥 837.03) → KRX 가 없는 날의 대체.
KRX_INDEX_ENDPOINTS = {"kospi": "idx/kospi_dd_trd", "kosdaq": "idx/kosdaq_dd_trd"}
KRX_INDEX_SERIES: dict[str, KrxIndexSeries] = {
    "kospi": KrxIndexSeries("kospi", "코스피", "CLSPRC_IDX"),
    "kosdaq": KrxIndexSeries("kosdaq", "코스닥", "CLSPRC_IDX"),
}

# ---------------------------------------------------------------------------
# Yahoo Finance 비공식 [B] — GET query1.finance.yahoo.com/v8/finance/chart/{symbol}
#   2026-10-09 14개 심볼 전부 200 응답 확인. 비공식이므로 실패는 항목 단위로 격리한다.
# ---------------------------------------------------------------------------
YAHOO_SERIES: dict[str, YahooSeries] = {
    # 일별 종가만 받는다(장중 스냅샷은 2026-10-14 에 없앴다). KRX 우선(db.upsert_observation 이 KRX 값을 야후가 덮지 않게 막는다). 야후는 KRX 게시 전 날짜의 대체.
    "kospi": YahooSeries("^KS11"),
    "kosdaq": YahooSeries("^KQ11"),
    "russell2000": YahooSeries("^RUT"),
    "dxy": YahooSeries("DX-Y.NYB", note="ICE US Dollar Index"),
    "wti_fut": YahooSeries("CL=F", note="근월 연속선물 — 롤오버 때 레벨이 튄다"),
    "ust_fut_2y": YahooSeries("ZT=F", note="가격(포인트). 수익률 아님"),
    "ust_fut_5y": YahooSeries("ZF=F"),
    "ust_fut_10y": YahooSeries("ZN=F"),
    "ust_fut_30y": YahooSeries("ZB=F"),
}

# ---------------------------------------------------------------------------
# 서울외국환중개 KRW IRS [B] — http://www.smbs.biz/Exchange/IRS.jsp (https 불가)
#   표 값이 JS d1~d5('%_Xhh…') 로 16진 문자코드 인코딩돼 있다 → 디코딩해 파싱.
#   열: 기일물 / Receive / Mid / Pay → Mid 사용.
#   과거 날짜 조회: GET 에 StrSch_* 와 StrSchFull* 12개 필드를 모두 넘기면 해당일 표가 온다 (2026-10-09 확인).
# ---------------------------------------------------------------------------
SMBS_IRS_URL = "http://www.smbs.biz/Exchange/IRS.jsp"
SMBS_IRS_TENORS = ("1Y", "2Y", "3Y", "4Y", "5Y", "7Y", "10Y", "12Y", "15Y", "20Y")
