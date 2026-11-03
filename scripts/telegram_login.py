"""텔레그램 최초 로그인 + 구독 채널 목록 출력 (사용자가 터미널에서 직접 실행한다).

  1) my.telegram.org 에서 api_id / api_hash 를 발급해 .env 에 적는다:
         TELEGRAM_API_ID=1234567
         TELEGRAM_API_HASH=abcdef...
  2) 이 스크립트를 실행하고 전화번호(+82...)와 텔레그램으로 온 인증코드를 입력한다:
         .venv\\Scripts\\python scripts\\telegram_login.py
     (이미 로그인한 세션이 있으면 인증 없이 바로 채널 목록만 출력한다)
  3) 출력된 채널 목록에서 읽을 채널의 key 를 marketview/config/telegram.py 의 CHANNELS 에 옮겨 적는다.

읽기 전용이다. 세션(data/telegram.session)과 api_hash 는 .gitignore 이고 출력하지 않는다.
Telethon 은 비동기 라이브러리라 모든 호출에 await 가 필요하다 (동기식 with 로 쓰면 코루틴만 돌려준다).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marketview.collect.telegram import TelegramUnavailable, credentials  # noqa: E402
from marketview.config import telegram as cfg  # noqa: E402


async def run(api_id: int, api_hash: str) -> int:
    from telethon import TelegramClient

    cfg.SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(cfg.SESSION_PATH), api_id, api_hash)
    await client.start()                          # 세션이 없으면 전화번호·인증코드를 묻는다
    try:
        me = await client.get_me()
        print(f"로그인됨: {me.first_name} (세션 저장: {cfg.session_file()})")
        print("\n구독 중인 채널 (key 를 CHANNELS 에 적으세요):")
        n = 0
        async for d in client.iter_dialogs():
            if d.is_channel:
                username = getattr(d.entity, "username", None)
                key = f"@{username}" if username else str(d.id)
                print(f"  key={key:32} 이름={d.name}")
                n += 1
        if not n:
            print("  (구독 중인 채널이 없습니다)")
    finally:
        await client.disconnect()
    print("\n끝. 이제 marketview/config/telegram.py 의 CHANNELS 를 채우면 앱이 읽습니다.")
    return 0


def main() -> int:
    try:
        api_id, api_hash = credentials()
    except TelegramUnavailable as exc:
        print("설정 필요:", exc)
        return 1
    try:
        import telethon  # noqa: F401
    except ImportError:
        print("telethon 이 없습니다:  .venv\\Scripts\\python -m pip install telethon")
        return 1
    return asyncio.run(run(api_id, api_hash))


if __name__ == "__main__":
    sys.exit(main())
