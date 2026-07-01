from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from telethon import TelegramClient
from collections import Counter
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings, build_telethon_proxy

async def main() -> None:
    session_dir = Path(__file__).resolve().parents[1] / "data"
    session_file = session_dir / "news_aggregator_session"

    proxy = build_telethon_proxy()
    client_kwargs = {
        "session": str(session_file),
        "api_id": settings.telegram_api_id,
        "api_hash": settings.telegram_api_hash,
        "connection_retries": 5 if proxy else 1,
        "timeout": 10.0,
    }
    if proxy:
        client_kwargs["proxy"] = proxy

    client = TelegramClient(**client_kwargs)
    await client.start(phone=settings.telegram_phone or None)

    chat = await client.get_input_entity("marketfeed")
    
    limit_date = datetime.now(timezone.utc) - timedelta(days=14)
    print(f"Target limit date: {limit_date}")

    date_counter = Counter()
    total = 0
    
    async for message in client.iter_messages(chat, limit=2000):
        if message.date < limit_date:
            break
        total += 1
        date_counter[message.date.date()] += 1

    print(f"Total messages fetched in last 14 days: {total}")
    print("Messages per date in Telegram channel:")
    for date, count in sorted(date_counter.items(), reverse=True):
        print(f"  {date}: {count} messages")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
