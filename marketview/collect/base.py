"""수집 결과 타입과 HTTP 헬퍼.

소스 함수는 예외를 밖으로 던지지 않고 Fetched / Missing / Failed 중 하나를 돌려준다.
Missing(소스는 정상인데 값이 없음)과 Failed(호출 실패)를 섞지 않는다.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

TIMEOUT = httpx.Timeout(15.0, connect=10.0)
RETRIES = 1
UA = "Mozilla/5.0 (MarketView personal research tool)"


@dataclass(frozen=True)
class Fetched:
    series_key: str
    value: float
    obs_date: date
    provider: str
    obs_ts: str = ""        # 장중 스냅샷의 관측 시각 (KST ISO). 일별은 ''
    note: str | None = None


@dataclass(frozen=True)
class Missing:
    series_key: str
    reason: str


@dataclass(frozen=True)
class Failed:
    series_key: str
    message: str


FetchResult = Fetched | Missing | Failed

# URL 쿼리/경로의 키가 예외 메시지에 새지 않게 한다 (FRED api_key=…, ECOS 경로의 키).
_SECRET = re.compile(r"(api_key=)[^&\s]+|(/api/\w+/)[^/\s]+(/json)", re.I)


def scrub(text: str) -> str:
    return _SECRET.sub(lambda m: (m.group(1) or m.group(2)) + "***" + (m.group(3) or ""), text)


def get(url: str, *, headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None, timeout: httpx.Timeout = TIMEOUT, retries: int = RETRIES) -> httpx.Response:
    """GET. 기본 1회 재시도. 실패는 키를 가린 RuntimeError 로 던진다."""
    last: Exception | None = None
    hdrs = {"User-Agent": UA, **(headers or {})}
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(min(2.0 * attempt, 5.0))     # 바로 다시 두드리면 같은 순간 장애에 또 걸린다
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                response = client.get(url, headers=hdrs, params=params)
                response.raise_for_status()
                return response
        except Exception as exc:  # noqa: BLE001 - 경계에서 전부 잡는 것이 의도다
            last = exc
    raise RuntimeError(describe(last))


def get_json(url: str, **kwargs: Any) -> Any:
    return get(url, **kwargs).json()


def describe(exc: Exception | None) -> str:
    if exc is None:
        return "알 수 없는 오류"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.ConnectTimeout):
        return "연결 타임아웃"          # 연결 단계(TCP·TLS)가 막힌 것 — 읽기 제한을 늘려도 소용없다
    if isinstance(exc, httpx.ReadTimeout):
        return "읽기 타임아웃"
    if isinstance(exc, httpx.TimeoutException):
        return "타임아웃"
    if isinstance(exc, httpx.TransportError):
        return f"연결 실패: {type(exc).__name__}"
    return scrub(f"{type(exc).__name__}: {exc}")


def to_float(raw: Any) -> float | None:
    """결측 표기('.', '-', '', NA)는 None. 0 으로 채우지 않는다."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if text in {"", ".", "-", "NA", "N/A", "null", "None", "nan"}:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None     # inf/-inf 도 결측(0 으로 채우지 않음)
