"""텔레그램 수집 설정. 읽기 전용이다 (메시지 전송·삭제·읽음 처리 코드를 두지 않는다).

api_id / api_hash 는 https://my.telegram.org 에서 사용자가 직접 발급해 .env 에 넣는다:
    TELEGRAM_API_ID=...
    TELEGRAM_API_HASH=...
최초 로그인(전화번호 + 인증코드)도 사용자가 터미널에서 직접 한다:
    .venv\\Scripts\\python scripts\\telegram_login.py
세션 파일(data/telegram.session)과 api_hash 는 .gitignore 이고 출력하지 않는다. 텔레그램은 클라우드에 올리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .settings import REPO_ROOT

SESSION_PATH = REPO_ROOT / "data" / "telegram"          # telethon 이 .session 을 붙인다 → data/telegram.session
FIRST_FETCH_LIMIT = 200          # 채널마다 처음 가져오는 메시지 수 (이후엔 마지막 msg_id 이후만)
EDIT_RECHECK_HOURS = 72          # 최근 이 시간 안의 저장 메시지는 다시 읽어 수정 여부를 확인한다


@dataclass(frozen=True)
class Channel:
    key: str                  # '@username' 또는 숫자 id 문자열 — DB 의 channel 값이 된다
    name: str                 # 화면에 보일 이름
    tab: str | None = None    # 기본 탭 (키워드가 더 강하게 가리키면 키워드를 따른다)


# 내가 구독 중인 증권사 리서치 채널을 여기에 적는다. 예:
#   Channel("@some_research_channel", "A증권 리서치", "bond"),
#   Channel("-1001234567890", "B증권 크레딧", "credit"),
CHANNELS: tuple[Channel, ...] = ()          # 공개본은 비워 둔다. 구독 중인 채널을 위 예시처럼 적는다.


def session_file() -> Path:
    return SESSION_PATH.with_suffix(".session")
