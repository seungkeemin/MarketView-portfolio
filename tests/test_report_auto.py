"""리포트 자동 생성·꺼져 있던 날 따라잡기, 그리고 사후 생성용 포지션 기준일 계산."""

from datetime import date, datetime

import pytest

from marketview import db, userdata
from marketview.collect.base import Fetched
from marketview.config.settings import KST
from marketview.report import auto, data, pdf, render, run


def at(y, m, d, hh=8, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETVIEW_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("MARKETVIEW_DRIVE_ROOT", str(tmp_path / "drive"))
    (tmp_path / "drive").mkdir()
    monkeypatch.setattr(pdf, "html_to_pdf", lambda h, p: p.write_bytes(b"%PDF-1.4 " + b"x" * 2000))
    return tmp_path


def _since(conn, day):
    auto._set_since(conn, day)
    conn.commit()


def test_first_evaluation_sets_baseline_and_waits_for_morning(conn):
    assert auto.due_days(conn, at(2026, 9, 14, 6, 0)) == []                       # 월요일 06:00 — 아직 이르다
    assert auto._state(conn, "report_auto_since") == "2026-09-14"
    assert auto.due_days(conn, at(2026, 9, 14, 7, 30)) == [("2026-09-14", "auto")]


def test_off_days_are_caught_up_by_date(conn):
    _since(conn, "2026-09-14")                       # 월 기준일, 화·수는 꺼져 있었고 목요일 아침에 켬
    assert auto.due_days(conn, at(2026, 9, 17, 8, 0)) == [
        ("2026-09-14", "catchup"), ("2026-09-15", "catchup"), ("2026-09-16", "catchup"), ("2026-09-17", "auto")]


def test_weekends_skipped(conn):
    _since(conn, "2026-09-11")                       # 금
    assert auto.due_days(conn, at(2026, 9, 14, 8, 0)) == [("2026-09-11", "catchup"), ("2026-09-14", "auto")]


def test_existing_report_of_any_origin_excludes_day(conn):
    _since(conn, "2026-09-14")
    conn.execute("INSERT INTO report_run (report_date,version,md_path,created_at) VALUES ('2026-09-15',1,'x','t')")
    conn.commit()
    assert [d for d, _ in auto.due_days(conn, at(2026, 9, 16, 8, 0))] == ["2026-09-14", "2026-09-16"]


def test_catchup_is_capped_to_most_recent_days(conn):
    _since(conn, "2026-08-03")
    days = auto.due_days(conn, at(2026, 9, 17, 8, 0))
    catch = [d for d, o in days if o == "catchup"]
    assert len(catch) == auto.MAX_CATCHUP and catch[-1] == "2026-09-16" and days[-1] == ("2026-09-17", "auto")


def test_gives_up_after_repeated_failures(conn):
    _since(conn, "2026-09-14")
    conn.execute("INSERT INTO sync_state (key,value,updated_at) VALUES ('report_auto_fail:2026-09-14','3','t')")
    conn.commit()
    assert [d for d, _ in auto.due_days(conn, at(2026, 9, 15, 8, 0))] == ["2026-09-15"]


def test_run_due_generates_records_origin_and_is_idempotent(conn, env):
    _since(conn, "2026-09-14")
    now = at(2026, 9, 16, 8, 0)
    r = auto.run_due(conn, now)
    assert r["state"] == "ok" and [(g["date"], g["origin"]) for g in r["generated"]] == [
        ("2026-09-14", "catchup"), ("2026-09-15", "catchup"), ("2026-09-16", "auto")]
    rows = conn.execute("SELECT report_date, origin FROM report_run ORDER BY report_date").fetchall()
    assert [(x["report_date"], x["origin"]) for x in rows] == [("2026-09-14", "catchup"), ("2026-09-15", "catchup"), ("2026-09-16", "auto")]
    assert (env / "reports/2026/09/2026-09-15.md").exists() and (env / "drive/reports/2026/09/2026-09-15.pdf").exists()
    assert auto.run_due(conn, now)["state"] == "idle"                    # 두 번째는 할 일 없음
    assert conn.execute("SELECT COUNT(*) FROM report_run").fetchone()[0] == 3


def test_disabled_and_reenable_resets_baseline(conn, env):
    _since(conn, "2026-08-03")
    auto.set_enabled(conn, False)
    assert auto.run_due(conn, at(2026, 9, 16, 8, 0))["state"] == "off"
    assert conn.execute("SELECT COUNT(*) FROM report_run").fetchone()[0] == 0
    auto.set_enabled(conn, True, today=date(2026, 9, 16))             # 끈 동안의 날들을 몰아서 만들지 않는다
    assert [d for d, _ in auto.due_days(conn, at(2026, 9, 16, 8, 0))] == ["2026-09-16"]


def test_busy_lock_skips_cycle(conn, env):
    _since(conn, "2026-09-16")
    assert run.generate_lock.acquire(blocking=False)
    try:
        assert auto.run_due(conn, at(2026, 9, 16, 8, 0))["state"] == "busy"
    finally:
        run.generate_lock.release()
    assert conn.execute("SELECT COUNT(*) FROM report_run").fetchone()[0] == 0


def test_failure_counted_and_other_days_continue(conn, env, monkeypatch):
    _since(conn, "2026-09-14")
    real = run.generate

    def flaky(c, day=None, origin="manual"):
        if day == "2026-09-14":
            raise RuntimeError("boom api_key=SECRETKEY")
        return real(c, day, origin=origin)
    monkeypatch.setattr(run, "generate", flaky)
    r = auto.run_due(conn, at(2026, 9, 15, 8, 0))
    assert r["state"] == "error" and [g["date"] for g in r["generated"]] == ["2026-09-15"]
    assert r["errors"][0]["attempt"] == 1 and "SECRETKEY" not in str(r)
    assert not run.generate_lock.locked()                                # 실패해도 잠금은 풀린다
    assert auto._state(conn, "report_auto_fail:2026-09-14") == "1"


def _pos(conn, **kw):
    f = dict(product="국고 3년", series_key="ktb_3y", side="long", entry_level=3.90, entry_date="2026-10-06",
             target=None, stop=None, rationale=None, exit_level=None, exit_date=None)
    f.update(kw)
    return userdata.create_position(conn, f)


def _obs(conn, key, d, v):
    y, m, dd = map(int, d.split("-"))
    db.upsert_observation(conn, Fetched(key, v, date(y, m, dd), "ecos"), "daily", "local")
    conn.commit()


def test_positions_asof_uses_that_days_state(conn):
    _obs(conn, "ktb_3y", "2026-10-07", 3.95)
    _obs(conn, "ktb_3y", "2026-10-10", 4.10)
    open_one = _pos(conn)                                                   # 10-06 진입, 아직 열림
    closed_later = _pos(conn, product="B", exit_level=4.05, exit_date="2026-10-09")
    _pos(conn, product="C", entry_date="2026-10-09")                       # 10-09 진입 → 10-07 시점엔 없다
    at7 = {p["product"]: p for p in userdata.list_positions(conn, asof="2026-10-07")}
    assert set(at7) == {"국고 3년", "B"}
    assert at7["B"]["status"] == "open" and at7["B"]["pnl_kind"] == "open"  # 청산은 10-09 → 10-07 엔 열려 있었다
    assert at7["국고 3년"]["current"] == 3.95 and round(at7["국고 3년"]["pnl"], 6) == -5.0   # 채권 롱: 금리 3.90→3.95 → −5bp
    now = {p["product"]: p for p in userdata.list_positions(conn)}
    assert set(now) == {"국고 3년", "C"} and now["국고 3년"]["current"] == 4.10        # asof 없으면 기존 동작
    assert open_one and closed_later


def test_past_report_has_note_and_today_does_not(conn, env):
    from marketview.config.settings import today_kst
    past = render.to_markdown(data.build(conn, "2026-10-08", {}, [], True))
    today = render.to_markdown(data.build(conn, today_kst().isoformat(), {}, [], True))
    assert "기준일 이후에 만든 리포트" in past and "기준일 이후에 만든 리포트" not in today
