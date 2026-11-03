"""cloud-data 브랜치 관리 (GitHub Actions 러너에서 실행). 브랜치가 끝없이 커지지 않게 한다.

러너는 매 회차 새 VM 이라 `git fetch origin cloud-data` 로 브랜치 전체 이력을 받고 전체 트리를 체크아웃한다.
데이터가 쌓일수록 그 비용이 선형으로 늘어난다(2026-10-14 실측: 하루 약 0.5MB 팩 · 2MB 트리). 로컬 앱은 15분마다 병합해서
로컬 DB 에 영구 저장하므로 클라우드 브랜치는 '최근 며칠치 전달용 버퍼' 면 충분하다.

  prune   KEEP_DAYS 보다 오래된 data/cloud/YYYY-MM-DD 폴더를 지운다 (폴더 이름의 날짜 기준 — mtime 은 체크아웃마다 같다).
  squash  커밋이 SQUASH_AFTER_COMMITS 를 넘으면 현재 트리를 커밋 하나로 합쳐 force-push 한다 (이력 초기화).

로컬 병합(cloudsync)은 파일 경로 + blob 해시로 중복을 가리므로, squash 뒤에도 내용이 같은 파일은 다시 합치지 않는다.
로컬 앱을 KEEP_DAYS 넘게 꺼 두면 그 사이 뉴스는 클라우드에서 받을 수 없다(일별 시세는 로컬이 직접 백필한다).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

KEEP_DAYS = 45
SQUASH_AFTER_COMMITS = 250       # 평일 하루 약 7커밋 → 5주쯤마다. 이력은 이 정도(약 20MB 이하)로 묶인다
BRANCH = "cloud-data"
_DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def prune_old_days(cloud_dir: Path, today: date, keep_days: int = KEEP_DAYS) -> list[str]:
    """cloud_dir(=data/cloud) 아래에서 keep_days 보다 오래된 날짜 폴더를 지우고 지운 이름을 돌려준다. 날짜 형식이 아닌 항목은 건드리지 않는다."""
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    gone: list[str] = []
    if not cloud_dir.is_dir():
        return gone
    for child in sorted(cloud_dir.iterdir()):
        if child.is_dir() and _DAY_DIR.match(child.name) and child.name < cutoff:
            shutil.rmtree(child)
            gone.append(child.name)
    return gone


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} 실패: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else proc.returncode}")
    return proc.stdout.strip()


def squash_if_needed(repo: Path, threshold: int = SQUASH_AFTER_COMMITS, remote: str = "origin", branch: str = BRANCH) -> dict | None:
    """repo 의 현재 브랜치(branch)가 threshold 개 넘는 커밋이면 트리를 한 커밋으로 합쳐 force-push 한다. 아니면 None.
    푸시는 --force-with-lease 라서 그 사이 원격이 바뀌었으면 덮어쓰지 않고 실패한다(호출자는 다음 회차에 다시 본다)."""
    count = int(_git(repo, "rev-list", "--count", "HEAD"))
    if count <= threshold:
        return None
    if _git(repo, "status", "--porcelain"):
        raise RuntimeError("커밋하지 않은 변경이 있어 squash 하지 않습니다")
    old = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "--orphan", "squash-tmp")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"squash: history reset ({count} commits)")
    _git(repo, "branch", "-M", branch)
    _git(repo, "push", "-q", f"--force-with-lease={branch}:{old}", remote, branch)
    return {"squashed": count, "head": _git(repo, "rev-parse", "HEAD")}
