"""2026-10-10 화면 구성 변경(IRS 1·3·5·10년, USD 스왑·신용 섹터·뷰 메모 제거)과 지표 검색."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from marketview import db, derived, queries
from marketview.collect.base import Fetched
from marketview.config import layout
from marketview.config.series import BY_KEY
from marketview.report import data, render

SECTOR_PREFIXES = ("pubco_", "bank_aaa", "card_aa", "finco_", "corp_aaa")


def _put(conn, key, d, v, provider="ecos"):
    y, m, dd = map(int, d.split("-"))
    db.upsert_observation(conn, Fetched(key, v, date(y, m, dd), provider), "daily", "local")
    conn.commit()


def test_irs_shows_only_1_3_5_10(conn):
    irs = next(g for g in layout.TABS["bond"]["groups"] if g["id"] == "irs")
    assert irs["tiles"] == ["irs_1y", "irs_3y", "irs_5y", "irs_10y"]
    assert layout.CURVES["irs"].keys == ("irs_1y", "irs_3y", "irs_5y", "irs_10y")
    assert queries.curve(conn, "irs", ["2026-10-08"])["tenors"] == ["1Y", "3Y", "5Y", "10Y"]


def test_excluded_items_are_gone_everywhere():
    shown = {k for t in layout.TABS for k in queries.visible_keys(t)}
    assert not any(k.startswith("usd_swap") or k.startswith(SECTOR_PREFIXES) for k in shown)
    assert "usd_swap" not in layout.CURVES
    assert not any(k.startswith("usd_swap") or k.startswith(SECTOR_PREFIXES) for k in list(BY_KEY) + list(derived.REGISTRY))
    assert [g["id"] for g in layout.TABS["credit"]["groups"]] == ["corp"]       # 섹터 수익률·스프레드 그룹 없음
    assert not hasattr(layout, "CREDIT_TENORS")


def test_summary_and_report_follow_layout(conn):
    for k in ("irs_2y", "irs_3y", "irs_7y", "irs_10y"):                          # 2·7년도 수집·저장은 그대로
        _put(conn, k, "2026-10-08", 4.0, provider="smbs")
    md = render.to_markdown(data.build(conn, "2026-10-08", {}, [], True))
    assert "| KRW IRS 3년 |" in md and "| KRW IRS 10년 |" in md
    assert "KRW IRS 2년" not in md and "KRW IRS 7년" not in md and "USD SOFR" not in md and "공사채" not in md
    assert "## 6. 포지션" in md and "나의 뷰" not in md and "그날 메모" not in md


# --- 검색 ---------------------------------------------------------------------
def test_search_matches_label_key_group_and_normalizes_minus(conn):
    _put(conn, "ktb_10y", "2026-10-08", 4.395)
    _put(conn, "ktb_3y", "2026-10-08", 3.983)
    r = queries.search_series(conn, "국고 10년")
    assert [x["key"] for x in r] == ["ktb_10y"] and r[0]["state"]["value"] == 4.395 and r[0]["group"] == "ktb" and r[0]["tab"] == "bond"
    assert [x["key"] for x in queries.search_series(conn, "10-3")][:1] == ["ktb_10s3s"]          # 하이픈 입력이 유니코드 마이너스 라벨에 맞는다
    assert "ust_10y" in [x["key"] for x in queries.search_series(conn, "UST 10")]                   # 대소문자·부분 일치
    assert queries.search_series(conn, "ktb_30y")[0]["key"] == "ktb_30y"                          # 키로도 찾는다
    irs = queries.search_series(conn, "irs", limit=50)
    assert [x["key"] for x in irs if x["shown"]] == ["irs_1y", "irs_3y", "irs_5y", "irs_10y"]       # 타일에 나오는 것
    assert {x["key"] for x in irs if not x["shown"]} == {"irs_2y", "irs_4y", "irs_7y", "irs_12y", "irs_15y", "irs_20y"}
    assert [x["shown"] for x in irs] == sorted((x["shown"] for x in irs), reverse=True)           # 같은 순위에서는 화면에 나오는 것이 먼저


def test_search_label_hits_rank_before_group_hits(conn):
    r = queries.search_series(conn, "국고채")                                                   # '국고채' 는 그룹 이름 → 타일 라벨엔 없음
    assert r and all(x["group"] == "ktb" for x in r[:6])
    r2 = queries.search_series(conn, "KOSPI")
    assert [x["key"] for x in r2] == ["kospi"] and r2[0]["tab"] == "equity"


def test_search_excluded_and_empty_and_missing_values(conn):
    assert queries.search_series(conn, "") == [] and queries.search_series(conn, "   ") == []
    assert queries.search_series(conn, "USD 스왑") == [] and queries.search_series(conn, "공사채") == [] and queries.search_series(conn, "섹터") == []
    r = queries.search_series(conn, "KOSPI")
    assert r[0]["state"] is None                                                                  # 값이 없으면 None (0 으로 채우지 않는다)
    assert queries.search_series(conn, "x" * 80) == []
    assert queries.search_series(conn, "%'; DROP TABLE observation; --") == []                     # 특수문자는 그냥 글자
    assert len(queries.search_series(conn, "년", limit=5)) == 5


def test_search_asof_uses_that_days_close(conn):
    _put(conn, "ktb_3y", "2026-10-07", 3.90)
    _put(conn, "ktb_3y", "2026-10-08", 3.98)
    assert queries.search_series(conn, "국고 3년", "2026-10-07")[0]["state"]["value"] == 3.90


# --- 화면 타일에 없지만 저장돼 있는 지표도 검색된다 ------------------------------------
def test_search_finds_stored_indicators_not_shown_as_tiles(conn):
    _put(conn, "irs_20y", "2026-10-08", 4.0875, provider="smbs")
    _put(conn, "ust_1m", "2026-10-07", 4.2, provider="fred")
    r = queries.search_series(conn, "irs 20")
    assert [x["key"] for x in r] == ["irs_20y"] and r[0]["shown"] is False and r[0]["group"] is None
    assert r[0]["tab"] == "bond" and r[0]["state"]["value"] == 4.0875 and r[0]["state"]["src"] == "서울외국환중개"
    u = queries.search_series(conn, "UST 1개월")
    assert u[0]["key"] == "ust_1m" and u[0]["shown"] is False and u[0]["state"]["obs_date"] == "2026-10-07"
    assert "ktb_20y" in [x["key"] for x in queries.search_series(conn, "국고 20년")]
    assert queries.search_series(conn, "irs_7y")[0]["state"] is None                                 # 값이 없으면 None (0 아님)


def test_search_by_source_name_and_derived(conn):
    keys = [x["key"] for x in queries.search_series(conn, "fred", limit=100)]
    assert "ust_10y" in keys and "ust_1m" in keys and "sofr" in keys and "ktb_3y" not in keys
    assert "ktb_10s3s" in [x["key"] for x in queries.search_series(conn, "파생", limit=100)]


def test_all_keys_cover_defined_indicators_only():
    keys = set(queries.all_keys())
    assert set(BY_KEY) | set(derived.REGISTRY) == keys and len(queries.all_keys()) == len(keys)
    assert {"irs_20y", "ust_1m", "ktb_1y"} <= keys
    assert not any(k.startswith("usd_swap") or k.startswith(SECTOR_PREFIXES) for k in keys)


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "l.db"
    monkeypatch.setenv("MARKETVIEW_DB_PATH", str(path))
    c = db.connect(path)
    db.migrate(c)
    c.close()
    from marketview.app import app
    return TestClient(app)


def test_http_find_meta_and_removed_routes(client):
    f = client.get("/api/find", params={"q": "irs 10"}).json()
    assert [x["key"] for x in f["items"]] == ["irs_10y"]
    assert client.get("/api/find", params={"q": ""}).json()["items"] == []
    assert client.get("/api/find", params={"q": "ktb", "asof": "2026-8-1"}).status_code == 400
    m = client.get("/api/meta").json()
    assert "tenors" not in m and "sector_bases" not in m and "default_tenor" not in m
    assert {"irs_20y", "ust_1m"} <= set(m["known_keys"]) and not any(k.startswith("usd_swap") for k in m["known_keys"])
    hidden = client.get("/api/find", params={"q": "irs 20"}).json()["items"]
    assert [(x["key"], x["shown"]) for x in hidden] == [("irs_20y", False)]
    credit = next(t for t in m["tabs"] if t["id"] == "credit")
    assert credit["keys"] == ["corp_aa3_3y", "corp_bbb3_3y", "corp_aa3_spread_3y", "corp_bbb3_spread_3y"]
    assert [c["id"] for c in m["curves"]] == ["ktb", "ktbf", "irs", "ust", "ust_fut"]
    assert client.get("/api/notes").status_code == 404 and client.get("/note").status_code == 404
    t = client.get("/api/tiles", params={"tab": "credit"}).json()
    assert [g["id"] for g in t["groups"]] == ["corp"] and "tenor" not in t
