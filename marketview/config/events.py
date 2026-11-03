"""이벤트 일정의 출처. 날짜·시각은 공식 페이지에서 확인한 것만 적는다 (추측 금지, CLAUDE.md 3).

확인일은 verified_on. 일정은 바뀔 수 있으므로 출처 URL 을 함께 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OfficialEvent:
    ext_key: str
    kind: str
    title: str
    region: str
    importance: int
    local_date: str
    local_time: str | None     # None = 공식 페이지에 시각이 없다
    tz: str
    source_url: str
    verified_on: str
    note: str = ""


# --- FOMC -----------------------------------------------------------------
# 확인 2026-10-09 https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
#   2026: Jan 27-28, Mar 17-18*, Apr 28-29, Jun 16-17*, Jul 28-29, Sep 15-16*, Oct 27-28, Dec 8-9*
#   2027: Jan 26-27, Mar 16-17*, Apr 27-28, Jun 8-9*, Jul 27-28, Sep 14-15*, Oct 26-27, Dec 7-8*
#   페이지 문구: "Each meeting date is tentative until confirmed at the meeting immediately preceding it."
# 결정 발표 시각: 연준 보도자료 'For release at 2:00 p.m. EDT September 16, 2026'
#   (https://www.federalreserve.gov/monetarypolicy/files/monetary20260916a1.pdf) — 회의 둘째 날.
# 의사록(minutes) 공개일은 페이지에 9월 회의분(2026-10-07)까지만 있고 10월·12월분은 없다 → 만들지 않는다.
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_DECISION_DAYS = (
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08",
)

# --- 한국은행 금융통화위원회 통화정책방향 결정회의 ---------------------------------
# 확인 2026-10-09 https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do?mtgSe=A&menuNo=200755
#   2026: 01-15, 02-26, 04-10, 05-28, 07-16, 08-27, 10-22, 11-26 (공식 표에는 날짜만 있고 시각은 없다)
BOK_URL = "https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do?mtgSe=A&menuNo=200755"
BOK_DAYS = ("2026-01-15", "2026-02-26", "2026-04-10", "2026-05-28", "2026-07-16", "2026-08-27", "2026-10-22", "2026-11-26")

VERIFIED_ON = "2026-10-09"


def official_events() -> list[OfficialEvent]:
    out: list[OfficialEvent] = []
    for d in FOMC_DECISION_DAYS:
        out.append(OfficialEvent(f"fomc:{d}", "fomc", "FOMC 금리 결정", "US", 3, d, "14:00", "America/New_York",
                                 FOMC_URL, VERIFIED_ON, "2027년 일정은 잠정" if d.startswith("2027") else ""))
    for d in BOK_DAYS:
        out.append(OfficialEvent(f"bok:{d}", "bok", "금융통화위원회 기준금리 결정", "KR", 3, d, None, "Asia/Seoul",
                                 BOK_URL, VERIFIED_ON, "공식 표에 시각 없음"))
    return out


# --- FRED 발표일 API (release_id 는 fred/releases 목록의 이름 일치로 확인, 2026-10-09) -----------------
#   10 Consumer Price Index · 50 Employment Situation · 54 Personal Income and Outlays · 53 Gross Domestic Product
# 발표 시각 08:30 ET 확인: BLS CPI 일정(https://www.bls.gov/schedule/news_release/cpi.htm → "08:30 AM"),
#   BLS Employment Situation 일정(…/empsit.htm → "08:30 AM"), BEA 일정(https://www.bea.gov/news/schedule →
#   GDP·Personal Income and Outlays "8:30 a.m.").
# 발표일 자체는 FRED 가 알려주는 값을 쓴다 (BLS·BEA 일정과 10-14 CPI, 10-29 GDP·PCE, 11-06 고용이 일치함을 확인).
@dataclass(frozen=True)
class FredRelease:
    release_id: int
    kind: str
    title: str
    importance: int
    local_time: str
    source_url: str


FRED_RELEASES = (
    FredRelease(10, "us_cpi", "미국 CPI", 3, "08:30", "https://www.bls.gov/schedule/news_release/cpi.htm"),
    FredRelease(50, "us_jobs", "미국 고용보고서(비농업)", 3, "08:30", "https://www.bls.gov/schedule/news_release/empsit.htm"),
    FredRelease(54, "us_pce", "미국 PCE(개인소득·지출)", 2, "08:30", "https://www.bea.gov/news/schedule"),
    FredRelease(53, "us_gdp", "미국 GDP", 2, "08:30", "https://www.bea.gov/news/schedule"),
)
FRED_WINDOW_DAYS = 150

# 자동 소스를 못 찾은 일정 (사용자가 직접 추가한다):
#   - 국고채 입찰일: 재정경제부 월별 발행계획 첨부(PDF, 이미지 압축)에서 자동 판독 실패 (2026-10-09)
#   - 한국 CPI(통계청), BOJ, ECB
