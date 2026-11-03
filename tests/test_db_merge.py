from datetime import date

import pytest

from marketview import db
from marketview.collect.base import Fetched
from marketview.config.settings import NY, canonical_date


def obs(value, d=date(2026, 10, 8), key="ktb_3y", ts="", provider="ecos"):
    return Fetched(key, value, d, provider, obs_ts=ts)


def count(conn, table="observation"):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_migrate_is_idempotent(conn):
    assert db.migrate(conn) == []
    assert count(conn, "series_def") > 50


def test_failed_migration_rolls_back_whole_file(tmp_path, monkeypatch):
    """마이그레이션 파일 중간 문장이 실패하면 앞 문장도 되돌리고 기록하지 않는다 → 고친 뒤 다시 적용된다."""
    mig = tmp_path / "migrations"
    mig.mkdir()
    for p in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
        (mig / p.name).write_bytes(p.read_bytes())
    bad = mig / "999_test.sql"
    bad.write_text("CREATE TABLE t_ok (x INTEGER);\nCREATE TABLE t_bad (x INTEGER;\n", encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", mig)
    c = db.connect(tmp_path / "m.db")
    try:
        with pytest.raises(Exception):
            db.migrate(c)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "t_ok" not in tables and "observation" in tables                   # 앞 파일들은 적용, 실패한 파일은 통째로 없음
        assert c.execute("SELECT COUNT(*) FROM schema_version WHERE version='999_test.sql'").fetchone()[0] == 0
        bad.write_text("CREATE TABLE t_ok (x INTEGER);\nCREATE TABLE t_bad (x INTEGER);\n", encoding="utf-8")
        assert db.migrate(c) == ["999_test.sql"]
    finally:
        c.close()


def test_same_value_is_not_stored_twice(conn):
    assert db.upsert_observation(conn, obs(3.98), "daily", "local") == "insert"
    assert db.upsert_observation(conn, obs(3.98), "daily", "cloud") == "same"     # 클라우드 병합 중복
    assert count(conn) == 1 and count(conn, "observation_log") == 1


def test_changed_value_updates_and_logs_previous(conn):
    db.upsert_observation(conn, obs(3.98), "daily", "local")
    assert db.upsert_observation(conn, obs(3.99), "daily", "local") == "update"
    assert count(conn) == 1
    log = conn.execute("SELECT action, prev_value, new_value FROM observation_log ORDER BY id").fetchall()
    assert [(r["action"], r["prev_value"], r["new_value"]) for r in log] == [("insert", None, 3.98), ("update", 3.98, 3.99)]


def test_manual_value_is_not_overwritten_by_auto(conn):
    db.upsert_observation(conn, obs(23.1, key="kr_cds_5y", provider="user"), "daily", "manual")
    assert db.upsert_observation(conn, obs(99.0, key="kr_cds_5y"), "daily", "local") == "skipped"
    assert conn.execute("SELECT value FROM observation").fetchone()[0] == 23.1


def test_daily_and_intraday_are_separate_rows(conn):
    db.upsert_observation(conn, obs(6625.9, key="kospi", provider="yahoo"), "daily", "local")
    db.upsert_observation(conn, obs(6630.0, key="kospi", ts="2026-10-08T15:20:00+09:00", provider="yahoo"),
                          "intraday", "local")
    db.upsert_observation(conn, obs(6631.0, key="kospi", ts="2026-10-08T15:30:00+09:00", provider="yahoo"),
                          "intraday", "local")
    kinds = [r[0] for r in conn.execute("SELECT kind FROM observation ORDER BY kind, obs_ts")]
    assert kinds == ["daily", "intraday", "intraday"]


def test_intraday_same_quote_time_dedupes(conn):
    f = obs(6630.0, key="kospi", ts="2026-10-08T15:20:00+09:00", provider="yahoo")
    db.upsert_observation(conn, f, "intraday", "local")
    assert db.upsert_observation(conn, f, "intraday", "local") == "same"


def test_unknown_series_is_rejected(conn):
    with pytest.raises(Exception):
        db.upsert_observation(conn, obs(1.0, key="not_a_series"), "daily", "local")


def test_canonical_date_is_strict():
    assert canonical_date("2026-10-08") == "2026-10-08"
    for bad in ("2026-8-11", "20261008", "2026/10/08", ""):
        with pytest.raises(ValueError):
            canonical_date(bad)


def test_new_york_dst_is_applied():
    from datetime import datetime, timezone
    summer = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc).astimezone(NY)
    winter = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc).astimezone(NY)
    assert summer.utcoffset().total_seconds() == -4 * 3600
    assert winter.utcoffset().total_seconds() == -5 * 3600


def test_official_krx_close_is_not_reverted_by_yahoo(conn):
    db.upsert_observation(conn, obs(6000.0, key="kospi", provider="yahoo"), "daily", "local")
    assert db.upsert_observation(conn, obs(6035.35, key="kospi", provider="krx"), "daily", "local") == "update"
    assert db.upsert_observation(conn, obs(6001.0, key="kospi", provider="yahoo"), "daily", "local") == "skipped"
    row = conn.execute("SELECT value, provider FROM observation").fetchone()
    assert (row["value"], row["provider"]) == (6035.35, "krx")


def test_same_value_krx_replaces_yahoo_source_label(conn):
    db.upsert_observation(conn, obs(4107.5, key="kospi", provider="yahoo"), "daily", "local")
    assert db.upsert_observation(conn, obs(4107.5, key="kospi", provider="krx"), "daily", "local") == "update"
    assert conn.execute("SELECT provider FROM observation").fetchone()[0] == "krx"
