"""백업·CSV·복원: 일관성, 보관 규칙, Drive 실패 격리, 텔레그램 제외, 복원 안전."""

import gzip
import sqlite3
from datetime import date

import pytest

from marketview import backup, db
from marketview.collect.base import Fetched

DAY = date(2026, 10, 10)
SECRET = "텔레그램비밀문장XYZ"


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, d in (("BACKUPS", "backups"), ("EXPORTS", "exports"), ("DRIVE_ROOT", "drive")):
        monkeypatch.setenv(f"MARKETVIEW_{k}_DIR" if k != "DRIVE_ROOT" else "MARKETVIEW_DRIVE_ROOT", str(tmp_path / d))
    (tmp_path / "drive").mkdir()
    return tmp_path


@pytest.fixture
def full(conn):
    db.upsert_observation(conn, Fetched("ktb_3y", 3.98, date(2026, 10, 8), "ecos"), "daily", "local")
    conn.execute("INSERT INTO news (link,title,source,feed_id,published_at,title_key,fetched_at) "
                 "VALUES ('https://e.com/1','제목, 쉼표 포함','소스','f','2026-10-09T10:00:00+09:00','k','2026-10-09T10:00:00+09:00')")
    conn.execute("INSERT INTO tg_message (channel,msg_id,body,posted_at,fetched_at) VALUES ('@c',1,?,?,?)",
                 (SECRET, "2026-10-09T10:00:00+09:00", "2026-10-09T10:00:00+09:00"))
    conn.execute("INSERT INTO view_note (note_date,tag,body,created_at,updated_at) VALUES ('2026-10-09','채권','메모','x','x')")
    conn.commit()
    return conn


def _restore_counts(gz, tmp):
    out = tmp / f"r{len(list(tmp.glob('r*.db')))}.db"
    backup.restore(gz, out)
    return backup.table_counts(out), out


def test_daily_backup_restores_with_same_counts(full, env):
    r = backup.backup_daily(full, DAY)
    assert (r["local"], r["drive"]) == ("ok", "ok")
    gz = env / "backups/daily/marketview-20261010.db.gz"
    counts, _ = _restore_counts(gz, env)
    live = {t: full.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in counts}
    counts.pop("sync_state"), live.pop("sync_state")     # 백업 결과 기록은 스냅샷 뒤에 쓰인다
    assert counts == live and counts["tg_message"] == 1 and counts["observation"] == 1


def test_drive_copy_has_no_telegram_even_in_raw_bytes(full, env):
    backup.backup_daily(full, DAY)
    for f in ((env / "drive/backups/daily/marketview-20261010.db.gz"), (env / "drive/backups/monthly/marketview-202610.db.gz")):
        raw = gzip.open(f).read()
        assert SECRET.encode() not in raw                    # FTS 인덱스·빈 페이지에도 남지 않는다
        counts, _ = _restore_counts(f, env)
        assert counts["tg_message"] == 0 and counts["news"] == 1 and counts["observation"] == 1
    assert SECRET.encode() in gzip.open(env / "backups/daily/marketview-20261010.db.gz").read()   # 로컬엔 있다


def test_same_day_not_redone_without_force(full, env):
    backup.backup_daily(full, DAY)
    assert backup.backup_daily(full, DAY)["local"] == "exists"
    assert backup.backup_daily(full, DAY, force=True)["local"] == "ok"


def test_retention_keeps_30_daily_and_only_touches_pattern(full, env):
    for d in (env / "backups/daily", env / "drive/backups/daily"):
        d.mkdir(parents=True)
        for i in range(1, 29):                                # 2026-09-01.. 2026-09-28 : 28개
            (d / f"marketview-202609{i:02d}.db.gz").write_bytes(b"x")
        (d / "notes.txt").write_text("keep")
        (d / "marketview-20260101.db").write_bytes(b"keep")   # 패턴 밖 (.gz 아님)
    backup.backup_daily(full, date(2026, 10, 1))
    backup.backup_daily(full, date(2026, 10, 2))
    r = backup.backup_daily(full, date(2026, 10, 3))          # 31번째 → 가장 오래된 하나가 빠진다
    for d in (env / "backups/daily", env / "drive/backups/daily"):
        names = sorted(f.name for f in d.iterdir() if backup.DAILY_RE.match(f.name))
        assert len(names) == 30 and names[0] == "marketview-20260902.db.gz" and names[-1] == "marketview-20261003.db.gz"
        assert (d / "notes.txt").exists() and (d / "marketview-20260101.db").exists()
    assert "marketview-20260901.db.gz" in r["pruned"]


def test_monthly_is_permanent_and_last_of_month(full, env):
    backup.backup_daily(full, date(2026, 9, 30))
    sep = (env / "backups/monthly/marketview-202609.db.gz").read_bytes()
    full.execute("INSERT INTO view_note (note_date,tag,body,created_at,updated_at) VALUES ('2026-10-10','주식','later','x','x')")
    full.commit()
    backup.backup_daily(full, date(2026, 10, 1))
    backup.backup_daily(full, date(2026, 10, 5))
    assert (env / "backups/monthly/marketview-202609.db.gz").read_bytes() == sep       # 지난달 파일은 그대로
    counts, _ = _restore_counts(env / "backups/monthly/marketview-202610.db.gz", env)
    assert counts["view_note"] == 2                                                    # 10월 파일 = 10월 마지막 백업


def test_drive_missing_keeps_local_and_creates_nothing(full, env, monkeypatch):
    monkeypatch.setenv("MARKETVIEW_DRIVE_ROOT", str(env / "no-drive"))
    r = backup.backup_daily(full, DAY)
    assert r["local"] == "ok" and r["drive"] == "failed" and "Drive" in r["message"]
    assert (env / "backups/daily/marketview-20261010.db.gz").exists() and not (env / "no-drive").exists()
    c = backup.export_csv(full, DAY)
    assert c["local"] == "ok" and c["drive"] == "failed"


def test_run_due_retries_failed_drive_same_day(full, env, monkeypatch):
    monkeypatch.setattr(backup, "today_kst", lambda: DAY)
    monkeypatch.setenv("MARKETVIEW_DRIVE_ROOT", str(env / "later"))
    assert backup.run_due(full)["daily"]["drive"] == "failed"
    (env / "later").mkdir()
    out = backup.run_due(full)
    assert out["daily"]["drive"] == "ok" and (env / "later/backups/daily/marketview-20261010.db.gz").exists()
    assert "daily" not in backup.run_due(full)                                          # 성공하면 더는 하지 않는다


def test_run_due_waits_for_drive_and_does_not_pile_up_csv(full, env, monkeypatch):
    """Drive 가 없는 동안에는 재시도하지 않고(VACUUM·CSV 재생성 없음), CSV 가 날마다 쌓이지 않는다.
    Drive 가 돌아오면 이미 만든 CSV 를 다시 내보내지 않고 복사만 한다."""
    monkeypatch.setenv("MARKETVIEW_DRIVE_ROOT", str(env / "later"))
    days = [DAY, date(2026, 10, 11), date(2026, 10, 12)]
    for i, d in enumerate(days):
        monkeypatch.setattr(backup, "today_kst", lambda d=d: d)
        out = backup.run_due(full)
        assert out["daily"]["drive"] == "failed"                                        # 날마다 새 백업은 만든다
        assert ("csv" in out) == (i == 0)                                               # CSV 는 첫날 한 번뿐
        assert "daily" not in backup.run_due(full)                                      # 같은 날 재시도 없음 (Drive 없음)
    csvs = sorted(p.name for p in (env / "exports").iterdir())
    assert csvs == ["marketview-20261010-news.csv.gz", "marketview-20261010-observation.csv.gz"]
    stamp = (env / "exports/marketview-20261010-observation.csv.gz").stat().st_mtime_ns
    (env / "later").mkdir()
    out = backup.run_due(full)
    assert out["daily"]["drive"] == "ok" and out["csv"]["drive"] == "ok"
    assert sorted(p.name for p in (env / "later/backups/csv").iterdir()) == csvs
    assert (env / "exports/marketview-20261010-observation.csv.gz").stat().st_mtime_ns == stamp   # 다시 내보내지 않았다
    assert backup.last_status(full)["csv"]["drive"] == "ok" and "csv" not in backup.run_due(full)


def test_interrupted_copy_leaves_no_truncated_file(full, env, monkeypatch):
    """복사가 중간에 끊겨도 Drive·월별 자리에 잘린 파일이 생기지 않고, 이미 있던 월별 영구본은 그대로다."""
    backup.backup_daily(full, DAY)
    monthly = env / "backups/monthly/marketview-202610.db.gz"
    good = monthly.read_bytes()
    drive_daily = env / "drive/backups/daily/marketview-20261010.db.gz"
    drive_daily.unlink()

    real, fail_on = backup.shutil.copyfile, ["monthly"]

    def flaky(src, dst):
        if any(s in str(dst) for s in fail_on):
            with open(dst, "wb") as fh:
                fh.write(open(src, "rb").read()[:10])    # 앞부분만 쓰고 끊긴다
            raise OSError("디스크 연결 끊김")
        return real(src, dst)
    monkeypatch.setattr(backup.shutil, "copyfile", flaky)
    with pytest.raises(OSError):
        backup.backup_daily(full, DAY, force=True)       # 월별 복사에서 끊긴다 (로컬 일일 gzip 은 이미 끝남)
    assert monthly.read_bytes() == good and not list(monthly.parent.glob("*.part"))
    fail_on[:] = [str(env / "drive")]
    r = backup.backup_daily(full, DAY, force=True)       # 이번엔 Drive 복사에서 끊긴다 → 실패로 기록, 잘린 파일 없음
    assert r["drive"] == "failed" and not drive_daily.exists() and not list(drive_daily.parent.glob("*.part"))


def test_csv_weekly_due_and_contents(full, env, monkeypatch):
    assert backup.csv_due(DAY)
    r = backup.export_csv(full, DAY)
    assert r["rows"] == {"observation": 1, "news": 1} and r["drive"] == "ok"
    assert not backup.csv_due(date(2026, 10, 16)) and backup.csv_due(date(2026, 10, 17))
    assert backup.export_csv(full, date(2026, 10, 12))["local"] == "not-due"
    text = gzip.open(env / "exports/marketview-20261010-news.csv.gz", "rt", encoding="utf-8-sig").read()
    assert '"제목, 쉼표 포함"' in text and SECRET not in text
    for f in (env / "exports").iterdir():
        assert SECRET.encode() not in gzip.open(f).read()                                # 텔레그램은 CSV 에 없다
    backup.export_csv(full, date(2026, 10, 20))
    assert len(list((env / "drive/backups/csv").iterdir())) == 4                        # 누적, 지우지 않음


def test_restore_refuses_existing_and_preserves_old(full, env):
    backup.backup_daily(full, DAY)
    gz = env / "backups/daily/marketview-20261010.db.gz"
    target = env / "live.db"
    target.write_bytes(b"old-db")
    Path = type(target)
    Path(str(target) + "-wal").write_bytes(b"old-wal")
    with pytest.raises(FileExistsError):
        backup.restore(gz, target)
    assert target.read_bytes() == b"old-db"
    res = backup.restore(gz, target, force=True)
    assert len(res["moved_aside"]) == 2 and res["tables"]["observation"] == 1
    assert any(f.read_bytes() == b"old-db" for f in env.glob("live.db.before-restore-*"))
    assert sqlite3.connect(target).execute("SELECT COUNT(*) FROM tg_message").fetchone()[0] == 1


def test_restore_rejects_corrupt_backup_without_touching_target(env):
    bad = env / "bad.db.gz"
    bad.write_bytes(gzip.compress(b"this is not a sqlite database" * 100))
    target = env / "live.db"
    target.write_bytes(b"precious")
    with pytest.raises(Exception):
        backup.restore(bad, target, force=True)
    assert target.read_bytes() == b"precious" and not list(env.glob("live.db.before-restore-*"))
