"""한국은행 ECOS OpenAPI.

URL 은 쿼리스트링이 아니라 경로에 값을 끼운다 (인증키도 경로에 들어간다 → 로그·예외에서 가린다):
  https://ecos.bok.or.kr/api/StatisticSearch/{키}/json/kr/{시작}/{끝}/{통계표}/{주기}/{시작일}/{종료일}/{항목}
"""

from __future__ import annotations

from datetime import date, datetime

from ..config import sources as srcs
from ..config.settings import as_compact, env
from .base import Failed, Fetched, FetchResult, Missing, get_json, scrub, to_float

BASE = "https://ecos.bok.or.kr/api"
PROVIDER = "ecos"
PAGE = 1000


class EcosError(RuntimeError):
    pass


def call(service: str, *segments: str, start: int = 1, end: int = 100) -> list[dict]:
    key = env("ECOS_API_KEY")
    if not key:
        raise EcosError("ECOS_API_KEY 없음")
    payload = get_json("/".join([BASE, service, key, "json", "kr", str(start), str(end), *segments]))
    if isinstance(payload, dict) and "RESULT" in payload:
        result = payload["RESULT"]
        if str(result.get("CODE")) == "INFO-200":      # 해당 데이터 없음 — 오류가 아니다
            return []
        raise EcosError(f"{result.get('CODE')} {str(result.get('MESSAGE', '')).strip()}")
    body = payload.get(service) if isinstance(payload, dict) else None
    if not isinstance(body, dict):
        raise EcosError("예상 밖 응답 구조")
    return [r for r in (body.get("row") or []) if isinstance(r, dict)]


def parse_rows(rows: list[dict], series_key: str) -> list[Fetched]:
    out: list[Fetched] = []
    for row in rows:
        text = str(row.get("TIME", "")).strip()
        value = to_float(row.get("DATA_VALUE"))
        if value is None or len(text) != 8:
            continue
        try:
            obs = datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            continue
        out.append(Fetched(series_key, value, obs, PROVIDER))
    return out


def fetch_range(series_key: str, start: date, end: date) -> list[FetchResult]:
    spec = srcs.ECOS_SERIES.get(series_key)
    if spec is None:
        return [Missing(series_key, "ECOS 코드 미확인")]
    try:
        rows: list[dict] = []
        first = 1
        while True:                                    # 1000행 상한 → 페이지를 이어 받는다
            page = call("StatisticSearch", spec.stat_code, spec.cycle, as_compact(start), as_compact(end),
                        spec.item_code, start=first, end=first + PAGE - 1)
            rows += page
            if len(page) < PAGE:
                break
            first += PAGE
    except Exception as exc:  # noqa: BLE001
        return [Failed(series_key, scrub(str(exc)))]
    parsed = parse_rows(rows, series_key)
    return parsed or [Missing(series_key, f"{spec.stat_code} {start}~{end} 게시분 없음")]
