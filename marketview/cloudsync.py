"""클라우드 수집분을 로컬 SQLite 에 병합한다 (SPEC §6).

Actions 는 별도 브랜치 `cloud-data` 의 data/cloud/ 에만 커밋한다 (코드 히스토리를 어지럽히지 않는다).
로컬은 `git fetch` 로 그 브랜치만 받아 **읽기만** 한다(`git ls-tree` / `git show`) — 작업 트리·현재 브랜치·
커밋하지 않은 변경을 건드리지 않으므로 충돌이 없다.

병합은 멱등이다: 관측치는 (series_key, obs_date, kind, obs_ts), 뉴스는 link 가 키라 같은 파일을 몇 번
합쳐도 결과가 같다. 이미 합친 파일은 blob 해시(sync_state)로 건너뛴다. 수동 입력은 덮어쓰지 않는다
(db.upsert_observation 규칙). 원격이 없거나 네트워크가 안 되면 상태만 남기고 조용히 끝난다.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from datetime import date
from pathlib import Path

from . import db
from .collect.base import Fetched, scrub
from .config.settings import REPO_ROOT, now_kst

BRANCH = "cloud-data"
REF = f"origin/{BRANCH}"
PREFIX = "data/cloud/"
GIT_TIMEOUT = 60
_FILE = re.compile(r"^data/cloud/(\d{4}-\d{2}-\d{2})/(snapshots|pages|news)\.json$")


def _git(*args: str, repo: Path = REPO_ROOT) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=GIT_TIMEOUT, check=False)
    if proc.returncode != 0:
        lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(scrub(lines[-1] if lines else f"git {args[0]} 실패"))
    return proc.stdout.decode("utf-8")


def _state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO sync_state (key,value,updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, value, now_kst()))


def has_remote(repo: Path = REPO_ROOT) -> bool:
    try:
        return bool(_git("remote", repo=repo).strip())
    except Exception:  # noqa: BLE001
        return False


def _merge_observations(conn: sqlite3.Connection, rows: list[dict]) -> tuple[int, list[str]]:
    """(새로 들어온 행 수, 문제 목록). 행 하나가 틀려도 나머지는 합친다. series_def 에 없는 키(클라우드 코드가 더 새로운 경우)는
    외래키 위반으로 병합 전체를 멈추게 하므로 건너뛰고 알린다."""
    known = {r[0] for r in conn.execute("SELECT series_key FROM series_def")}
    n, unknown, bad = 0, set(), 0
    for r in rows:
        try:
            if r["series_key"] not in known:
                unknown.add(r["series_key"])
                continue
            f = Fetched(r["series_key"], float(r["value"]), date.fromisoformat(r["obs_date"]), r["provider"],
                        obs_ts=r.get("obs_ts") or "", note=r.get("note"))
            if db.upsert_observation(conn, f, r["kind"], "cloud", r.get("captured_at")) in ("insert", "update"):
                n += 1
        except (KeyError, TypeError, ValueError, AttributeError):
            bad += 1
    problems = ([f"로컬에 정의 없는 시리즈 건너뜀: {', '.join(sorted(unknown))}"] if unknown else []) + \
               ([f"형식이 틀린 행 {bad}개 건너뜀"] if bad else [])
    return n, problems


def _merge_news(conn: sqlite3.Connection, rows: list[dict]) -> tuple[int, list[str]]:
    n, bad = 0, 0
    for r in rows:
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO news (link,title,summary,source,feed_id,published_at,tab,title_key,fetched_at,origin) "
                "VALUES (?,?,?,?,?,?,?,?,?,'cloud')",
                (r["link"], r["title"], r.get("summary"), r["source"], r["feed_id"], r["published_at"], r.get("tab"),
                 r["title_key"], r["fetched_at"]))
            n += cur.rowcount
        except (KeyError, TypeError, sqlite3.IntegrityError):
            bad += 1
    return n, ([f"형식이 틀린 기사 {bad}개 건너뜀"] if bad else [])


def merge_ref(conn: sqlite3.Connection, repo: Path = REPO_ROOT, ref: str = REF) -> dict:
    """ref 의 data/cloud/ 를 병합한다. 반환: 새로 들어온 행 수·읽은 파일 수·원격 status.json."""
    listing = _git("ls-tree", "-r", ref, "--", PREFIX, repo=repo)
    files, merged_files = {"snapshots": 0, "pages": 0, "news": 0}, 0
    added = {"observations": 0, "news": 0}
    remote_status = None
    problems: list[str] = []
    for line in listing.splitlines():
        meta, _, path = line.partition("\t")
        blob = meta.split()[2]
        try:
            if path == PREFIX + "status.json":
                remote_status = json.loads(_git("show", f"{ref}:{path}", repo=repo))
                continue
            m = _FILE.match(path)
            if not m or m.group(2) == "snapshots" or _state(conn, f"cloud_blob:{path}") == blob:   # 장중 스냅샷은 더 쓰지 않는다 (2026-10-14)
                continue
            rows = json.loads(_git("show", f"{ref}:{path}", repo=repo))
            kind = m.group(2)
            n, file_problems = _merge_news(conn, rows) if kind == "news" else _merge_observations(conn, rows)
        except Exception as exc:  # noqa: BLE001 - 파일 하나가 깨져도 나머지 파일은 계속 합친다 (다음 회차에 이 파일부터 다시 시도)
            conn.rollback()
            problems.append(f"{path}: {scrub(str(exc))[:120]}")
            continue
        added["news" if kind == "news" else "observations"] += n
        conn.commit()
        if file_problems:                                     # 건너뛴 행이 있으면 해시를 남기지 않아, 정의가 생기면 다음 회차에 다시 합친다(멱등)
            problems += [f"{path}: {p}" for p in file_problems]
            continue
        _set_state(conn, f"cloud_blob:{path}", blob)          # 파일 하나가 끝난 뒤에 해시를 기록한다 (중간 실패 시 다시 합침 → 멱등)
        conn.commit()
        files[kind] += 1
        merged_files += 1
    return {"files": files, "merged_files": merged_files, "added": added, "remote_status": remote_status, "problems": problems}


def sync(conn: sqlite3.Connection, repo: Path = REPO_ROOT) -> dict:
    """fetch → 병합 → 상태 기록. 실패해도 예외를 밖으로 던지지 않는다."""
    result: dict = {"at": now_kst(), "state": "ok", "message": None}
    try:
        if not has_remote(repo):
            result.update(state="no_remote", message="git 원격(origin)이 없습니다 — 클라우드 미연결")
        else:
            try:
                _git("fetch", "--quiet", "origin", BRANCH, repo=repo)
            except RuntimeError as exc:
                msg = str(exc)
                state = "no_branch" if "couldn't find remote ref" in msg else "fetch_failed"
                result.update(state=state, message="아직 클라우드 수집분이 없습니다" if state == "no_branch" else msg)
            else:
                result.update(merge_ref(conn, repo))
    except Exception as exc:  # noqa: BLE001 - 동기화 실패가 수집과 화면을 막지 않는다
        result.update(state="error", message=scrub(str(exc))[:200])
    _set_state(conn, "cloud_sync", json.dumps(result, ensure_ascii=False))
    conn.commit()
    return result


def last_state(conn: sqlite3.Connection) -> dict | None:
    raw = _state(conn, "cloud_sync")
    return json.loads(raw) if raw else None
