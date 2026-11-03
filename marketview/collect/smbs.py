"""서울외국환중개 KRW IRS (http 전용).

표 값이 <script>d1('%_Z33%_Z2e…')</script> 처럼 16진 문자코드로 인코딩돼 있다
(%_Xhh = 문자코드 hh, %u_Xhhhh = 유니코드). 디코딩 규칙은 2026-10-09 실제 응답으로 확인했다
(예: %_Z59 → 'Y', %_Z2e → '.', %u_Db144 → '년').
열은 기일물 / Receive / Mid / Pay 이고 Mid 를 쓴다.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from ..config import sources as srcs
from .base import Failed, Fetched, FetchResult, Missing, get, scrub, to_float

PROVIDER = "smbs"
_ENC = re.compile(r"%u_\w(\w{4})|%_\w(\w\w)")
_CALL = re.compile(r"d\d\(\s*'([^']*)'\s*\)")


def decode(text: str) -> str:
    return _ENC.sub(lambda m: chr(int(m.group(1) or m.group(2), 16)), text)


def parse_irs(html: str) -> tuple[date, dict[str, float]] | None:
    """(표 기준일, {'1Y': Mid, ...}). 표를 못 찾으면 None."""
    marker = html.find("IRS RATE 조회 결과 표")
    if marker < 0:
        return None
    # 표 제목 옆 기준일 'YYYY년 MM월 DD일' (h4 안의 인코딩된 조각들을 이어 붙인다)
    head = html[:marker]
    h4 = head[head.rfind('<h4 class="fl">'):] if '<h4 class="fl">' in head else ""
    stamp = decode("".join(_CALL.findall(h4)))
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", stamp)
    if not m:
        # 표 제목은 있는데 기준일을 못 읽으면 페이지 구조가 바뀐 것이다 — 휴장일의 '표 없음'(None → Missing)과 구분해 Failed 로 올린다
        raise ValueError("IRS 표 기준일을 읽지 못했습니다 (페이지 구조 변경 가능성)")
    table_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    body = html[marker:]
    body = body[body.find("<tbody"):body.find("</tbody>")]
    mids: dict[str, float] = {}
    for row in re.findall(r"<tr>(.*?)</tr>", body, flags=re.S):
        cells = [decode(c) for c in _CALL.findall(row)]
        if len(cells) == 4 and cells[0] in srcs.SMBS_IRS_TENORS:
            mid = to_float(cells[2])            # [기일물, Receive, Mid, Pay]
            if mid is not None:
                mids[cells[0]] = mid
    return (table_date, mids) if mids else None


def _query(day: date) -> dict[str, str]:
    """과거 날짜 조회는 폼 필드 12개를 모두 넘겨야 먹는다 (2026-10-09 확인)."""
    start, end = day - timedelta(days=180), day
    return {
        "StrSch_sYear": f"{start:%Y}", "StrSch_sMonth": f"{start:%m}", "StrSch_sDay": f"{start:%d}",
        "StrSch_eYear": f"{end:%Y}", "StrSch_eMonth": f"{end:%m}", "StrSch_eDay": f"{end:%d}",
        "StrSch_Year": f"{day:%Y}", "StrSch_Month": f"{day:%m}", "StrSch_Day": f"{day:%d}",
        "StrSchFull": f"{start:%Y.%m.%d}", "StrSchFull2": f"{end:%Y.%m.%d}", "StrSchFull3": f"{day:%Y.%m.%d}",
    }


def to_fetched(table_date: date, mids: dict[str, float]) -> list[Fetched]:
    return [Fetched(f"irs_{tenor[:-1]}y", value, table_date, PROVIDER)
            for tenor, value in mids.items()]


def fetch_day(day: date | None = None) -> list[FetchResult]:
    """day=None 이면 오늘 화면(가장 최근 마감치). 표 기준일이 요청일과 다르면 표의 날짜를 따른다."""
    try:
        response = get(srcs.SMBS_IRS_URL, params=_query(day) if day else None)
        parsed = parse_irs(response.content.decode("euc-kr", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return [Failed("irs", scrub(str(exc)))]
    if parsed is None:
        return [Missing("irs", f"{day or '최신'} IRS 표 없음")]
    table_date, mids = parsed
    return list(to_fetched(table_date, mids))


def fetch_range(start: date, end: date) -> list[FetchResult]:
    out: list[FetchResult] = []
    seen: set[date] = set()
    day = start
    fails = 0
    while day <= end:
        if day.weekday() < 5:
            results = fetch_day(day)
            for r in results:
                if isinstance(r, Fetched):
                    if r.obs_date in seen and r.obs_date != day:
                        continue                    # 휴장일 요청이 직전 영업일 표를 돌려준 경우의 중복
                    out.append(r)
            fails = fails + 1 if any(isinstance(r, Failed) for r in results) else 0      # 연속 실패만 센다(띄엄띄엄 실패로 백필이 끊기지 않게)
            seen.update(r.obs_date for r in results if isinstance(r, Fetched))
            if fails >= 5:
                out.append(Failed("irs", "연속 실패 5회 — 중단"))
                break
        day += timedelta(days=1)
    return out or [Missing("irs", f"{start}~{end} IRS 게시분 없음")]
