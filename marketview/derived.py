"""파생값 레지스트리. 저장하지 않고 조회할 때 계산한다. 문자열 eval 금지.

구성 값이 하나라도 없는 날짜는 결과에서 빠진다 (None/미수집 — 0 으로 채우지 않는다).
날짜는 정확히 같은 obs_date 끼리만 맞춘다: 관측일이 다른 값을 짝지어 스프레드를 만들지 않는다.
금리 차이는 (a - b) * 100 으로 bp. OAS 는 원본이 % 라 표시 단위 변환만 한다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from .config.settings import today_kst
from .futures import implied_yield


@dataclass(frozen=True)
class Derived:
    key: str
    label: str
    inputs: tuple[str, ...]
    fn: Callable[..., float | None]
    unit: str          # bp | pct
    tab: str


def _bp(a: float, b: float) -> float:
    return round((a - b) * 100, 6)


def _fut_iy(years: int) -> Callable[[float], float | None]:
    return lambda price: implied_yield(price, years)


REGISTRY: dict[str, Derived] = {d.key: d for d in (
    Derived("cd_kofr", "CD−KOFR", ("cd_91d", "kofr"), _bp, "bp", "bond"),
    Derived("ktb_10s3s", "국고 10−3", ("ktb_10y", "ktb_3y"), _bp, "bp", "bond"),
    Derived("ktb_30s10s", "국고 30−10", ("ktb_30y", "ktb_10y"), _bp, "bp", "bond"),
    Derived("ust_10s2s", "UST 10−2", ("ust_10y", "ust_2y"), _bp, "bp", "bond"),
    Derived("ust_30s10s", "UST 30−10", ("ust_30y", "ust_10y"), _bp, "bp", "bond"),
    Derived("ktbf_3y_iy", "국채선물 3년 내재수익률", ("ktbf_3y",), _fut_iy(3), "pct", "bond"),
    Derived("ktbf_10y_iy", "국채선물 10년 내재수익률", ("ktbf_10y",), _fut_iy(10), "pct", "bond"),
    Derived("ktbf_30y_iy", "국채선물 30년 내재수익률", ("ktbf_30y",), _fut_iy(30), "pct", "bond"),
    # 신용 스프레드: 같은 만기(3년) 국고채 대비. 국내 크레딧 섹터(공사채·은행채 등)는 다루지 않는다 (2026-10-10 사용자 결정)
    Derived("corp_aa3_spread_3y", "회사채 AA- 3Y 스프레드", ("corp_aa3_3y", "ktb_3y"), _bp, "bp", "credit"),
    Derived("corp_bbb3_spread_3y", "회사채 BBB- 3Y 스프레드", ("corp_bbb3_3y", "ktb_3y"), _bp, "bp", "credit"),
)}
assert all(len(set(d.inputs)) == len(d.inputs) for d in REGISTRY.values())


def compute(key: str, inputs_by_date: dict[str, dict[str, float]]) -> list[tuple[str, float]]:
    """{obs_date: {입력키: 값}} → [(obs_date, 파생값)] 날짜순. 입력이 모자란 날짜는 건너뛴다."""
    spec = REGISTRY[key]
    out: list[tuple[str, float]] = []
    for day in sorted(inputs_by_date):
        values = inputs_by_date[day]
        if not all(k in values for k in spec.inputs):
            continue
        result = spec.fn(*(values[k] for k in spec.inputs))
        if result is not None:
            out.append((day, result))
    return out


def load_daily(conn: sqlite3.Connection, keys: tuple[str, ...], start: str | None = None,
               end: str | None = None) -> dict[str, dict[str, float]]:
    """일별 종가(kind='daily')를 {obs_date: {series_key: value}} 로."""
    marks = ",".join("?" * len(keys))
    sql = f"SELECT obs_date, series_key, value FROM observation WHERE kind='daily' AND series_key IN ({marks})"
    args: list[str] = list(keys)
    if start:
        sql += " AND obs_date >= ?"
        args.append(start)
    if end:
        sql += " AND obs_date <= ?"
        args.append(end)
    table: dict[str, dict[str, float]] = {}
    for row in conn.execute(sql, args):
        table.setdefault(row["obs_date"], {})[row["series_key"]] = row["value"]
    return table


def series(conn: sqlite3.Connection, key: str, start: str | None = None,
           end: str | None = None) -> list[tuple[str, float]]:
    spec = REGISTRY[key]
    return compute(key, load_daily(conn, spec.inputs, start, end))


RECENT_WINDOW_DAYS = 45


def recent(conn: sqlite3.Connection, key: str, end: str | None = None, need: int = 2) -> list[tuple[str, float]]:
    """최근 값 몇 개만 필요할 때(타일·기본 기준일). 최근 구간만 계산하고 need 개에 못 미치면 전체 이력으로 되돌아간다.
    전체 이력을 매번 계산하면 내재수익률처럼 반복 계산이 큰 파생값이 타일마다 수천 번 돈다. 결과는 series() 의 꼬리와 같다."""
    anchor = date.fromisoformat(end) if end else today_kst()
    pts = series(conn, key, start=(anchor - timedelta(days=RECENT_WINDOW_DAYS)).isoformat(), end=end)
    return pts if len(pts) >= need else series(conn, key, end=end)
