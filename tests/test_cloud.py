"""클라우드 수집기(내보내기)와 로컬 병합(cloudsync): 멱등, 수동 값 보호, 작업 트리 불변, 원격 없음 처리."""

import json
import subprocess
from datetime import date

import pytest

from marketview import cloud, cloudsync, db, ingest, news
from marketview.collect.base import Fetched

DAY = date(2026, 10, 10)


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *args],
                   check=True, capture_output=True)


def _obs(conn, key, d, v, kind="daily", ts="", provider="ecos"):
    y, m, dd = map(int, d.split("-"))
    db.upsert_observation(conn, Fetched(key, v, date(y, m, dd), provider, obs_ts=ts), kind, "cloud")
    conn.commit()


def _fake_run(monkeypatch, fail_all=False):
    def daily(conn, today, origin, log, recent_days):
        assert origin == "cloud" and recent_days == cloud.RECENT_DAYS       # 빈 DB 로 3년 백필하지 않는다
        run = ingest.Run(conn, "daily", origin)
        if fail_all:
            run.record("ecos", "ktb_3y", "error", 0, "HTTP 500 api_key=SECRETKEY")
        else:
            run.record("ecos", "ktb_3y", "ok", 1)
            _obs(conn, "ktb_3y", "2026-10-09", 3.98)
        run.finish()

    def rss(conn, feeds=None, origin="cloud"):
        if fail_all:
            return [{"feed": "f", "status": "error", "new": 0, "message": "HTTP 503"}]
        conn.execute("INSERT INTO news (link,title,source,feed_id,published_at,title_key,fetched_at,origin) "
                     "VALUES ('https://e.com/a','기사','소스','f','2026-10-10T10:00:00+09:00','k','2026-10-10T10:00:00+09:00',?)", (origin,))
        conn.commit()
        return [{"feed": "f", "status": "ok", "new": 1, "message": None}]

    monkeypatch.setattr(ingest, "run_daily", daily)
    monkeypatch.setattr(news, "collect_rss", rss)


def test_collect_writes_files_and_status(tmp_path, monkeypatch):
    _fake_run(monkeypatch)
    st = cloud.collect("daily", tmp_path, DAY, log=lambda m: None)
    assert st["status"] == "ok" and st["exported"] == {"snapshots": 0, "pages": 1, "news": 1}   # 장중 스냅샷은 더 받지 않는다
    d = tmp_path / "2026-10-10"
    assert json.loads((d / "pages.json").read_text(encoding="utf-8"))[0]["value"] == 3.98
    assert json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))["mode"] == "daily"


def test_collect_twice_same_result(tmp_path, monkeypatch):
    _fake_run(monkeypatch)
    cloud.collect("daily", tmp_path, DAY, log=lambda m: None)
    first = {p.name: p.read_bytes() for p in (tmp_path / "2026-10-10").iterdir()}
    cloud.collect("daily", tmp_path, DAY, log=lambda m: None)
    assert {p.name: p.read_bytes() for p in (tmp_path / "2026-10-10").iterdir()} == first


def test_news_mode_skips_daily(tmp_path, monkeypatch):
    _fake_run(monkeypatch)
    st = cloud.collect("news", tmp_path, DAY, log=lambda m: None)
    assert st["exported"]["pages"] == 0 and not (tmp_path / "2026-10-10/pages.json").exists()


def test_failures_are_scrubbed_and_all_failed_exits_nonzero(tmp_path, monkeypatch):
    _fake_run(monkeypatch, fail_all=True)
    st = cloud.collect("daily", tmp_path, DAY, log=lambda m: None)
    assert st["status"] == "failed"
    assert "SECRETKEY" not in (tmp_path / "status.json").read_text(encoding="utf-8")
    assert cloud.main(["collect", "--mode", "news", "--out", str(tmp_path / "o2")]) == 2


def test_merge_rows_newer_wins_for_observations_first_for_news(tmp_path):
    p = tmp_path / "x.json"
    key = ("series_key", "obs_date", "kind", "obs_ts")
    cloud._merge_rows(p, [dict(zip(key, ("a", "d", "daily", "")), value=1)], key, newer_wins=True)
    cloud._merge_rows(p, [dict(zip(key, ("a", "d", "daily", "")), value=2)], key, newer_wins=True)
    assert json.loads(p.read_text(encoding="utf-8"))[0]["value"] == 2
    q = tmp_path / "n.json"
    cloud._merge_rows(q, [{"link": "l", "title": "처음"}], ("link",), newer_wins=False)
    cloud._merge_rows(q, [{"link": "l", "title": "나중"}], ("link",), newer_wins=False)
    assert json.loads(q.read_text(encoding="utf-8")) == [{"link": "l", "title": "처음"}]


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """원격(bare) · 클라우드 러너(clone, cloud-data 브랜치에 커밋) · 로컬(clone, master)."""
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    remote, runner, local = tmp_path / "remote.git", tmp_path / "runner", tmp_path / "local"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(local)], check=True, capture_output=True)
    (local / "code.txt").write_text("code", encoding="utf-8")
    _git(local, "add", "-A"); _git(local, "commit", "-q", "-m", "code"); _git(local, "push", "-q", "origin", "HEAD:main")
    subprocess.run(["git", "clone", "-q", str(remote), str(runner)], check=True, capture_output=True)
    _git(runner, "checkout", "-q", "--orphan", "cloud-data"); _git(runner, "rm", "-rf", "-q", ".")
    return remote, runner, local


def _publish(runner, monkeypatch, tmp_path, value=3.98):
    _fake_run(monkeypatch)
    (runner / "data").mkdir(exist_ok=True)
    cloud.collect("daily", runner / "data" / "cloud", DAY, log=lambda m: None)
    _git(runner, "add", "-A"); _git(runner, "commit", "-q", "-m", "cloud")
    _git(runner, "push", "-q", "origin", "cloud-data")


def test_sync_merges_idempotently_and_leaves_worktree_alone(repos, conn, monkeypatch, tmp_path):
    _, runner, local = repos
    _publish(runner, monkeypatch, tmp_path)
    head = subprocess.run(["git", "-C", str(local), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    (local / "dirty.txt").write_text("uncommitted", encoding="utf-8")
    r = cloudsync.sync(conn, local)
    assert r["state"] == "ok" and r["added"] == {"observations": 1, "news": 1} and r["remote_status"]["status"] == "ok"
    assert conn.execute("SELECT origin FROM observation WHERE series_key='ktb_3y'").fetchone()[0] == "cloud"
    assert conn.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 1
    r2 = cloudsync.sync(conn, local)                                     # 같은 blob 은 건너뛴다
    assert r2["merged_files"] == 0 and r2["added"] == {"observations": 0, "news": 0}
    assert conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == 1
    assert subprocess.run(["git", "-C", str(local), "rev-parse", "HEAD"], capture_output=True, text=True).stdout == head
    assert (local / "dirty.txt").read_text(encoding="utf-8") == "uncommitted" and not (local / "data").exists()


def test_sync_does_not_overwrite_manual_values(repos, conn, monkeypatch, tmp_path):
    _, runner, local = repos
    db.upsert_observation(conn, Fetched("ktb_3y", 9.99, DAY.replace(day=9), "user"), "daily", "manual")
    conn.commit()
    _publish(runner, monkeypatch, tmp_path)
    cloudsync.sync(conn, local)
    assert conn.execute("SELECT value, origin FROM observation WHERE series_key='ktb_3y'").fetchone()[:] == (9.99, "manual")


def test_sync_new_cloud_commit_is_picked_up(repos, conn, monkeypatch, tmp_path):
    _, runner, local = repos
    _publish(runner, monkeypatch, tmp_path)
    cloudsync.sync(conn, local)
    news_file = runner / "data/cloud/2026-10-10/news.json"
    rows = json.loads(news_file.read_text(encoding="utf-8"))
    rows.append({**rows[0], "link": "https://e.com/b", "title": "둘째"})
    news_file.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    _git(runner, "add", "-A"); _git(runner, "commit", "-q", "-m", "more"); _git(runner, "push", "-q", "origin", "cloud-data")
    r = cloudsync.sync(conn, local)
    assert r["added"]["news"] == 1 and conn.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 2


def test_sync_without_remote_or_branch(tmp_path, conn):
    plain = tmp_path / "plain"
    subprocess.run(["git", "init", "-q", str(plain)], check=True)
    assert cloudsync.sync(conn, plain)["state"] == "no_remote"
    remote = tmp_path / "r.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(plain, "remote", "add", "origin", str(remote))
    assert cloudsync.sync(conn, plain)["state"] == "no_branch"
    assert cloudsync.last_state(conn)["state"] == "no_branch"


def _put_cloud_file(runner, rel, rows):
    p = runner / "data/cloud" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(rows if isinstance(rows, str) else json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def _commit_push(runner, msg="more"):
    _git(runner, "add", "-A"); _git(runner, "commit", "-q", "-m", msg); _git(runner, "push", "-q", "origin", "cloud-data")


def _obs_row(key, d="2026-10-09", v=3.98):
    return {"series_key": key, "obs_date": d, "kind": "daily", "obs_ts": "", "value": v, "provider": "ecos",
            "captured_at": "2026-10-10T10:00:00+09:00", "note": None}


def test_unknown_series_key_is_skipped_not_fatal(repos, conn):
    """클라우드 코드가 로컬보다 새로워 series_def 에 없는 키가 오면, 예전엔 외래키 위반으로 병합 전체가 멈췄다."""
    _, runner, local = repos
    _put_cloud_file(runner, "2026-10-10/pages.json", [_obs_row("brand_new_series"), _obs_row("ktb_3y")])
    _put_cloud_file(runner, "2026-10-09/news.json", [{"link": "https://e.com/n", "title": "기사", "source": "s", "feed_id": "f",
                                                      "published_at": "2026-10-09T10:00:00+09:00", "title_key": "k", "fetched_at": "2026-10-09T10:00:00+09:00"}])
    _commit_push(runner)
    r = cloudsync.sync(conn, local)
    assert r["state"] == "ok" and r["added"] == {"observations": 1, "news": 1}             # 정상 행·다른 파일은 합쳐졌다
    assert any("brand_new_series" in p for p in r["problems"])
    assert conn.execute("SELECT COUNT(*) FROM observation WHERE series_key='brand_new_series'").fetchone()[0] == 0
    again = cloudsync.sync(conn, local)                                                    # 건너뛴 행이 있는 파일은 다음 회차에 다시 시도한다
    assert again["problems"] and again["added"] == {"observations": 0, "news": 0}


def test_broken_file_does_not_block_other_files(repos, conn):
    _, runner, local = repos
    _put_cloud_file(runner, "2026-10-08/pages.json", "{ not json")
    _put_cloud_file(runner, "2026-10-10/pages.json", [_obs_row("ktb_3y")])
    _commit_push(runner)
    r = cloudsync.sync(conn, local)
    assert r["state"] == "ok" and r["added"]["observations"] == 1
    assert any("2026-10-08/pages.json" in p for p in r["problems"])
    assert conn.execute("SELECT COUNT(*) FROM sync_state WHERE key LIKE 'cloud_blob:%2026-10-08%'").fetchone()[0] == 0


def test_malformed_rows_are_skipped_individually(repos, conn):
    _, runner, local = repos
    _put_cloud_file(runner, "2026-10-10/pages.json", [{**_obs_row("ktb_3y"), "value": "abc"}, _obs_row("ktb_5y", v=4.1)])
    _commit_push(runner)
    r = cloudsync.sync(conn, local)
    assert r["added"]["observations"] == 1 and any("형식이 틀린 행 1개" in p for p in r["problems"])


def test_clean_merge_has_no_problems(repos, conn, monkeypatch, tmp_path):
    _, runner, local = repos
    _publish(runner, monkeypatch, tmp_path)
    assert cloudsync.sync(conn, local)["problems"] == []


def test_status_lists_cloud_merge_problems(tmp_path, monkeypatch):
    """병합에서 건너뛴 것은 화면 상단의 '수집 경고' 목록에 나온다(조용히 묻히지 않는다)."""
    from fastapi.testclient import TestClient
    path = tmp_path / "api.db"
    monkeypatch.setenv("MARKETVIEW_DB_PATH", str(path))
    c = db.connect(path); db.migrate(c)
    cloudsync._set_state(c, "cloud_sync", json.dumps({"at": "t", "state": "ok", "message": None,
                                                     "problems": ["data/cloud/2026-10-10/pages.json: 로컬에 정의 없는 시리즈 건너뜀: x"]}))
    c.commit(); c.close()
    from marketview.app import app
    fails = TestClient(app).get("/api/status").json()["failures"]
    assert fails == [{"source": "cloud-merge", "series_key": "-", "message": "data/cloud/2026-10-10/pages.json: 로컬에 정의 없는 시리즈 건너뜀: x"}]


# --- cloud-data 브랜치 관리 (prune · squash) --------------------------------------------
def test_prune_removes_only_old_date_folders(tmp_path):
    from marketview import cloud_housekeeping as hk
    root = tmp_path / "cloud"
    for name in ("2026-08-01", "2026-08-30", "2026-08-31", "2026-10-14", "junk", "2026-8-1"):
        (root / name).mkdir(parents=True)
        (root / name / "news.json").write_text("[]", encoding="utf-8")
    (root / "status.json").write_text("{}", encoding="utf-8")
    assert hk.prune_old_days(root, date(2026, 10, 14), 45) == ["2026-08-01"]                  # 기준일(08-30)보다 오래된 것만
    assert sorted(p.name for p in root.iterdir()) == ["2026-08-30", "2026-08-31", "2026-10-14", "2026-8-1", "junk", "status.json"]
    assert hk.prune_old_days(tmp_path / "missing", date(2026, 10, 14)) == []


def _seed_history(runner, n):
    for i in range(n):
        _put_cloud_file(runner, f"2026-10-{10 + i}/pages.json", [_obs_row("ktb_3y", f"2026-10-{9 + i:02d}", 3.9 + i / 100)])
        _commit_push(runner, f"c{i}")


def _count(repo, ref):
    return int(subprocess.run(["git", "-C", str(repo), "rev-list", "--count", ref], capture_output=True, text=True, check=True).stdout)


def test_squash_resets_history_and_local_sync_survives(repos, conn):
    from marketview import cloud_housekeeping as hk
    _, runner, local = repos
    _seed_history(runner, 5)
    assert cloudsync.sync(conn, local)["merged_files"] == 5
    tree = lambda repo, ref: subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", ref], capture_output=True, text=True, check=True).stdout
    before = tree(runner, "HEAD")
    assert hk.squash_if_needed(runner, threshold=10) is None                                  # 아직 많지 않다
    res = hk.squash_if_needed(runner, threshold=3)
    assert res["squashed"] == 5 and _count(runner, "HEAD") == 1 and tree(runner, "HEAD") == before   # 내용은 그대로, 이력만 1개
    r = cloudsync.sync(conn, local)                                                           # force-push 뒤에도 로컬 병합이 이어진다
    assert r["state"] == "ok" and r["merged_files"] == 0 and r["problems"] == []              # 같은 blob 은 다시 합치지 않는다
    assert _count(local, "origin/cloud-data") == 1
    assert conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == 5
    assert hk.squash_if_needed(runner, threshold=3) is None                                   # 커밋이 1개가 됐으니 다시 하지 않는다


def test_squash_refuses_dirty_tree_and_respects_lease(repos, tmp_path):
    from marketview import cloud, cloud_housekeeping as hk
    remote, runner, _ = repos
    _seed_history(runner, 4)
    (runner / "stray.txt").write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="커밋하지 않은 변경"):
        hk.squash_if_needed(runner, threshold=2)
    assert cloud.main(["squash", "--repo", str(runner)]) == 0                                 # 커밋 4개 ≤ 250 이면 지저분해도 할 일이 없다
    (runner / "stray.txt").unlink()
    other = tmp_path / "other"                                                                # 그 사이 다른 곳에서 원격이 바뀐 경우
    subprocess.run(["git", "clone", "-q", "-b", "cloud-data", str(remote), str(other)], check=True, capture_output=True)
    (other / "x.txt").write_text("y", encoding="utf-8")
    _git(other, "add", "-A"); _git(other, "commit", "-q", "-m", "other"); _git(other, "push", "-q", "origin", "cloud-data")
    with pytest.raises(RuntimeError):                                                         # --force-with-lease 가 남의 커밋을 덮어쓰지 않는다
        hk.squash_if_needed(runner, threshold=2)
    assert _count(other, "origin/cloud-data") == 5


def test_cli_prune_and_squash_report(tmp_path, capsys):
    from marketview import cloud
    d = tmp_path / "cloud"
    (d / "2020-01-01").mkdir(parents=True)
    assert cloud.main(["prune", "--dir", str(d)]) == 0
    assert "1개 폴더 삭제" in capsys.readouterr().out and not (d / "2020-01-01").exists()


def test_cloud_collects_only_cloud_feeds_and_local_collects_all(tmp_path, monkeypatch):
    """mt 처럼 러너에서 연결이 막히는 피드(cloud=False)는 클라우드 수집에서 빠지고, 로컬 수집(기본 FEEDS)에는 남는다."""
    from marketview.config import news as ncfg
    seen = {}
    def rss(conn, feeds=None, origin="cloud"):
        seen["feeds"], seen["origin"] = feeds, origin
        return []
    monkeypatch.setattr(ingest, "run_daily", lambda *a, **k: "ok")
    monkeypatch.setattr(news, "collect_rss", rss)
    cloud.collect("news", tmp_path, DAY, log=lambda m: None)
    ids = {f.id for f in seen["feeds"]}
    assert seen["origin"] == "cloud" and "mt" not in ids and "mk_economy" in ids
    assert ids == {f.id for f in ncfg.FEEDS if f.cloud} and len(ids) == len(ncfg.FEEDS) - 1
    assert "mt" in {f.id for f in ncfg.FEEDS}                                                  # 로컬 앱은 계속 받는다


def test_workflow_gives_api_keys_only_to_collect_step():
    """API 키(Secrets)는 수집 단계에만 있다 — 잡 전체 env 에 두면 pip install 등 다른 단계의 코드도 키를 본다."""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/collect.yml").read_text(encoding="utf-8")
    steps = text.split("\n      - ")
    with_secrets = [s.splitlines()[0] for s in steps if "secrets." in s]
    assert with_secrets == ["name: Collect (${{ env.MODE }})"]
    assert all(k in steps[[s.splitlines()[0] for s in steps].index(with_secrets[0])]
               for k in ("ECOS_API_KEY", "FRED_API_KEY", "KRX_AUTH_KEY"))
