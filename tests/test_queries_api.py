from datetime import date

import pytest
from fastapi.testclient import TestClient

from marketview import db, queries
from marketview.collect.base import Fetched


def put(conn, key, value, day, kind="daily", ts="", provider="ecos", origin="local"):
    y, m, d = map(int, day.split("-"))
    db.upsert_observation(conn, Fetched(key, value, date(y, m, d), provider, obs_ts=ts), kind, origin)
    conn.commit()


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setenv("MARKETVIEW_DB_PATH", str(path))
    c = db.connect(path)
    db.migrate(c)
    c.close()
    from marketview.app import app   # lifespan(수집 스레드)은 돌리지 않는다: with 문 없이 사용
    return TestClient(app), path


def conn_of(path):
    return db.connect(path)


# --- 타일 -------------------------------------------------------------------
def test_tile_bp_change_for_pct_series(conn):
    put(conn, "ktb_3y", 3.960, "2026-10-07"); put(conn, "ktb_3y", 3.983, "2026-10-08")
    s = queries.tile_state(conn, "ktb_3y", None)
    assert s["value"] == 3.983 and s["obs_date"] == "2026-10-08"
    assert s["change"] == pytest.approx(2.3) and s["change_unit"] == "bp"


def test_oas_is_shown_in_bp(conn):
    put(conn, "us_ig_oas", 0.80, "2026-10-07", provider="fred"); put(conn, "us_ig_oas", 0.82, "2026-10-08", provider="fred")
    s = queries.tile_state(conn, "us_ig_oas", None)
    assert s["value"] == pytest.approx(82.0) and s["change"] == pytest.approx(2.0) and s["change_unit"] == "bp"
    assert queries.meta_of("us_ig_oas")["unit"] == "bp"
    assert queries.series_points(conn, "us_ig_oas", None)["values"] == pytest.approx([80.0, 82.0])


def test_past_asof_ignores_future(conn):
    put(conn, "sp500", 7700.0, "2026-10-07", provider="fred"); put(conn, "sp500", 7765.0, "2026-10-08", provider="fred")
    put(conn, "sp500", 7790.0, "2026-10-09", "intraday", "2026-10-09T23:00:00+09:00", "yahoo")
    s = queries.tile_state(conn, "sp500", "2026-10-07")
    assert s["value"] == 7700.0 and s["kind"] == "daily" and s["change"] is None


def test_missing_series_has_no_tile_state(conn):
    assert queries.tile_state(conn, "kr_cds_5y", None) is None         # 0 으로 채우지 않는다
    assert queries.tile_state(conn, "ktb_10s3s", None) is None


def test_derived_tile_needs_same_observation_date(conn):
    put(conn, "ktb_3y", 3.98, "2026-10-08"); put(conn, "ktb_10y", 4.39, "2026-10-07")
    assert queries.tile_state(conn, "ktb_10s3s", None) is None
    put(conn, "ktb_10y", 4.395, "2026-10-08")
    s = queries.tile_state(conn, "ktb_10s3s", None)
    assert s["value"] == pytest.approx(41.5) and s["obs_date"] == "2026-10-08"


# --- 커브 -------------------------------------------------------------------
def test_curve_uses_lookback_and_leaves_gaps(conn):
    put(conn, "ktb_3y", 3.98, "2026-10-08"); put(conn, "ktb_10y", 4.39, "2026-10-08")
    put(conn, "ktb_3y", 3.90, "2026-10-01")
    c = queries.curve(conn, "ktb", ["2026-10-08", "2026-10-03"])      # 10-03 은 토요일 → 10-01 값
    keys = c["keys"]
    now, old = c["curves"]["2026-10-08"], c["curves"]["2026-10-03"]
    assert now["values"][keys.index("ktb_3y")] == 3.98 and now["values"][keys.index("ktb_1y")] is None
    assert old["values"][keys.index("ktb_3y")] == 3.90 and old["obs_dates"][keys.index("ktb_3y")] == "2026-10-01"
    assert old["values"][keys.index("ktb_10y")] is None               # look-back(7일) 안에 값이 없다 → 미수집
    assert c["tenors"][:3] == ["1Y", "3Y", "5Y"]


def test_curve_default_date_and_futures_implied_yield(conn):
    put(conn, "ktbf_3y", 103.34, "2026-10-08", provider="krx")
    assert queries.curve_default_date(conn, "ktbf", None) == "2026-10-08"
    c = queries.curve(conn, "ktbf", ["2026-10-08"])
    v = c["curves"]["2026-10-08"]["values"]
    assert 3.4 < v[0] < 4.2 and v[1] is None and v[2] is None
    assert c["tenors"] == ["3Y", "10Y", "30Y"]


def test_tenor_labels():
    assert [queries.tenor_label(k) for k in ("ust_1m", "ust_6m", "ust_1y", "irs_20y", "ktbf_10y_iy")] == ["1M", "6M", "1Y", "20Y", "10Y"]


# --- HTTP -------------------------------------------------------------------
def test_api_tiles_series_curve(client):
    cl, path = client
    c = conn_of(path)
    put(c, "ktb_3y", 3.96, "2026-10-07"); put(c, "ktb_3y", 3.983, "2026-10-08"); put(c, "ktb_10y", 4.395, "2026-10-08")
    c.close()
    t = cl.get("/api/tiles", params={"tab": "bond"}).json()
    ktb = next(g for g in t["groups"] if g["id"] == "ktb")
    tile = next(x for x in ktb["tiles"] if x["key"] == "ktb_3y")
    assert tile["state"]["value"] == 3.983 and tile["state"]["src"] == "ECOS"
    assert next(x for x in ktb["tiles"] if x["key"] == "ktb_5y")["state"] is None
    s = cl.get("/api/series", params={"keys": "ktb_3y,nope,ktb_10s3s"}).json()
    assert set(s) == {"ktb_3y", "ktb_10s3s"} and s["ktb_3y"]["values"] == [3.96, 3.983]
    cv = cl.get("/api/curve", params={"family": "ktb"}).json()
    assert cv["default_date"] == "2026-10-08" and len(cv["curves"]) == 3


def test_api_validation(client):
    cl, _ = client
    assert cl.get("/api/tiles", params={"tab": "nope"}).status_code == 404
    assert cl.get("/api/tiles", params={"tab": "bond", "asof": "2026-8-1"}).status_code == 400
    assert cl.get("/api/curve", params={"family": "nope"}).status_code == 404


def test_manual_input_flow(client):
    cl, path = client
    ok = cl.put("/api/observation", json={"series_key": "kr_cds_5y", "obs_date": "2026-10-08", "value": 23.1, "note": "wgb"})
    assert ok.status_code == 200 and ok.json()["action"] == "insert"
    tile = next(x for g in cl.get("/api/tiles", params={"tab": "bond"}).json()["groups"] for x in g["tiles"] if x["key"] == "kr_cds_5y")
    assert tile["state"]["value"] == 23.1 and tile["state"]["src"] == "수동 입력"
    again = cl.put("/api/observation", json={"series_key": "kr_cds_5y", "obs_date": "2026-10-08", "value": 24.0})
    assert again.json()["action"] == "update"
    c = conn_of(path)
    log = c.execute("SELECT action, prev_value, new_value FROM observation_log WHERE series_key='kr_cds_5y' ORDER BY id").fetchall()
    assert [(r["action"], r["prev_value"], r["new_value"]) for r in log] == [("insert", None, 23.1), ("update", 23.1, 24.0)]
    row = c.execute("SELECT origin, provider FROM observation WHERE series_key='kr_cds_5y'").fetchone()
    assert (row["origin"], row["provider"]) == ("manual", "user")


def test_manual_input_rejections(client):
    cl, _ = client
    body = {"series_key": "ktb_3y", "obs_date": "2026-10-08", "value": 1.0}
    assert cl.put("/api/observation", json=body).status_code == 400                       # 자동 수집 항목
    assert cl.put("/api/observation", json={**body, "series_key": "nope"}).status_code == 400
    manual = {"series_key": "kr_cds_5y", "obs_date": "2026-10-08", "value": 1.0}
    assert cl.put("/api/observation", json={**manual, "obs_date": "2026-8-1"}).status_code == 400
    assert cl.put("/api/observation", json={**manual, "obs_date": "2999-01-01"}).status_code == 400   # 미래
    assert cl.put("/api/observation", json={**manual, "value": "abc"}).status_code == 422
    raw = '{"series_key": "kr_cds_5y", "obs_date": "2026-10-08", "value": NaN}'      # 표준 JSON 은 아니지만 파이썬 클라이언트는 보낼 수 있다
    assert cl.put("/api/observation", content=raw, headers={"Content-Type": "application/json"}).status_code in (400, 422)
    raw_inf = raw.replace("NaN", "Infinity")
    assert cl.put("/api/observation", content=raw_inf, headers={"Content-Type": "application/json"}).status_code in (400, 422)


def test_manual_value_not_overwritten_by_auto_collector(conn):
    put(conn, "kr_cds_5y", 23.1, "2026-10-08", provider="user", origin="manual")
    assert db.upsert_observation(conn, Fetched("kr_cds_5y", 99.0, date(2026, 10, 8), "ecos"), "daily", "local") == "skipped"


def test_status_shape(client):
    cl, path = client
    c = conn_of(path)
    c.execute("INSERT INTO ingest_run (origin,kind,started_at,finished_at,status) VALUES ('local','daily','t','2026-10-09T10:00:00+09:00','partial')")
    c.execute("INSERT INTO ingest_result VALUES (1,'yahoo','russell2000','error',0,'HTTP 429')")
    c.commit(); c.close()
    st = cl.get("/api/status").json()
    assert st["last_refresh"] == "2026-10-09T10:00:00+09:00" and st["collecting"] is False
    assert st["failures"] == [{"source": "yahoo", "series_key": "russell2000", "message": "HTTP 429"}]
    assert st["cloud_sync"] is None


def test_status_versions_move_only_with_their_data(client):
    """화면은 버전이 바뀐 패널만 다시 그린다: 같은 값 재수집은 시세 버전을 올리지 않고, 뉴스는 뉴스 버전만 올린다."""
    cl, path = client
    v0 = cl.get("/api/status").json()["versions"]
    c = conn_of(path)
    put(c, "ktb_3y", 3.96, "2026-10-08")
    v1 = cl.get("/api/status").json()["versions"]
    put(c, "ktb_3y", 3.96, "2026-10-08")                       # 같은 값 → observation_log 행 없음
    assert cl.get("/api/status").json()["versions"] == v1 and v1["obs"] != v0["obs"] and v1["news"] == v0["news"]
    c.execute("INSERT INTO news (link,title,source,feed_id,published_at,title_key,fetched_at,origin) "
              "VALUES ('https://x/1','t','s','f','2026-10-08T09:00:00+09:00','t','2026-10-08T09:00:00+09:00','local')")
    c.commit(); c.close()
    v2 = cl.get("/api/status").json()["versions"]
    assert v2["news"] != v1["news"] and v2["obs"] == v1["obs"] and v2["events"] == v1["events"]


def test_curve_with_compare_dates_includes_base(client):
    cl, path = client
    c = conn_of(path)
    put(c, "ktb_3y", 3.90, "2026-09-08"); put(c, "ktb_3y", 3.95, "2026-10-01"); put(c, "ktb_3y", 3.98, "2026-10-08")
    c.close()
    cv = cl.get("/api/curve", params={"family": "ktb", "dates": "2026-10-01,2026-09-08"}).json()
    assert cv["default_date"] == "2026-10-08" and list(cv["curves"]) == ["2026-10-08", "2026-10-01", "2026-09-08"]


# --- 기준일 모드(권역별 마감일) --------------------------------------------------
def _fill(conn, keys, day, provider):
    for k in keys:
        put(conn, k, 1.0, day, provider=provider)


def test_close_dates_are_per_region_and_use_coverage(conn):
    from datetime import timedelta
    today = queries.today_kst()
    d1, d2, d3 = ((today - timedelta(days=n)).isoformat() for n in (1, 2, 3))
    kr = [k for tab in queries.layout.TABS for k in queries.visible_keys(tab)
          if k in queries.BY_KEY and queries.region_of(k) == "kr" and queries.BY_KEY[k].provider != "manual"]
    us = [k for tab in queries.layout.TABS for k in queries.visible_keys(tab)
          if k in queries.BY_KEY and queries.region_of(k) == "us"]
    _fill(conn, kr, d1, "ecos")                       # 한국 전부 d1
    _fill(conn, us, d2, "fred")                       # 미국 전부 d2 (d1 은 일부만)
    _fill(conn, us[:2], d1, "fred")
    got = queries.common_close_dates(conn)
    assert got == {"kr": d1, "us": d2}
    # 마감 모드: 권역 마감일까지의 값, 장중 값 미사용
    put(conn, "kospi", 9999.0, today.isoformat(), kind="intraday", ts=today.isoformat() + "T10:00:00+09:00", provider="yahoo")
    t = {x["key"]: x for g in queries.tiles(conn, "equity", None, got)["groups"] for x in g["tiles"]}
    assert t["kospi"]["state"]["kind"] == "daily" and t["kospi"]["ref_date"] == d1
    assert t["sp500"]["ref_date"] == d2
    live = {x["key"]: x for g in queries.tiles(conn, "equity", None, None)["groups"] for x in g["tiles"]}
    assert live["kospi"]["state"]["kind"] == "daily" and live["kospi"]["ref_date"] is None    # 장중 값은 더 쓰지 않는다


def test_explicit_asof_beats_close_mode(conn):
    assert queries.asof_for("kospi", "2026-10-01", {"kr": "2026-10-13"}) == "2026-10-01"
    assert queries.asof_for("kospi", None, {"kr": "2026-10-13"}) == "2026-10-13"
    assert queries.asof_for("kospi", None, None) is None


def test_api_tiles_uses_close_dates(client):
    c, path = client
    conn = conn_of(path)
    put(conn, "ktb_3y", 3.98, queries.default_today())
    conn.close()
    assert c.get("/api/tiles", params={"tab": "bond"}).status_code == 200
    assert "closes" in c.get("/api/meta").json()


def test_step_series_is_not_flagged_stale_in_close_mode(conn):
    put(conn, "bok_base_rate", 3.0, "2026-10-08")
    put(conn, "kofr", 3.05, "2026-10-12")
    closes = {"kr": "2026-10-13", "us": "2026-10-12"}
    t = {x["key"]: x for g in queries.tiles(conn, "bond", None, closes)["groups"] for x in g["tiles"]}
    assert t["bok_base_rate"]["ref_date"] is None and t["bok_base_rate"]["state"]["obs_date"] == "2026-10-08"
    assert t["kofr"]["ref_date"] == "2026-10-13"          # 일반 지표는 그대로 표시 대상


def test_index_versions_static_assets(client):
    c, _ = client
    html = c.get("/").text
    assert "/static/app.js?v=" in html and "/static/app.css?v=" in html
    assert c.get("/").headers["cache-control"] == "no-cache"
