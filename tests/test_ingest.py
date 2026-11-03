"""일별 수집 시작점(빈 구간 다시 받기)과 여러 날짜를 받는 소스의 일부 실패 기록."""

from datetime import date, timedelta

from marketview import db, ingest
from marketview.collect import krx, smbs
from marketview.collect.base import Failed, Fetched

TODAY = date(2026, 10, 15)


def _put(conn, key, days, provider="ecos", origin="local"):
    for d in days:
        db.upsert_observation(conn, Fetched(key, 3.0, d, provider), "daily", origin)
    conn.commit()


def _weekdays(start, end):
    return [start + timedelta(n) for n in range((end - start).days + 1) if (start + timedelta(n)).weekday() < 5]


def test_start_is_last_minus_overlap_when_contiguous(conn):
    _put(conn, "ktb_3y", _weekdays(date(2026, 6, 1), date(2026, 10, 14)))
    assert ingest._start_for(conn, "ktb_3y", "ecos", TODAY) == date(2026, 10, 7)


def test_holiday_gap_is_not_a_hole(conn):
    # 2025 추석처럼 8일 비는 것은 정상 휴장이다
    _put(conn, "ktb_3y", _weekdays(date(2026, 8, 1), date(2026, 9, 25)) + _weekdays(date(2026, 10, 3), date(2026, 10, 14)))
    assert ingest._start_for(conn, "ktb_3y", "ecos", TODAY) == date(2026, 10, 7)


def test_cloud_merge_after_long_absence_backfills_the_hole(conn):
    """앱이 오래 꺼져 있다가 켜지면 클라우드가 최근 값만 먼저 채운다. 그 앞의 빈 구간을 건너뛰지 않는다."""
    _put(conn, "ktb_3y", _weekdays(date(2026, 6, 1), date(2026, 7, 1)))
    _put(conn, "ktb_3y", _weekdays(date(2026, 9, 1), date(2026, 10, 14)), origin="cloud")
    assert ingest._start_for(conn, "ktb_3y", "ecos", TODAY) == date(2026, 7, 1)


def test_hole_starting_before_lookback_window_is_found(conn):
    _put(conn, "ktb_3y", _weekdays(date(2026, 1, 5), date(2026, 3, 2)))
    _put(conn, "ktb_3y", _weekdays(date(2026, 8, 25), date(2026, 10, 14)), origin="cloud")      # 120일 창 안에는 이것뿐
    assert ingest._start_for(conn, "ktb_3y", "ecos", TODAY) == date(2026, 3, 2)


def test_dates_held_by_another_provider_are_not_a_hole(conn):
    """KOSPI 는 KRX 가 야후 값을 대체한다. 야후 행 사이가 비어 보여도 KRX 행이 있으면 빈 구간이 아니다."""
    _put(conn, "kospi", [date(2026, 5, 1), date(2026, 10, 14)], provider="yahoo")
    _put(conn, "kospi", _weekdays(date(2026, 5, 4), date(2026, 10, 13)), provider="krx")
    assert ingest._start_for(conn, "kospi", "yahoo", TODAY) == date(2026, 10, 7)


def test_first_run_goes_back_history_years(conn):
    assert ingest._start_for(conn, "ktb_3y", "ecos", TODAY) == TODAY - timedelta(days=365 * 3)


def _results(conn, run_id):
    return {r["series_key"]: (r["status"], r["message"]) for r in conn.execute(
        "SELECT series_key, status, message FROM ingest_result WHERE run_id=?", (run_id,))}


def test_krx_partial_failure_is_an_error_not_ok(conn, monkeypatch):
    """일부 날짜만 실패해도 받은 행은 저장하고 오류로 남긴다 → 경고에 보이고, 회차가 partial 이라 1시간 뒤 다시 받는다."""
    got = [Fetched(k, 105.0, date(2026, 10, 13), "krx") for k in ("ktbf_3y", "ktbf_10y", "ktbf_30y", "ktbf_3y_oi", "ktbf_10y_oi")]
    monkeypatch.setattr(krx, "fetch_range", lambda st, end: (got, [Failed("krx", "2026-10-14: HTTP 500"), Failed("krx", "시간 초과")]))
    monkeypatch.setattr(krx, "fetch_index_range", lambda key, st, end: (
        [Fetched(key, 2600.0, date(2026, 10, 13), "krx")], [Failed("krx", "2026-10-14: HTTP 500")]))
    assert ingest.run_daily(conn, TODAY, only={"krx"}, log=lambda m: None) == "failed"      # krx 만 돌렸고 전부 일부 실패
    rid = conn.execute("SELECT MAX(id) FROM ingest_run").fetchone()[0]
    res = _results(conn, rid)
    assert res["ktbf_3y"] == ("error", "수신 1행 · 일부 실패 2026-10-14: HTTP 500 외 1건")
    assert res["kospi"][0] == "error" and "일부 실패" in res["kospi"][1]
    assert conn.execute("SELECT value FROM observation WHERE series_key='ktbf_3y'").fetchone()[0] == 105.0   # 받은 행은 저장
    assert any(f["series_key"] == "ktbf_3y" for f in ingest.latest_failures(conn))


def test_smbs_partial_failure_is_reported_per_tenor(conn, monkeypatch):
    monkeypatch.setattr(smbs, "fetch_range", lambda st, end: [
        Fetched("irs_3y", 2.9, date(2026, 10, 13), "smbs"), Fetched("irs_5y", 3.0, date(2026, 10, 13), "smbs"),
        Failed("irs", "연속 실패 5회 — 중단")])
    ingest.run_daily(conn, TODAY, only={"smbs"}, log=lambda m: None)
    res = _results(conn, conn.execute("SELECT MAX(id) FROM ingest_run").fetchone()[0])
    assert set(res) == {"irs_3y", "irs_5y"} and all(s == "error" and "연속 실패" in m for s, m in res.values())


def test_full_success_is_still_ok(conn, monkeypatch):
    monkeypatch.setattr(smbs, "fetch_range", lambda st, end: [Fetched("irs_3y", 2.9, date(2026, 10, 13), "smbs")])
    assert ingest.run_daily(conn, TODAY, only={"smbs"}, log=lambda m: None) == "ok"


def test_record_does_not_hold_write_lock(conn, tmp_path):
    """뉴스 단계가 RSS 결과를 기록한 뒤 텔레그램을 받는 동안에도 다른 단계(일별·이벤트)가 바로 쓸 수 있어야 한다."""
    run = ingest.Run(conn, "news", "local")
    run.record("rss", "feed", "ok", 1, None)              # 여기서 커밋하지 않으면 아래 쓰기가 잠금에 막힌다
    other = db.connect(tmp_path / "t.db")
    try:
        other.execute("PRAGMA busy_timeout=0")
        ingest.Run(other, "daily", "local").finish()       # 잠금이 남아 있으면 sqlite3.OperationalError: database is locked
    finally:
        other.close()
    run.finish()
