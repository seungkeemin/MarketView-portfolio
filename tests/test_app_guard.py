from datetime import timedelta

from fastapi.testclient import TestClient

from marketview import app as appmod
from marketview.config.settings import today_kst


def test_untrusted_host_rejected():
    c = TestClient(appmod.app)          # lifespan 은 with 없이는 돌지 않는다
    assert c.get("/api/status", headers={"host": "evil.example.com"}).status_code == 400


def test_daily_due_on_new_day_or_after_interval():
    appmod._last_daily.update(date=None, at=0.0)
    assert appmod.daily_due()
    appmod._last_daily.update(date=today_kst(), at=appmod.time.monotonic())
    assert not appmod.daily_due()
    appmod._last_daily.update(date=today_kst() - timedelta(days=1))
    assert appmod.daily_due()
    appmod._last_daily.update(date=today_kst(), at=appmod.time.monotonic() - appmod.DAILY_REFRESH_SEC - 1)
    assert appmod.daily_due()


def test_app_errors_show_in_failures_only_when_recent(conn):
    from marketview import ingest
    run = ingest.Run(conn, "stage", "local")
    run.record("app", "stage", "error", 0, "boom")
    run.finish()
    assert {"source": "app", "series_key": "stage", "message": "boom"} in ingest.latest_failures(conn)
    conn.execute("UPDATE ingest_run SET started_at='2020-01-01T00:00:00+09:00'")
    assert ingest.latest_failures(conn) == []


def test_failed_daily_retries_after_an_hour():
    appmod._mark_daily(True)
    assert not appmod.daily_due()
    appmod._mark_daily(False)
    assert not appmod.daily_due()          # 방금 실패: 바로 다시 두드리지 않는다
    appmod._last_daily["at"] -= appmod.DAILY_RETRY_SEC
    assert appmod.daily_due()


def test_to_float_rejects_infinity():
    from marketview.collect.base import to_float
    assert to_float("inf") is None and to_float("-Infinity") is None and to_float("1,234.5") == 1234.5


def _writes_client(monkeypatch):
    started = []
    monkeypatch.setattr(appmod.api, "start_collect", lambda: started.append(1))
    return TestClient(appmod.app), started


def test_cross_site_writes_are_rejected(monkeypatch):
    """다른 사이트의 페이지가 로컬 서버에 쓰기 요청(수집·리포트·포지션)을 보내지 못한다."""
    c, started = _writes_client(monkeypatch)
    evil = {"origin": "http://evil.example.com"}
    assert c.post("/api/refresh", headers=evil).status_code == 403
    assert c.post("/api/refresh", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert c.post("/api/refresh", headers={"sec-fetch-site": "same-site"}).status_code == 403          # 다른 포트의 로컬 페이지도 막는다
    assert c.post("/api/refresh", headers={"origin": "null"}).status_code == 403                       # sandbox iframe 등
    assert c.put("/api/report/settings", json={"auto": False}, headers=evil).status_code == 403
    assert c.delete("/api/positions/1", headers=evil).status_code == 403
    assert started == []


def test_same_origin_and_direct_writes_still_work(monkeypatch):
    c, started = _writes_client(monkeypatch)
    ok_origin = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
    assert c.post("/api/refresh", headers=ok_origin).json() == {"started": True}
    assert c.post("/api/refresh").json() == {"started": True}                                         # 헤더 없는 직접 호출(스크립트·테스트)
    assert started == [1, 1]


def test_cross_site_reads_are_not_blocked_by_the_guard(monkeypatch, tmp_path, conn):
    monkeypatch.setenv("MARKETVIEW_DB_PATH", str(tmp_path / "t.db"))   # conn 픽스처가 마이그레이션한 임시 DB. 실제 DB 가 없는 환경에서도 돈다
    c, _ = _writes_client(monkeypatch)
    assert c.get("/api/status", headers={"origin": "http://evil.example.com"}).status_code != 403       # 읽기는 SOP 가 응답을 막는다


def test_partial_daily_retries_after_an_hour_but_ok_waits_six(monkeypatch):
    from marketview import ingest
    for status, retry in (("partial", True), ("failed", True), ("ok", False)):
        appmod._last_daily.update(date=None, at=0.0)
        monkeypatch.setattr(ingest, "run_daily", lambda conn, log: status)
        appmod._daily_stage(None)
        assert not appmod.daily_due()
        appmod._last_daily["at"] -= appmod.DAILY_RETRY_SEC + 1
        assert appmod.daily_due() is retry
