"""경로·API 키·시각 헬퍼. 로컬 앱과 GitHub Actions 수집기가 함께 쓴다.

.env 파싱에 python-dotenv 를 쓰지 않는다 (필요한 문법이 KEY=VALUE 뿐).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "migrations"

# KST 는 서머타임이 없어 고정 오프셋이 정확하다. 뉴욕은 서머타임 때문에 tzdata 가 필요하다.
KST = timezone(timedelta(hours=9), "KST")
NY = ZoneInfo("America/New_York")


def load_env(path: Path | None = None) -> dict[str, str]:
    """.env 를 읽어 os.environ 에 넣는다. 이미 설정된 환경변수(Actions Secrets)는 덮어쓰지 않는다."""
    path = path or (REPO_ROOT / ".env")
    parsed: dict[str, str] = {}
    if not path.exists():
        return parsed
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            parsed[key] = value
            if value:
                os.environ.setdefault(key, value)
    return parsed


def env(key: str, default: str = "") -> str:
    load_env()
    return os.environ.get(key, default) or default


def drive_root() -> Path:
    """Google Drive 동기화 폴더 안의 MarketView 폴더 (확정 2026-10-09). 없으면 만들지 않고 복사를 실패로 기록한다."""
    return Path(env("MARKETVIEW_DRIVE_ROOT", r"G:\내 드라이브\MarketView"))


def backups_dir() -> Path:
    configured = env("MARKETVIEW_BACKUPS_DIR")
    return Path(configured) if configured else REPO_ROOT / "backups"


def exports_dir() -> Path:
    configured = env("MARKETVIEW_EXPORTS_DIR")
    return Path(configured) if configured else REPO_ROOT / "exports"


def reports_dir() -> Path:
    configured = env("MARKETVIEW_REPORTS_DIR")
    return Path(configured) if configured else REPO_ROOT / "reports"


def db_path() -> Path:
    configured = env("MARKETVIEW_DB_PATH")
    path = Path(configured) if configured else REPO_ROOT / "data" / "marketview.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def today_kst() -> date:
    return datetime.now(KST).date()


def as_ymd(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def as_compact(value: date) -> str:
    return value.strftime("%Y%m%d")


def canonical_date(raw: str) -> str:
    """'YYYY-MM-DD' 인지 엄격히 검사한다. 문자열 정렬이 어긋나는 것을 막는다."""
    text = raw.strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"날짜는 YYYY-MM-DD 여야 한다: {raw!r}") from exc
    if as_ymd(parsed) != text:
        raise ValueError(f"날짜는 YYYY-MM-DD 여야 한다: {raw!r}")
    return text
