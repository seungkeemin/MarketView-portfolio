"""화면 구성: 탭 · 그룹 · 타일 · 커브 패밀리. 데이터가 아니라 '무엇을 어디에 보여주나' 이다.

여기에 없는 항목은 화면·리포트·검색에 나오지 않는다. (2026-10-10 사용자 결정: KRW IRS 는 1·3·5·10년만, USD 스왑·
국내 크레딧 섹터 수익률/스프레드는 제외. IRS 는 소스가 같은 페이지로 주기 때문에 수집은 전 만기를 계속 저장한다.)
"""

from __future__ import annotations

from dataclasses import dataclass

PROVIDER_LABEL = {
    "ecos": "ECOS", "fred": "FRED", "krx": "KRX", "smbs": "서울외국환중개",
    "yahoo": "Yahoo(비공식)", "user": "수동 입력", "derived": "파생",
}
# 게시 지연 (2026-10-09 실측, marketview/config/sources.py 상단 참조)
PROVIDER_LAG = {
    "ecos": "T+0~1", "fred": "T+1~2", "krx": "T+1", "smbs": "T+1", "yahoo": "", "user": "", "derived": "",
}
# 원본이 % 인데 화면에는 bp 로 보여주는 항목 (OAS 0.82 = 82bp). 값에 곱할 배율.
SCALE_TO_BP = {"us_ig_oas": 100.0, "us_hy_oas": 100.0}

IRS_TENORS = (1, 3, 5, 10)

TABS: dict[str, dict] = {
    "bond": {
        "label": "채권",
        "default_sel": ["ktb_3y", "ktb_10y", "ktb_10s3s"],
        "groups": [
            {"id": "kr", "label": "국내 단기", "tiles": ["bok_base_rate", "kofr", "cd_91d", "cp_91d", "cd_kofr", "kr_cds_5y"]},
            {"id": "ktb", "label": "국고채", "tiles": ["ktb_3y", "ktb_5y", "ktb_10y", "ktb_30y", "ktb_10s3s", "ktb_30s10s"]},
            {"id": "ktbf", "label": "국채선물", "tiles": ["ktbf_3y", "ktbf_3y_iy", "ktbf_10y", "ktbf_10y_iy", "ktbf_30y", "ktbf_30y_iy",
                                                      "ktbf_3y_oi", "ktbf_10y_oi"]},
            {"id": "irs", "label": "KRW IRS", "tiles": [f"irs_{t}y" for t in IRS_TENORS]},
            {"id": "us", "label": "미국", "tiles": ["sofr", "ust_2y", "ust_5y", "ust_10y", "ust_30y", "ust_10s2s", "ust_30s10s"]},
            {"id": "us_x", "label": "미국 신용·선물", "tiles": ["us_ig_oas", "us_hy_oas", "wti_fut", "wti", "ust_fut_2y", "ust_fut_5y",
                                                       "ust_fut_10y", "ust_fut_30y"]},
        ],
    },
    "fx": {
        "label": "외환",
        "default_sel": ["usdkrw"],
        "groups": [{"id": "fx", "label": "", "tiles": ["usdkrw", "ndf_1m", "usdjpy", "eurusd", "dxy"]}],
    },
    "credit": {
        "label": "신용",
        "default_sel": ["corp_aa3_spread_3y"],
        "groups": [
            {"id": "corp", "label": "회사채(ECOS)", "tiles": ["corp_aa3_3y", "corp_bbb3_3y", "corp_aa3_spread_3y", "corp_bbb3_spread_3y"]},
        ],
    },
    "equity": {
        "label": "주식",
        "default_sel": ["kospi"],
        "groups": [{"id": "equity", "label": "", "tiles": ["kospi", "kosdaq", "sp500", "nasdaq", "russell2000", "vix"]}],
    },
}


@dataclass(frozen=True)
class CurveFamily:
    id: str
    label: str
    keys: tuple[str, ...]
    unit: str          # pct | price
    src: str


CURVES: dict[str, CurveFamily] = {c.id: c for c in (
    CurveFamily("ktb", "국고채", ("ktb_1y", "ktb_3y", "ktb_5y", "ktb_10y", "ktb_20y", "ktb_30y"), "pct", "ECOS"),
    CurveFamily("ktbf", "국채선물 내재", ("ktbf_3y_iy", "ktbf_10y_iy", "ktbf_30y_iy"), "pct", "KRX"),
    CurveFamily("irs", "KRW IRS", tuple(f"irs_{t}y" for t in IRS_TENORS), "pct", "서울외국환중개"),
    CurveFamily("ust", "UST", ("ust_1m", "ust_3m", "ust_6m", "ust_1y", "ust_2y", "ust_3y", "ust_5y", "ust_7y", "ust_10y",
                               "ust_20y", "ust_30y"), "pct", "FRED"),
    CurveFamily("ust_fut", "미국채선물(가격)", ("ust_fut_2y", "ust_fut_5y", "ust_fut_10y", "ust_fut_30y"), "price", "Yahoo(비공식)"),
)}
CURVE_LOOKBACK_DAYS = 7     # 비교일에 값이 없으면 이 기간 안에서 직전 관측을 쓴다 (휴장일·지연 게시)
