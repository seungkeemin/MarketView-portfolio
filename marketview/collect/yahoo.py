"""Yahoo Finance 비공식 chart 엔드포인트. 언제든 깨질 수 있다 → 심볼 단위로 격리하고 실패를 그대로 보고한다.

  GET https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=…&interval=…
  (2026-10-09 14개 심볼 200 확인)

obs_date 는 거래소 현지 날짜(meta.gmtoffset 적용)다. 일별 종가(kind=daily)와 같은 날짜 체계라 맞물린다.
장중 스냅샷 수집은 2026-10-14 에 없앴다(일별 종가만 쓴다).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from ..config import sources as srcs
from .base import Failed, Fetched, FetchResult, Missing, get_json, scrub, to_float

BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
PROVIDER = "yahoo"


def _chart(symbol: str, **params: str) -> dict:
    payload = get_json(f"{BASE}/{quote(symbol, safe='')}", params=params)
    result = (payload.get("chart") or {}).get("result") if isinstance(payload, dict) else None
    if not result:
        raise RuntimeError("chart.result 없음")
    return result[0]


def _local_date(ts: int, gmtoffset: int) -> date:
    return (datetime.fromtimestamp(ts, timezone.utc) + timedelta(seconds=gmtoffset)).date()


def parse_daily(result: dict, series_key: str, now_ts: int | None = None) -> list[Fetched]:
    """일별 종가. 아직 장이 안 끝난 오늘 봉은 종가가 아니므로 버린다."""
    meta = result.get("meta") or {}
    off = int(meta.get("gmtoffset", 0))
    stamps = result.get("timestamp") or []
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    end_of_session = ((meta.get("currentTradingPeriod") or {}).get("regular") or {}).get("end")
    now_ts = now_ts or int(datetime.now(timezone.utc).timestamp())
    today_local = _local_date(now_ts, off)
    session_over = end_of_session is not None and now_ts >= int(end_of_session)

    out: list[Fetched] = []
    for ts, close in zip(stamps, closes):
        value = to_float(close)
        if value is None:
            continue
        day = _local_date(int(ts), off)
        if day == today_local and not session_over:
            continue
        out.append(Fetched(series_key, value, day, PROVIDER))
    # 같은 날짜가 두 번 오면 마지막 값을 쓴다
    return list({f.obs_date: f for f in out}.values())


def fetch_daily(series_key: str, start: date) -> list[FetchResult]:
    spec = srcs.YAHOO_SERIES.get(series_key)
    if spec is None:
        return [Missing(series_key, "Yahoo 일별 대상 아님")]
    try:
        result = _chart(spec.symbol, period1=str(int(datetime.combine(start, datetime.min.time(),
                                                                       timezone.utc).timestamp())),
                        period2=str(int(datetime.now(timezone.utc).timestamp()) + 86400), interval="1d")
    except Exception as exc:  # noqa: BLE001
        return [Failed(series_key, scrub(str(exc)))]
    rows = [f for f in parse_daily(result, series_key) if f.obs_date >= start]
    return list(rows) or [Missing(series_key, f"{spec.symbol} 일별 데이터 없음")]
