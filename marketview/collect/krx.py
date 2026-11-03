"""KRX Data Marketplace — 국채선물 일별매매정보.

  GET https://data-dbg.krx.co.kr/svc/apis/drv/fut_bydd_trd?basDd=YYYYMMDD   Header: AUTH_KEY

PROD_NM 은 정확히 일치시킨다 ('10년국채 선물' 과 '30년국채 선물' 은 한 글자 차이, 별개 상품
'3년-10년국채선물스프레드 선물' 도 있다). 한 상품 안에는 결제월별 종목과 캘린더 스프레드(ISU_NM 에 'SP')가
섞여 오는데, 스프레드를 먼저 빼고 정규장 중 거래량 최대 행(= 최근월물)을 고른다. 휴장일은 0행이다.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from ..config import sources as srcs
from ..config.settings import as_compact, env
from .base import Failed, Fetched, FetchResult, Missing, get_json, scrub, to_float

BASE = "https://data-dbg.krx.co.kr/svc/apis"
PROVIDER = "krx"
# 한 번의 범위 수집이 쓸 수 있는 최대 시간. 호출당 제한(15초 × 재시도 1회)만 있으면 KRX 가 느린 날 평일마다 30초씩 걸려
# 화면의 '수집 중' 이 몇 분씩 이어진다(2026-10-14 실제로 80초 넘게 멈춤). 넘기면 남은 날짜는 다음 수집에서 다시 받는다.
BUDGET_SECONDS = 40.0


def fetch_day_rows(day: date, endpoint: str | None = None) -> list[dict]:
    key = env("KRX_AUTH_KEY")
    if not key:
        raise RuntimeError("KRX_AUTH_KEY 없음")
    payload = get_json(f"{BASE}/{endpoint or srcs.KRX_FUTURES_ENDPOINT}", headers={"AUTH_KEY": key},
                       params={"basDd": as_compact(day)})
    rows = payload.get("OutBlock_1") if isinstance(payload, dict) else None
    if rows is None:
        raise RuntimeError("OutBlock_1 없음")
    return [r for r in rows if isinstance(r, dict)]


def is_calendar_spread(row: dict) -> bool:
    return "SP" in str(row.get("ISU_NM", "")).split()


def extract(rows: list[dict], day: date) -> list[Fetched]:
    """한 영업일 응답에서 srcs.KRX_SERIES 전부를 뽑는다."""
    out: list[Fetched] = []
    for key, spec in srcs.KRX_SERIES.items():
        matched = [r for r in rows
                   if str(r.get("PROD_NM", "")).strip() == spec.product_name
                   and str(r.get("MKT_NM", "")).strip() in {"", "정규"}
                   and not is_calendar_spread(r)]
        if not matched:
            continue
        front = max(matched, key=lambda r: to_float(r.get("ACC_TRDVOL")) or 0.0)
        value = to_float(front.get(spec.value_field))
        if value is None:
            continue
        try:
            obs = datetime.strptime(str(front.get("BAS_DD", "")).strip(), "%Y%m%d").date()
        except ValueError:
            obs = day
        out.append(Fetched(key, value, obs, PROVIDER, note=str(front.get("ISU_NM", "")).strip() or None))
    return out


def extract_index(rows: list[dict], key: str, day: date) -> Fetched | None:
    """지수 응답에서 IDX_NM 정확일치 행의 종가. 빈 문자열·없음은 None (0 으로 채우지 않는다)."""
    spec = srcs.KRX_INDEX_SERIES[key]
    for r in rows:
        if str(r.get("IDX_NM", "")).strip() == spec.index_name:
            value = to_float(r.get(spec.value_field))
            if value is None:
                return None
            try:
                obs = datetime.strptime(str(r.get("BAS_DD", "")).strip(), "%Y%m%d").date()
            except ValueError:
                obs = day
            return Fetched(key, value, obs, PROVIDER, note=spec.index_name)
    return None


def fetch_index_range(key: str, start: date, end: date) -> tuple[list[Fetched], list[FetchResult]]:
    """지수 하나를 [start, end] 평일마다 호출. 휴장일은 0행(정상)."""
    endpoint = srcs.KRX_INDEX_ENDPOINTS[srcs.KRX_INDEX_SERIES[key].endpoint]
    fetched: list[Fetched] = []
    problems: list[FetchResult] = []
    day = start
    deadline = time.monotonic() + BUDGET_SECONDS
    while day <= end:
        if day.weekday() < 5:
            if time.monotonic() > deadline:
                problems.append(Failed("krx", f"{day}~: 시간 초과(예산 {BUDGET_SECONDS:.0f}초) — 다음 수집에서 이어서 받음"))
                break
            try:
                got = extract_index(fetch_day_rows(day, endpoint), key, day)
                if got:
                    fetched.append(got)
            except Exception as exc:  # noqa: BLE001
                problems.append(Failed("krx", f"{day}: {scrub(str(exc))}"))
                if len(problems) >= 5:
                    break
        day += timedelta(days=1)
    return fetched, problems


def fetch_range(start: date, end: date) -> tuple[list[Fetched], list[FetchResult]]:
    """[start, end] 의 평일마다 호출. (수집된 값, 실패/없음 요약) 을 돌려준다. 휴장일 0행은 정상이다."""
    fetched: list[Fetched] = []
    problems: list[FetchResult] = []
    day = start
    deadline = time.monotonic() + BUDGET_SECONDS
    while day <= end:
        if day.weekday() < 5:
            if time.monotonic() > deadline:
                problems.append(Failed("krx", f"{day}~: 시간 초과(예산 {BUDGET_SECONDS:.0f}초) — 다음 수집에서 이어서 받음"))
                break
            try:
                fetched += extract(fetch_day_rows(day), day)
            except Exception as exc:  # noqa: BLE001
                problems.append(Failed("krx", f"{day}: {scrub(str(exc))}"))
                if len(problems) >= 5:           # 키/권한 문제면 계속 두드리지 않는다
                    break
        day += timedelta(days=1)
    return fetched, problems
