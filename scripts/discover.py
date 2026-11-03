"""'확인 필요' 소스를 실제로 호출해 확인하는 탐색 스크립트 (Phase 1). 키는 출력하지 않는다.

  .venv\\Scripts\\python scripts\\discover.py fred
  .venv\\Scripts\\python scripts\\discover.py yahoo
  .venv\\Scripts\\python scripts\\discover.py smbs
  .venv\\Scripts\\python scripts\\discover.py krx
  .venv\\Scripts\\python scripts\\discover.py ecos <키워드>
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marketview.collect.base import get, get_json, scrub, to_float  # noqa: E402
from marketview.config.settings import as_compact, env  # noqa: E402

FRED_CANDIDATES = [
    "DGS1MO", "DGS6MO", "DGS1", "DGS3", "DGS7", "DGS20",
    "SP500", "NASDAQCOM", "NASDAQ100", "RU2000PR", "DCOILWTICO", "DTWEXBGS",
]
YAHOO_CANDIDATES = [
    "^KS11", "^GSPC", "^IXIC", "^RUT", "DX-Y.NYB", "CL=F", "KRW=X",
    "JPY=X", "EURUSD=X", "^VIX", "ZN=F", "ZB=F", "ZT=F", "ZF=F",
]


def fred() -> None:
    key = env("FRED_API_KEY")
    for sid in FRED_CANDIDATES:
        try:
            meta = get_json("https://api.stlouisfed.org/fred/series",
                            params={"series_id": sid, "api_key": key, "file_type": "json"})["seriess"][0]
            obs = get_json("https://api.stlouisfed.org/fred/series/observations",
                           params={"series_id": sid, "api_key": key, "file_type": "json",
                                   "sort_order": "desc", "limit": 8})["observations"]
            good = [o for o in obs if to_float(o["value"]) is not None]
            last = good[0] if good else None
            print(f"{sid:12} {meta['title'][:52]:52} | {meta['units_short']:10} | "
                  f"{meta['frequency_short']} | 최신 {last['date'] if last else '-'} "
                  f"= {last['value'] if last else '-'} | 시리즈갱신 {meta['last_updated'][:16]}")
        except Exception as exc:  # noqa: BLE001
            print(f"{sid:12} 실패: {scrub(str(exc))}")


def yahoo() -> None:
    for sym in YAHOO_CANDIDATES:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        try:
            data = get_json(url, params={"range": "5d", "interval": "1h"})
            res = data["chart"]["result"][0]
            meta = res["meta"]
            closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
            print(f"{sym:10} {meta.get('shortName', meta.get('symbol'))!s:28} "
                  f"{meta['regularMarketPrice']!s:>10} {meta.get('currency')} "
                  f"bars={len(closes)} marketTime={meta['regularMarketTime']}")
        except Exception as exc:  # noqa: BLE001
            print(f"{sym:10} 실패: {scrub(str(exc))}")


def smbs() -> None:
    for url in ("http://www.smbs.biz/Exchange/IRS.jsp", "http://www.smbs.biz/Exchange/FxSwapUS.jsp"):
        try:
            text = get(url).content.decode("euc-kr", errors="replace")
            print(url, "len", len(text))
            i = text.find("IRS RATE") if "IRS" in url else text.find("POINT")
            print(text[max(i - 200, 0): i + 2500].replace("\r", "")[:2700])
        except Exception as exc:  # noqa: BLE001
            print(url, "실패:", scrub(str(exc)))


def krx() -> None:
    key = env("KRX_AUTH_KEY")
    day = date.today()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    for ep in ("idx/kospi_dd_trd", "idx/krx_dd_trd", "idx/kosdaq_dd_trd"):
        for back in range(0, 6):
            d = day - timedelta(days=back)
            if d.weekday() >= 5:
                continue
            try:
                body = get_json(f"https://data-dbg.krx.co.kr/svc/apis/{ep}",
                                headers={"AUTH_KEY": key}, params={"basDd": as_compact(d)})
                rows = body.get("OutBlock_1", [])
                print(ep, as_compact(d), "rows", len(rows))
                if rows:
                    print("  keys:", list(rows[0])[:14])
                    print("  sample:", {k: rows[0][k] for k in list(rows[0])[:8]})
                    print("  names:", [r.get("IDX_NM") for r in rows][:8])
                    break
            except Exception as exc:  # noqa: BLE001
                print(ep, "실패:", scrub(str(exc)))
                break


def ecos(keyword: str = "") -> None:
    key = env("ECOS_API_KEY")
    url = f"https://ecos.bok.or.kr/api/StatisticTableList/{key}/json/kr/1/2000"
    rows = get_json(url)["StatisticTableList"]["row"]
    hits = [r for r in rows if keyword in r["STAT_NAME"]] if keyword else rows[:20]
    for r in hits[:60]:
        print(r["STAT_CODE"], r.get("CYCLE", ""), r["SRCH_YN"], r["STAT_NAME"])
    print("총", len(rows), "건 / 매칭", len(hits))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"fred": fred, "yahoo": yahoo, "smbs": smbs, "krx": krx}.get(cmd, lambda: ecos(*sys.argv[2:3]))()
