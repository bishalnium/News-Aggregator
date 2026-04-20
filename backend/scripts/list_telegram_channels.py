from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from telethon import TelegramClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings


async def main() -> None:
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print("missing_api_credentials")
        return

    session_dir = Path(__file__).resolve().parents[1] / "data"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / "news_aggregator_session"

    client = TelegramClient(
        str(session_file),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    await client.start(phone=settings.telegram_phone or None)

    dialogs = await client.get_dialogs(limit=None)
    channels: list[tuple[str, str]] = []

    for dialog in dialogs:
        entity = dialog.entity
        is_channel = bool(
            getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False)
        )
        if not is_channel:
            continue

        title = getattr(entity, "title", "") or ""
        username = getattr(entity, "username", "") or ""
        channels.append((title, username))

    print(f"TOTAL_CHANNELS={len(channels)}")
    for idx, (title, username) in enumerate(channels, 1):
        handle = f"@{username}" if username else "(no-public-username)"
        print(f"{idx}. {title} | {handle}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
