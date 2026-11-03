import pytest

from marketview import derived
from marketview.futures import implied_yield, price_from_yield


def test_par_bond_prices_at_100_when_yield_equals_coupon():
    for years in (3, 10, 30):
        assert price_from_yield(5.0, years) == pytest.approx(100.0, abs=1e-9)


def test_price_decreases_with_yield_and_longer_is_more_sensitive():
    assert price_from_yield(4.0, 3) > price_from_yield(5.0, 3) > price_from_yield(6.0, 3)
    drop3 = price_from_yield(4.0, 3) - price_from_yield(6.0, 3)
    drop10 = price_from_yield(4.0, 10) - price_from_yield(6.0, 10)
    assert drop10 > drop3


@pytest.mark.parametrize("years", [3, 10, 30])
@pytest.mark.parametrize("y", [0.5, 2.75, 3.8, 5.0, 7.2])
def test_roundtrip(years, y):
    assert implied_yield(price_from_yield(y, years), years) == pytest.approx(y, abs=1e-6)


def test_known_value_three_year_semiannual():
    # 3년 표면 5% 반년 이표, y=4% → 손계산: 2.5*(1-1.02^-6)/0.02 + 100*1.02^-6
    expected = 2.5 * (1 - 1.02 ** -6) / 0.02 + 100 * 1.02 ** -6
    assert price_from_yield(4.0, 3) == pytest.approx(expected, abs=1e-9)
    assert expected == pytest.approx(102.8014, abs=1e-3)


def test_out_of_range_price_returns_none_not_zero():
    assert implied_yield(1000.0, 3) is None
    assert implied_yield(1.0, 3) is None


def test_market_sanity_vs_ktb():
    # 2026-08-10 KRX 3년 종가 103.34 / 국고 3년 3.8% 근방 (ficc-desk 기록). 같은 자릿수여야 한다.
    y3 = implied_yield(103.34, 3)
    assert 3.4 < y3 < 4.2


def test_bp_spread_and_missing_inputs():
    table = {
        "2026-10-07": {"ktb_10y": 4.40, "ktb_3y": 3.98},
        "2026-10-08": {"ktb_10y": 4.395},            # ktb_3y 없음 → 건너뛴다 (0 으로 채우지 않는다)
        "2026-10-09": {"ktb_3y": 3.99, "ktb_10y": 4.41},
    }
    out = derived.compute("ktb_10s3s", table)
    assert [d for d, _ in out] == ["2026-10-07", "2026-10-09"]
    assert out[0][1] == pytest.approx(42.0)
    assert out[1][1] == pytest.approx(42.0)


def test_dates_must_match_exactly():
    # 관측일이 다른 값끼리 짝지어 스프레드를 만들지 않는다
    table = {"2026-10-07": {"ust_10y": 5.28}, "2026-10-08": {"ust_2y": 4.77}}
    assert derived.compute("ust_10s2s", table) == []


def test_futures_implied_yield_derived():
    out = derived.compute("ktbf_3y_iy", {"2026-10-08": {"ktbf_3y": 103.34}})
    assert len(out) == 1 and 3.4 < out[0][1] < 4.2
    assert derived.compute("ktbf_3y_iy", {"2026-10-08": {"ktbf_3y": 5000.0}}) == []


def test_series_from_db(conn):
    from marketview.collect.base import Fetched
    from datetime import date
    from marketview import db
    for k, v in (("cd_91d", 3.24), ("kofr", 2.968)):
        db.upsert_observation(conn, Fetched(k, v, date(2026, 10, 7), "ecos"), "daily", "local")
    db.upsert_observation(conn, Fetched("cd_91d", 3.30, date(2026, 10, 8), "ecos"), "daily", "local")
    conn.commit()
    out = derived.series(conn, "cd_kofr")
    assert out == [("2026-10-07", pytest.approx(27.2))]
