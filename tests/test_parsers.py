from datetime import date, timedelta

import pytest

from marketview.collect import ecos, fred, krx, smbs, yahoo
from marketview.collect.base import scrub, to_float


def test_to_float_missing_is_none_not_zero():
    for raw in (None, "", ".", "-", "NA", "null", " "):
        assert to_float(raw) is None
    assert to_float("1,234.5") == 1234.5
    assert to_float("0") == 0.0


def test_scrub_hides_keys():
    assert "SECRET" not in scrub("GET https://x/fred?series_id=A&api_key=SECRET&file_type=json")
    assert "SECRET" not in scrub("https://ecos.bok.or.kr/api/StatisticSearch/SECRET/json/kr/1/2")


def test_ecos_parse_skips_blank_and_bad_rows():
    rows = [{"TIME": "20261008", "DATA_VALUE": "3.983"}, {"TIME": "20261007", "DATA_VALUE": ""},
            {"TIME": "202610", "DATA_VALUE": "1"}, {"TIME": "20261006", "DATA_VALUE": "3.97"}]
    got = ecos.parse_rows(rows, "ktb_3y")
    assert [(f.obs_date, f.value) for f in got] == [(date(2026, 10, 8), 3.983), (date(2026, 10, 6), 3.97)]


def test_fred_dot_is_missing():
    rows = [{"date": "2026-10-07", "value": "4.07"}, {"date": "2026-10-08", "value": "."},
            {"date": "bad", "value": "1"}]
    got = fred.parse_observations(rows, "ust_1m")
    assert [(f.obs_date, f.value) for f in got] == [(date(2026, 10, 7), 4.07)]


KRX_ROWS = [
    {"BAS_DD": "20261008", "PROD_NM": "3년국채 선물", "MKT_NM": "정규", "ISU_NM": "3년국채    F 202612 (주간)",
     "TDD_CLSPRC": "103.34", "ACC_TRDVOL": "150586", "ACC_OPNINT_QTY": "556455"},
    {"BAS_DD": "20261008", "PROD_NM": "3년국채 선물", "MKT_NM": "정규", "ISU_NM": "3년국채    F 202703 (주간)",
     "TDD_CLSPRC": "102.91", "ACC_TRDVOL": "1", "ACC_OPNINT_QTY": "10"},
    # 롤오버 주간: 캘린더 스프레드 거래량이 더 커도 선물 종가로 저장되면 안 된다
    {"BAS_DD": "20261008", "PROD_NM": "3년국채 선물", "MKT_NM": "정규", "ISU_NM": "3년국채    SP 2612-2703 (주간)",
     "TDD_CLSPRC": "0.43", "ACC_TRDVOL": "999999", "ACC_OPNINT_QTY": "1"},
    {"BAS_DD": "20261008", "PROD_NM": "3년국채 선물", "MKT_NM": "야간", "ISU_NM": "3년국채    F 202612 (야간)",
     "TDD_CLSPRC": "103.44", "ACC_TRDVOL": "164", "ACC_OPNINT_QTY": "0"},
    {"BAS_DD": "20261008", "PROD_NM": "10년국채 선물", "MKT_NM": "정규", "ISU_NM": "10년국채   F 202612 (주간)",
     "TDD_CLSPRC": "106.25", "ACC_TRDVOL": "62352", "ACC_OPNINT_QTY": "311096"},
    {"BAS_DD": "20261008", "PROD_NM": "3년-10년국채선물스프레드 선물", "MKT_NM": "정규", "ISU_NM": "x F 1",
     "TDD_CLSPRC": "9.9", "ACC_TRDVOL": "5", "ACC_OPNINT_QTY": "5"},
]


def test_krx_picks_front_regular_non_spread_exact_name():
    got = {f.series_key: f.value for f in krx.extract(KRX_ROWS, date(2026, 10, 8))}
    assert got["ktbf_3y"] == 103.34
    assert got["ktbf_3y_oi"] == 556455
    assert got["ktbf_10y"] == 106.25
    assert "ktbf_30y" not in got                      # 행이 없으면 만들지 않는다


def _enc(text: str) -> str:
    return "".join(f"%_Z{ord(c):02x}" if ord(c) < 128 else f"%u_D{ord(c):04x}" for c in text)


def _irs_html(table_date="2026년 10월 08일") -> str:
    def call(fn, text):
        return f"<script>{fn}( '{_enc(text)}' );</script>"
    rows = ""
    for tenor, rec, mid, pay in (("1Y", "3.6525", "3.6425", "3.6325"), ("3Y", "4.0400", "4.0300", "4.0200"),
                                 ("10Y", "4.5", "4.4", "4.3"), ("99Y", "1", "2", "3")):
        rows += f"<tr><td>{call('d1', tenor)}</td><td>{call('d2', rec)}</td><td><b>{call('d5', mid)}</b></td>" \
                f"<td>{call('d1', pay)}</td></tr>"
    return (f'<h4 class="fl">{call("d1", table_date)}</h4>'
            f'<table><caption>IRS RATE 조회 결과 표</caption><tbody>{rows}</tbody></table>')


def test_smbs_decode():
    assert smbs.decode("%_Z33%_Z2e%_Z36%_Z35%_Z32%_Z35") == "3.6525"
    assert smbs.decode("%u_Db144") == "년"


def test_smbs_parse_irs_uses_mid_and_table_date():
    table_date, mids = smbs.parse_irs(_irs_html())
    assert table_date == date(2026, 10, 8)
    assert mids == {"1Y": 3.6425, "3Y": 4.03, "10Y": 4.4}      # Receive/Pay 가 아니라 Mid, 정의 밖 기일물 제외
    got = {f.series_key: f.value for f in smbs.to_fetched(table_date, mids)}
    assert got["irs_3y"] == 4.03 and all(f.obs_date == table_date for f in smbs.to_fetched(table_date, mids))


def test_smbs_no_table_is_none():
    assert smbs.parse_irs("<html>nothing</html>") is None


def _chart(stamps, closes, end, now_offset=0):
    return {"meta": {"gmtoffset": 32400, "currentTradingPeriod": {"regular": {"end": end}}},
            "timestamp": stamps, "indicators": {"quote": [{"close": closes}]}}


def test_yahoo_drops_unfinished_today_bar():
    # KST 오전 9시 = UTC 0시. 2026-10-08 00:00 UTC = KST 10-08 09:00
    day1, day2 = 1791417600, 1791504000            # 2026-10-08 00:00Z, 2026-10-09 00:00Z
    result = _chart([day1, day2], [100.0, 101.0], end=day2 + 6 * 3600)
    now_open = day2 + 3600                         # 장중 → 오늘 봉(10-09)은 종가가 아니다
    got = yahoo.parse_daily(result, "kospi", now_ts=now_open)
    assert [(f.obs_date, f.value) for f in got] == [(date(2026, 10, 8), 100.0)]
    now_closed = day2 + 8 * 3600                   # 장 종료 후 → 오늘 봉 포함
    got = yahoo.parse_daily(result, "kospi", now_ts=now_closed)
    assert [f.obs_date for f in got] == [date(2026, 10, 8), date(2026, 10, 9)]


def test_yahoo_null_close_is_skipped():
    result = _chart([1791417600], [None], end=1791417600 + 3600)
    assert yahoo.parse_daily(result, "kospi", now_ts=1791417600 + 99999) == []


KRX_IDX_ROWS = [
    {"BAS_DD": "20261013", "IDX_NM": "코스피 (외국주포함)", "CLSPRC_IDX": ""},        # 값 없는 행이 먼저 온다
    {"BAS_DD": "20261013", "IDX_NM": "코스피", "CLSPRC_IDX": "6035.35"},
    {"BAS_DD": "20261013", "IDX_NM": "코스피 200", "CLSPRC_IDX": "942.07"},
    {"BAS_DD": "20261013", "IDX_NM": "코스닥", "CLSPRC_IDX": ""},
]


def test_krx_index_exact_name_and_empty_is_missing():
    got = krx.extract_index(KRX_IDX_ROWS, "kospi", date(2026, 10, 13))
    assert got and got.value == 6035.35 and got.provider == "krx" and got.obs_date == date(2026, 10, 13)
    assert krx.extract_index(KRX_IDX_ROWS, "kosdaq", date(2026, 10, 13)) is None     # 빈 문자열을 0 으로 채우지 않는다
    assert krx.extract_index([], "kospi", date(2026, 10, 13)) is None                # 휴장일 0행


def test_krx_range_stops_at_time_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(krx, "BUDGET_SECONDS", 0.0)
    monkeypatch.setattr(krx, "fetch_day_rows", lambda day, endpoint=None: calls.append(day) or [])
    fetched, problems = krx.fetch_range(date(2026, 10, 5), date(2026, 10, 9))
    assert fetched == [] and calls == [] and len(problems) == 1 and "시간 초과" in problems[0].message
    f2, p2 = krx.fetch_index_range("kospi", date(2026, 10, 5), date(2026, 10, 9))
    assert f2 == [] and calls == [] and "시간 초과" in p2[0].message


def test_smbs_table_without_date_is_a_structure_change_not_a_holiday():
    """표 제목은 있는데 기준일을 못 읽으면 Failed(구조 변경)이다. 표가 아예 없는 날(휴장)은 None → Missing."""
    broken = _irs_html().replace('<h4 class="fl">', '<h4 class="fx">')
    with pytest.raises(ValueError, match="구조 변경"):
        smbs.parse_irs(broken)


def test_smbs_fetch_day_reports_structure_change_as_failed(monkeypatch):
    class R:
        content = _irs_html().replace('<h4 class="fl">', '<h4 class="fx">').encode("euc-kr", errors="replace")
    monkeypatch.setattr(smbs, "get", lambda *a, **k: R())
    out = smbs.fetch_day(date(2026, 10, 8))
    assert len(out) == 1 and type(out[0]).__name__ == "Failed"


def test_smbs_range_counts_only_consecutive_failures(monkeypatch):
    from marketview.collect.base import Failed
    start = date(2026, 10, 5)                                                   # 월요일
    days = [start + timedelta(days=i) for i in range(10) if (start + timedelta(days=i)).weekday() < 5]
    fail_days = {days[0], days[2], days[4], days[6]}                            # 띄엄띄엄 4번 + 사이에 성공
    def fake(day):
        return [Failed("irs", "HTTP 500")] if day in fail_days else smbs.to_fetched(day, {"3Y": 4.0})
    monkeypatch.setattr(smbs, "fetch_day", fake)
    out = smbs.fetch_range(days[0], days[-1])
    assert not any(isinstance(r, Failed) and "연속" in r.message for r in out)       # 중단하지 않았다
    assert {r.obs_date for r in out if hasattr(r, "obs_date")} == set(days) - fail_days
    monkeypatch.setattr(smbs, "fetch_day", lambda day: [Failed("irs", "HTTP 500")])
    assert any("연속 실패 5회" in r.message for r in smbs.fetch_range(days[0], days[-1]) if isinstance(r, Failed))
