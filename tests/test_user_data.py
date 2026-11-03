import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from marketview import db, events, userdata
from marketview.collect.base import Fetched
from marketview.config import events as cfg


# --- KST 변환 -----------------------------------------------------------------
def test_kst_view_dst_and_date_shift():
    base = date(2026, 10, 9)
    cpi = events.kst_view("2026-10-14", "08:30", "America/New_York", base)          # EDT: 08:30 → 21:30 KST 같은 날
    assert (cpi["kst_date"], cpi["kst_time"], cpi["d_day"], cpi["weekday"]) == ("2026-10-14", "21:30", 5, "수")
    fomc = events.kst_view("2026-10-28", "14:00", "America/New_York", base)         # 14:00 EDT → 03:00 KST 다음날
    assert (fomc["kst_date"], fomc["kst_time"], fomc["d_day"]) == ("2026-10-29", "03:00", 20)
    winter = events.kst_view("2026-12-09", "14:00", "America/New_York", base)       # EST: 14:00 → 04:00 KST 다음날
    assert (winter["kst_date"], winter["kst_time"]) == ("2026-12-10", "04:00")
    after_dst = events.kst_view("2026-11-04", "08:30", "America/New_York", base)    # 11/1 서머타임 종료 후 08:30 EST → 22:30 KST
    assert after_dst["kst_time"] == "22:30"


def test_unknown_time_is_not_invented():
    v = events.kst_view("2026-10-22", None, "Asia/Seoul", date(2026, 10, 9))
    assert v["kst_time"] is None and v["kst_date"] == "2026-10-22" and v["d_day"] == 13


# --- 이벤트 동기화 ---------------------------------------------------------------
def test_official_sync_idempotent_and_verified_sources(conn):
    n1 = events.sync_official(conn)
    assert n1 == len(cfg.official_events())
    assert events.sync_official(conn) == 0
    row = conn.execute("SELECT * FROM event WHERE ext_key='fomc:2026-10-28'").fetchone()
    assert (row["local_time"], row["tz"], row["origin"]) == ("14:00", "America/New_York", "official")
    assert row["source_url"].startswith("https://www.federalreserve.gov") and row["verified_on"]
    bok = conn.execute("SELECT * FROM event WHERE ext_key='bok:2026-10-22'").fetchone()
    assert bok["local_time"] is None and bok["tz"] == "Asia/Seoul"


def test_official_dates_match_checked_sources():
    fomc = {e.local_date for e in cfg.official_events() if e.kind == "fomc" and e.local_date.startswith("2026")}
    assert fomc == {"2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"}
    bok = {e.local_date for e in cfg.official_events() if e.kind == "bok"}
    assert bok == set(cfg.BOK_DAYS) and "2026-10-22" in bok and "2026-11-26" in bok


def fake_fetch(table):
    def f(release_id, start, end):
        v = table[release_id]
        if isinstance(v, Exception):
            raise v
        return [d for d in v if start.isoformat() <= d <= end.isoformat()]
    return f


def test_fred_sync_user_edits_survive_and_stale_and_revive(conn):
    today = date(2026, 10, 9)
    res = events.sync_fred(conn, today, fetch=fake_fetch({10: ["2026-10-14", "2026-11-10"], 50: ["2026-11-06"], 54: [], 53: []}))
    assert res[10] == "ok: 2건"
    eid = conn.execute("SELECT id FROM event WHERE ext_key='fred:10:2026-10-14'").fetchone()["id"]
    events.update(conn, eid, {"consensus": "+0.3% m/m", "memo": "헤드라인보다 코어", "importance": 3})
    # 같은 데이터로 다시 동기화해도 사용자가 쓴 칸은 그대로
    events.sync_fred(conn, today, fetch=fake_fetch({10: ["2026-10-14", "2026-11-10"], 50: ["2026-11-06"], 54: [], 53: []}))
    row = conn.execute("SELECT * FROM event WHERE id=?", (eid,)).fetchone()
    assert (row["consensus"], row["memo"]) == ("+0.3% m/m", "헤드라인보다 코어")
    # FRED 가 날짜를 11-10 → 11-12 로 바꾸면 옛 항목은 stale, 새 항목이 생긴다
    events.sync_fred(conn, today, fetch=fake_fetch({10: ["2026-10-14", "2026-11-12"], 50: ["2026-11-06"], 54: [], 53: []}))
    assert conn.execute("SELECT status FROM event WHERE ext_key='fred:10:2026-11-10'").fetchone()["status"] == "stale"
    assert conn.execute("SELECT status FROM event WHERE ext_key='fred:10:2026-11-12'").fetchone()["status"] == "active"
    # 되돌아오면 되살아난다
    events.sync_fred(conn, today, fetch=fake_fetch({10: ["2026-10-14", "2026-11-10"], 50: ["2026-11-06"], 54: [], 53: []}))
    assert conn.execute("SELECT status FROM event WHERE ext_key='fred:10:2026-11-10'").fetchone()["status"] == "active"


def test_fred_resync_next_day_without_changes_writes_no_log(conn):
    dates = {10: ["2026-10-14"], 50: [], 54: [], 53: []}
    events.sync_fred(conn, date(2026, 10, 9), fetch=fake_fetch(dates))
    before = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
    events.sync_fred(conn, date(2026, 10, 10), fetch=fake_fetch(dates))      # 확인 날짜만 다르다
    assert conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0] == before


def test_one_failing_release_does_not_stop_others(conn):
    res = events.sync_fred(conn, date(2026, 10, 9), fetch=fake_fetch({10: RuntimeError("HTTP 500"), 50: ["2026-11-06"], 54: [], 53: []}))
    assert res[10].startswith("error") and res[50] == "ok: 1건"
    assert conn.execute("SELECT COUNT(*) FROM event WHERE ext_key LIKE 'fred:50:%'").fetchone()[0] == 1


def test_user_deleted_event_is_not_revived_by_sync(conn):
    events.sync_official(conn)
    eid = conn.execute("SELECT id FROM event WHERE ext_key='fomc:2026-10-28'").fetchone()["id"]
    events.delete(conn, eid)
    events.sync_official(conn)
    assert conn.execute("SELECT status FROM event WHERE id=?", (eid,)).fetchone()["status"] == "deleted"
    assert all(e["id"] != eid for e in events.list_events(conn, date(2026, 10, 9)))


def test_official_event_core_fields_locked_but_memo_editable(conn):
    events.sync_official(conn)
    eid = conn.execute("SELECT id FROM event WHERE ext_key='bok:2026-10-22'").fetchone()["id"]
    events.update(conn, eid, {"memo": "동결 전망", "consensus": "동결"})
    with pytest.raises(ValueError):
        events.update(conn, eid, {"local_date": "2026-10-23"})
    row = conn.execute("SELECT local_date, memo FROM event WHERE id=?", (eid,)).fetchone()
    assert (row["local_date"], row["memo"]) == ("2026-10-22", "동결 전망")


def test_manual_event_crud_log_and_listing_window(conn):
    eid = events.create_manual(conn, {"title": "국고채 10년 입찰", "region": "KR", "importance": 2, "local_date": "2026-10-13",
                                      "local_time": "10:30", "kind": "auction"})
    events.update(conn, eid, {"title": "국고채 10년 입찰", "region": "KR", "importance": 3, "local_date": "2026-10-14",
                              "local_time": "10:30", "memo": "응찰 부담"})
    got = events.list_events(conn, date(2026, 10, 9))
    assert [(e["title"], e["kst_date"], e["d_day"], e["importance"]) for e in got] == [("국고채 10년 입찰", "2026-10-14", 5, 3)]
    events.delete(conn, eid)
    assert events.list_events(conn, date(2026, 10, 9)) == []
    log = conn.execute("SELECT action, snapshot FROM event_log WHERE event_id=? ORDER BY id", (eid,)).fetchall()
    assert [r["action"] for r in log] == ["insert", "update", "delete"]
    assert json.loads(log[0]["snapshot"])["local_date"] == "2026-10-13"      # 이전 버전이 로그에 남아 있다


def test_event_validation(conn):
    bad = [{"title": "", "region": "KR", "local_date": "2026-10-13"},
           {"title": "x", "region": "XX", "local_date": "2026-10-13"},
           {"title": "x", "region": "KR", "local_date": "2026-10-33"},
           {"title": "x", "region": "KR", "local_date": "2026-10-13", "local_time": "25:99"},
           {"title": "x", "region": "KR", "local_date": "2026-10-13", "importance": 9},
           {"title": "x", "region": "KR", "local_date": "2026-10-13", "tz": "Mars/Base"}]
    for f in bad:
        with pytest.raises(Exception):
            events.create_manual(conn, f)


def test_list_includes_events_whose_kst_date_shifts_into_window(conn):
    # 현지 10-27 21:00 ET → KST 10-28 10:00. 기준일 10-28 에서 d_day=0
    events.create_manual(conn, {"title": "야간", "region": "US", "local_date": "2026-10-27", "local_time": "21:00"})
    got = events.list_events(conn, date(2026, 10, 28), days=3)
    assert got and got[0]["kst_date"] == "2026-10-28" and got[0]["d_day"] == 0


# --- 뷰 메모 --------------------------------------------------------------------
@pytest.mark.parametrize("side,entry,level,unit,expect", [
    ("long", 3.90, 3.983, "pct", (-8.3, "bp")),          # 금리(%) 롱 = 채권 롱: 금리 상승은 손실
    ("short", 3.90, 3.983, "pct", (8.3, "bp")),
    ("long", 42.0, 41.2, "bp", (-0.8, "bp")),            # 스프레드는 레벨 기준
    ("long", 106.04, 106.30, "price", (0.26, "pt")),     # 국채선물 가격도 레벨 기준
    ("short", 1335.0, 1342.8, "krw", (-7.8, "원")),
    ("long", 6600.0, 6625.93, "index", (25.93, "pt")),
])
def test_pnl_sign_and_unit(side, entry, level, unit, expect):
    v, u = userdata.pnl(side, entry, level, unit)
    assert v == pytest.approx(expect[0], abs=1e-6) and u == expect[1]


def put(conn, key, value, day, provider="ecos"):
    y, m, d = map(int, day.split("-"))
    db.upsert_observation(conn, Fetched(key, value, date(y, m, d), provider), "daily", "local")
    conn.commit()


def test_position_pnl_from_live_level_and_close(conn):
    put(conn, "ktb_3y", 3.983, "2026-10-08")
    pid = userdata.create_position(conn, {"product": "국고 3년", "series_key": "ktb_3y", "side": "long", "entry_level": "3.90",
                                          "entry_date": "2026-10-01", "target": 3.8, "stop": 4.1, "rationale": "베이시스"})
    p = userdata.list_positions(conn)[0]
    assert p["current"] == 3.983 and p["pnl"] == pytest.approx(-8.3) and p["pnl_unit"] == "bp" and p["pnl_kind"] == "open"
    assert p["side_hint"] == "롱 — 금리 하락 시 이익"
    userdata.update_position(conn, pid, {"product": "국고 3년", "series_key": "ktb_3y", "side": "long", "entry_level": 3.90,
                                         "entry_date": "2026-10-01", "exit_level": 3.95, "exit_date": "2026-10-08"})
    assert userdata.list_positions(conn) == []                                   # 청산분은 기본 목록에서 빠진다
    closed = userdata.list_positions(conn, include_closed=True)[0]
    assert closed["status"] == "closed" and closed["pnl"] == pytest.approx(-5.0) and closed["pnl_kind"] == "realized"
    actions = [r["action"] for r in conn.execute("SELECT action FROM position_log WHERE position_id=? ORDER BY id", (pid,))]
    assert actions == ["insert", "close"]


def test_position_without_series_has_no_auto_pnl_and_missing_level_is_blank(conn):
    userdata.create_position(conn, {"product": "임의 상품", "side": "short", "entry_level": 100, "entry_date": "2026-10-01"})
    userdata.create_position(conn, {"product": "국고 10년", "series_key": "ktb_10y", "side": "long", "entry_level": 4.4,
                                    "entry_date": "2026-10-01"})                 # ktb_10y 값이 DB 에 없다
    for p in userdata.list_positions(conn):
        assert p["pnl"] is None and p["current"] is None                          # 0 으로 채우지 않는다


def test_position_validation_and_delete_log(conn):
    base = {"product": "x", "side": "long", "entry_level": 1, "entry_date": "2026-10-01"}
    for bad in ({**base, "product": " "}, {**base, "side": "buy"}, {**base, "entry_level": ""}, {**base, "entry_level": "abc"},
                {**base, "series_key": "nope"}, {**base, "entry_date": "2026-1-1"}, {**base, "exit_level": 3}, {**base, "exit_date": "2026-10-02"}):
        with pytest.raises(Exception):
            userdata.create_position(conn, bad)
    pid = userdata.create_position(conn, base)
    userdata.delete_position(conn, pid)
    assert userdata.list_positions(conn, include_closed=True) == []
    assert [r["action"] for r in conn.execute("SELECT action FROM position_log ORDER BY id")] == ["insert", "delete"]
    with pytest.raises(LookupError):
        userdata.delete_position(conn, pid)


# --- CSV 수동 시세 ------------------------------------------------------------------
def test_csv_long_and_wide_formats(conn):
    r = userdata.import_manual_csv(conn, "2026-10-08,kr_cds_5y,3.52\n2026-10-07,kr_cds_5y,3.50\n")
    assert r["rows"] == 2 and r["insert"] == 2
    r2 = userdata.import_manual_csv(conn, "date,ndf_1m,kr_cds_5y\n2026-10-08,1342.5,\n2026-10-06,1340.0,41.5\n")
    assert r2["rows"] == 3                                                         # 빈 칸(미수집)은 건너뛴다
    n = conn.execute("SELECT COUNT(*) FROM observation WHERE origin='manual'").fetchone()[0]
    assert n == 5
    r3 = userdata.import_manual_csv(conn, "2026-10-08,kr_cds_5y,3.55")
    assert r3["update"] == 1


def test_csv_is_all_or_nothing(conn):
    with pytest.raises(ValueError) as e:
        userdata.import_manual_csv(conn, "2026-10-08,kr_cds_5y,3.52\n2026-10-08,ktb_3y,3.9\n2026-10-08,ndf_1m,abc")
    assert "2행" in str(e.value) and "3행" in str(e.value)
    assert conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == 0


@pytest.mark.parametrize("text", ["", "2999-01-01,kr_cds_5y,3.5", "2026-13-01,kr_cds_5y,3.5", "2026-10-08,kr_cds_5y",
                                  "2026-10-08,kr_cds_5y,nan", "date,kr_cds_5y\n2026-10-08,"])
def test_csv_rejections(conn, text):
    with pytest.raises(ValueError):
        userdata.import_manual_csv(conn, text)


# --- HTTP -----------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "u.db"
    monkeypatch.setenv("MARKETVIEW_DB_PATH", str(path))
    c = db.connect(path)
    db.migrate(c)
    events.sync_official(c)
    c.close()
    from marketview.app import app
    return TestClient(app)


def test_http_events_flow(client):
    got = client.get("/api/events", params={"base": "2026-10-09", "days": 28}).json()
    titles = [(e["title"], e["kst_date"], e["kst_time"]) for e in got["events"]]
    assert ("금융통화위원회 기준금리 결정", "2026-10-22", None) in titles
    assert ("FOMC 금리 결정", "2026-10-29", "03:00") in titles                         # 14:00 EDT → 다음날 03:00 KST
    created = client.post("/api/events", json={"title": "국고채 3년 입찰", "region": "KR", "importance": 2,
                                               "local_date": "2026-10-15", "local_time": "10:30", "kind": "auction"})
    assert created.status_code == 200
    eid = created.json()["id"]
    assert client.put(f"/api/events/{eid}", json={"memo": "응찰률 확인"}).status_code == 200
    assert client.delete(f"/api/events/{eid}").json() == {"deleted": True}
    assert client.delete(f"/api/events/{eid}").status_code == 404
    assert client.post("/api/events", json={"title": "x", "region": "KR"}).status_code == 400          # 날짜 누락
    assert client.get("/api/events", params={"base": "2026-1-1"}).status_code == 400
    fomc_id = next(e["id"] for e in got["events"] if e["title"] == "FOMC 금리 결정")
    assert client.put(f"/api/events/{fomc_id}", json={"local_date": "2026-10-29"}).status_code == 400   # 공식 일정은 잠금


def test_http_positions_csv(client):
    assert client.get("/api/notes").status_code == 404 and client.get("/note").status_code == 404      # 뷰 작성 기능은 없다
    body = {"product": "USD/KRW", "series_key": "usdkrw", "side": "long", "entry_level": 1335, "entry_date": "2026-10-02"}
    pid = client.post("/api/positions", json=body).json()["id"]
    assert client.get("/api/positions").json()["positions"][0]["product"] == "USD/KRW"
    assert client.put(f"/api/positions/{pid}", json={**body, "target": 1360}).status_code == 200
    assert client.post("/api/positions", json={"product": "x"}).status_code == 422                   # 필수 필드 누락
    assert client.post("/api/positions", json={**body, "side": "buy"}).status_code == 400
    assert any(t["key"] == "ktb_10s3s" for t in client.get("/api/trackables").json()["items"])
    assert client.delete(f"/api/positions/{pid}").status_code == 200
    assert client.post("/api/observations/csv", json={"text": "2026-10-08,kr_cds_5y,3.52"}).json()["rows"] == 1
    assert client.post("/api/observations/csv", json={"text": "2026-10-08,ktb_3y,3.9"}).status_code == 400


def test_open_position_current_uses_close_date_not_intraday(conn):
    from datetime import timedelta
    today = userdata.queries.today_kst()
    d1 = (today - timedelta(days=1)).isoformat()
    put(conn, "kospi", 6000.0, d1, "krx")
    db.upsert_observation(conn, Fetched("kospi", 6200.0, today, "yahoo", obs_ts=today.isoformat() + "T10:00:00+09:00"),
                          "intraday", "local")
    conn.commit()
    userdata.create_position(conn, {"product": "KOSPI", "series_key": "kospi", "side": "long", "entry_level": 5900,
                                    "entry_date": (today - timedelta(days=3)).isoformat()})
    p = userdata.list_positions(conn)[0]
    assert p["current"] == 6000.0 and p["current_obs"] == d1      # 장중 6200 이 아니라 마감 확정값


def test_position_exit_before_entry_rejected_and_wrong_side_warns(conn):
    base = {"product": "x", "side": "long", "entry_level": 100, "entry_date": "2026-10-05"}
    with pytest.raises(ValueError, match="청산일"):
        userdata.create_position(conn, {**base, "exit_level": 101, "exit_date": "2026-10-01"})
    assert userdata.warnings_for({**base, "target": 110, "stop": 95}) == []
    assert len(userdata.warnings_for({**base, "target": 90, "stop": 105})) == 2          # 롱: 목표가 아래, 손절이 위
    assert userdata.warnings_for({**base, "side": "short", "target": 90, "stop": 105}) == []
    assert len(userdata.warnings_for({**base, "side": "short", "target": 110})) == 1


def test_rate_long_is_bond_long_for_warnings_spread_stays_level():
    rate = {"product": "국고 10년", "series_key": "ktb_10y", "side": "long", "entry_level": 4.229, "entry_date": "2026-10-14"}
    assert userdata.warnings_for({**rate, "target": 4.129, "stop": 4.329}) == []     # 채권 롱: 목표는 금리 아래, 손절은 위
    warns = userdata.warnings_for({**rate, "target": 4.329, "stop": 4.129})
    assert len(warns) == 2 and "금리 하락 시 이익" in warns[0]
    spread = {"product": "10−3", "series_key": "ktb_10s3s", "side": "long", "entry_level": 37.8, "entry_date": "2026-10-14"}
    assert userdata.warnings_for({**spread, "target": 45, "stop": 33}) == []           # 스프레드 롱 = 확대 시 이익
    assert userdata.side_hint("long", "bp") == "롱 — 레벨 상승 시 이익"
    assert userdata.side_hint("short", "pct") == "숏 — 금리 상승 시 이익"


def test_abort_stale_closes_running_runs(conn):
    from marketview import ingest
    ingest.Run(conn, "daily", "local")
    done = ingest.Run(conn, "news", "local"); done.finish()
    assert ingest.abort_stale(conn) == 1
    assert [r[0] for r in conn.execute("SELECT status FROM ingest_run ORDER BY id")] == ["failed", "ok"]


def _manual_value(conn, key, day):
    return conn.execute("SELECT value FROM observation WHERE series_key=? AND obs_date=?", (key, day)).fetchone()[0]


TAB, NL = chr(9), chr(10)


def test_csv_tab_separated_keeps_thousands_comma(conn):
    """엑셀·웹 표 복사(탭 구분, 천 단위 쉼표): 예전에는 '1,385.20' 이 쪼개져 ndf_1m=1.0 이 조용히 저장됐다."""
    userdata.import_manual_csv(conn, TAB.join(["2026-10-08", "ndf_1m", "1,385.20"]))
    assert _manual_value(conn, "ndf_1m", "2026-10-08") == 1385.2
    userdata.import_manual_csv(conn, NL.join([TAB.join(["date", "ndf_1m", "kr_cds_5y"]), TAB.join(["2026-10-07", "1,380.5", "41.5"])]))
    assert _manual_value(conn, "ndf_1m", "2026-10-07") == 1380.5 and _manual_value(conn, "kr_cds_5y", "2026-10-07") == 41.5


def test_csv_unquoted_thousands_comma_is_rejected_not_truncated(conn):
    with pytest.raises(ValueError, match="칸이 너무 많습니다"):
        userdata.import_manual_csv(conn, "2026-10-08,ndf_1m,1,385.20")
    with pytest.raises(ValueError, match="칸이 너무 많습니다"):
        userdata.import_manual_csv(conn, NL.join(["date,ndf_1m", "2026-10-08,1,385.20"]))
    assert conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == 0


def test_csv_quoted_thousands_and_trailing_empty_cell_are_fine(conn):
    userdata.import_manual_csv(conn, NL.join(['2026-10-08,ndf_1m,"1,385.20"', "2026-10-09,kr_cds_5y,41.5,"]))
    assert _manual_value(conn, "ndf_1m", "2026-10-08") == 1385.2 and _manual_value(conn, "kr_cds_5y", "2026-10-09") == 41.5


def test_manual_event_update_applies_kind_and_ignores_null_importance(conn):
    eid = events.create_manual(conn, {"title": "입찰", "region": "KR", "importance": 3, "local_date": "2026-10-13", "kind": "auction"})
    events.update(conn, eid, {"kind": "other", "importance": None, "memo": "m"})
    row = conn.execute("SELECT kind, importance, memo FROM event WHERE id=?", (eid,)).fetchone()
    assert (row["kind"], row["importance"], row["memo"]) == ("other", 3, "m")          # None 이 중요도를 2 로 되돌리지 않는다
    events.update(conn, eid, {"kind": "auction"})
    assert conn.execute("SELECT kind FROM event WHERE id=?", (eid,)).fetchone()["kind"] == "auction"
