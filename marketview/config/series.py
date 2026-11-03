"""화면·DB 에 노출되는 항목 사전. series_def 테이블의 원본이다.

status: confirmed(공식 API 실호출 확인) | unofficial(비공식 소스) | manual(소스 없음 · 수동 입력)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SeriesDef:
    key: str
    label: str
    tab: str                 # bond | fx | credit | equity
    group: str
    unit: str                # pct | krw | index | price | contracts
    decimals: int
    provider: str            # ecos | fred | krx | smbs | yahoo | manual
    status: str
    tenor_months: int | None = None
    delta_unit: str = "bp"   # 전일 대비 표시: bp | pct | abs
    note: str = ""
    step: bool = False       # 결정·변경 때만 값이 바뀌는 계단형(기준금리). 오래된 관측일이 지연이 아니므로 "이전 값" 표시에서 뺀다


_S: list[SeriesDef] = []


def _add(*args, **kwargs) -> None:
    _S.append(SeriesDef(*args, **kwargs))


# --- 채권: 국내 단기 ------------------------------------------------------
_add("bok_base_rate", "기준금리", "bond", "kr_short", "pct", 2, "ecos", "confirmed", step=True,
     note="금통위 결정 때만 변경 · 관측일은 마지막 변경 게시일")
_add("kofr", "KOFR", "bond", "kr_short", "pct", 3, "ecos", "confirmed")
_add("cd_91d", "CD 91일", "bond", "kr_short", "pct", 2, "ecos", "confirmed")
_add("cp_91d", "CP 91일", "bond", "kr_short", "pct", 2, "ecos", "confirmed")
_add("kr_cds_5y", "한국 CDS 5Y", "bond", "kr_short", "bp", 1, "manual", "manual",
     delta_unit="abs", note="worldgovernmentbonds 는 JS 렌더링 → 수동 입력")

# --- 채권: 국고채 ----------------------------------------------------------
for _m, _lbl in ((12, "1년"), (36, "3년"), (60, "5년"), (120, "10년"), (240, "20년"), (360, "30년")):
    _add(f"ktb_{_m // 12}y", f"국고 {_lbl}", "bond", "ktb", "pct", 3, "ecos", "confirmed", _m)
for _m, _lbl in ((36, "3년"), (120, "10년"), (360, "30년")):
    _add(f"ktbf_{_m // 12}y", f"국채선물 {_lbl}", "bond", "ktbf", "price", 2, "krx", "confirmed", _m,
         delta_unit="abs", note="종가(최근월, 정규장). 내재수익률은 조회 시 계산")
for _y in (3, 10):
    _add(f"ktbf_{_y}y_oi", f"국채선물 {_y}년 미결제", "bond", "ktbf_oi", "contracts", 0, "krx", "confirmed",
         delta_unit="abs")

# --- 채권: KRW IRS ---------------------------------------------------------
for _t in (1, 2, 3, 4, 5, 7, 10, 12, 15, 20):
    _add(f"irs_{_t}y", f"KRW IRS {_t}년", "bond", "irs", "pct", 4, "smbs", "unofficial", _t * 12,
         note="서울외국환중개 Mid. 마감 후 게시")

# --- 채권: 미국 ------------------------------------------------------------
_add("sofr", "SOFR", "bond", "us_short", "pct", 2, "fred", "confirmed")
_add("wti", "WTI 현물", "bond", "us_short", "price", 2, "fred", "confirmed", delta_unit="pct",
     note="FRED DCOILWTICO, 지연 ~3일")
_add("wti_fut", "WTI 근월선물", "bond", "us_short", "price", 2, "yahoo", "unofficial", delta_unit="pct",
     note="Yahoo CL=F")
for _key, _m, _lbl in (("1m", 1, "1개월"), ("3m", 3, "3개월"), ("6m", 6, "6개월"), ("1y", 12, "1년"),
                       ("2y", 24, "2년"), ("3y", 36, "3년"), ("5y", 60, "5년"), ("7y", 84, "7년"),
                       ("10y", 120, "10년"), ("20y", 240, "20년"), ("30y", 360, "30년")):
    _add(f"ust_{_key}", f"UST {_lbl}", "bond", "ust", "pct", 2, "fred", "confirmed", _m)
_add("us_ig_oas", "미국 IG OAS", "bond", "us_credit", "pct", 2, "fred", "confirmed",
     note="원본 단위 %, 표시는 bp")
_add("us_hy_oas", "미국 HY OAS", "bond", "us_credit", "pct", 2, "fred", "confirmed",
     note="원본 단위 %, 표시는 bp")
for _key, _m, _lbl in (("2y", 24, "2년"), ("5y", 60, "5년"), ("10y", 120, "10년"), ("30y", 360, "30년")):
    _add(f"ust_fut_{_key}", f"미국채선물 {_lbl}", "bond", "ust_fut", "price", 3, "yahoo", "unofficial", _m,
         delta_unit="abs", note="Yahoo ZT/ZF/ZN/ZB 근월 연속선물 가격")

# --- 외환 -------------------------------------------------------------------
_add("usdkrw", "USD/KRW", "fx", "fx", "krw", 2, "ecos", "confirmed", delta_unit="abs",
     note="일별=ECOS 종가 15:30")
_add("ndf_1m", "USD/KRW NDF 1M", "fx", "fx", "krw", 2, "manual", "manual", delta_unit="abs",
     note="무료 아웃라이트 소스 없음 · 수동 입력")
_add("usdjpy", "USD/JPY", "fx", "fx", "index", 3, "fred", "confirmed", delta_unit="abs")
_add("eurusd", "EUR/USD", "fx", "fx", "index", 4, "fred", "confirmed", delta_unit="abs")
_add("dxy", "DXY", "fx", "fx", "index", 2, "yahoo", "unofficial", delta_unit="abs",
     note="Yahoo DX-Y.NYB (ICE). FRED DTWEXBGS 는 다른 지수")

# --- 신용 -------------------------------------------------------------------
_add("corp_aa3_3y", "회사채 AA- 3년", "credit", "credit_kr", "pct", 3, "ecos", "confirmed", 36)
_add("corp_bbb3_3y", "회사채 BBB- 3년", "credit", "credit_kr", "pct", 3, "ecos", "confirmed", 36)

# --- 주식 -------------------------------------------------------------------
_add("kospi", "KOSPI", "equity", "equity", "index", 2, "krx", "confirmed", delta_unit="pct",
     note="일별=KRX(T+1), KRX 게시 전=Yahoo ^KS11")
_add("kosdaq", "KOSDAQ", "equity", "equity", "index", 2, "krx", "confirmed", delta_unit="pct",
     note="일별=KRX(T+1), KRX 게시 전=Yahoo ^KQ11")
_add("sp500", "S&P 500", "equity", "equity", "index", 2, "fred", "confirmed", delta_unit="pct",
     note="일별=FRED")
_add("nasdaq", "NASDAQ", "equity", "equity", "index", 2, "fred", "confirmed", delta_unit="pct")
_add("russell2000", "Russell 2000", "equity", "equity", "index", 2, "yahoo", "unofficial", delta_unit="pct")
_add("vix", "VIX", "equity", "equity", "index", 2, "fred", "confirmed", delta_unit="abs")

SERIES: tuple[SeriesDef, ...] = tuple(_S)
BY_KEY: dict[str, SeriesDef] = {s.key: s for s in SERIES}
assert len(BY_KEY) == len(SERIES), "series key 중복"
