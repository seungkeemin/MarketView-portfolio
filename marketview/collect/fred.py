"""FRED (세인트루이스 연준). 결측은 value 가 "." 로 온다 → Missing 이지 0 이 아니다."""

from __future__ import annotations

from datetime import date, datetime

from ..config import sources as srcs
from ..config.settings import as_ymd, env
from .base import Failed, Fetched, FetchResult, Missing, get_json, scrub, to_float

BASE = "https://api.stlouisfed.org/fred"
PROVIDER = "fred"


def parse_observations(rows: list[dict], series_key: str) -> list[Fetched]:
    out: list[Fetched] = []
    for row in rows:
        value = to_float(row.get("value"))
        if value is None:
            continue
        try:
            obs = datetime.strptime(str(row.get("date", "")), "%Y-%m-%d").date()
        except ValueError:
            continue
        out.append(Fetched(series_key, value, obs, PROVIDER))
    return out


def fetch_range(series_key: str, start: date, end: date) -> list[FetchResult]:
    spec = srcs.FRED_SERIES.get(series_key)
    if spec is None:
        return [Missing(series_key, "FRED 시리즈 미확인")]
    key = env("FRED_API_KEY")
    if not key:
        return [Failed(series_key, "FRED_API_KEY 없음")]
    try:
        payload = get_json(f"{BASE}/series/observations", params={
            "series_id": spec.series_id, "api_key": key, "file_type": "json",
            "observation_start": as_ymd(start), "observation_end": as_ymd(end), "sort_order": "asc"})
        if not isinstance(payload, dict) or "error_message" in payload:
            raise RuntimeError(str(payload.get("error_message", "예상 밖 응답")) if isinstance(payload, dict)
                               else "예상 밖 응답")
    except Exception as exc:  # noqa: BLE001
        return [Failed(series_key, scrub(str(exc)))]
    parsed = parse_observations(payload.get("observations") or [], series_key)
    return parsed or [Missing(series_key, f"{spec.series_id} {start}~{end} 게시분 없음")]
