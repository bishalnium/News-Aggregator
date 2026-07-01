from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from telethon import TelegramClient

# Add parent directory to path so imports work correctly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings, build_telethon_proxy
from database import init_pool, close_pool, get_pool
from processing.llm_classifier import classify_news_heuristic

async def check_hash_exists(content_hash: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM news WHERE content_hash = $1",
            content_hash
        )
        return row is not None

async def main() -> None:
    print("--- Telegram Historical Backfill Script ---")
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print("Telegram API ID or API Hash is missing from config!")
        return

    days = 15 # Go back 15 days to cover full 2 weeks safely
    print(f"Targeting last {days} days of messages...")
    limit_date = datetime.now(timezone.utc) - timedelta(days=days)

    # Step 1: Connect to Telegram and fetch message payloads
    session_dir = Path(__file__).resolve().parents[1] / "data"
    session_dir.mkdir(parents=True, exist_ok=True)
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
        print(f"Using proxy: {settings.proxy_host}:{settings.proxy_port}")

    client = TelegramClient(**client_kwargs)
    await client.start(phone=settings.telegram_phone or None)
    print("Connected to Telegram successfully.")

    channels = settings.telegram_channels
    print(f"Configured Telegram channels: {channels}")

    all_channel_messages = []

    for channel_name in channels:
        print(f"\nFetching messages from channel: {channel_name}...")
        try:
            chat = await client.get_input_entity(channel_name)
            full_chat = await client.get_entity(chat)
            username = getattr(full_chat, "username", None)
            channel_label = getattr(full_chat, "title", channel_name)
        except Exception as exc:
            print(f"Failed to resolve channel {channel_name}: {exc}")
            continue

        print(f"Resolved channel: {channel_label} (username: @{username or 'None'})")

        offset_id = 0
        limit = 100
        count_fetched = 0
        reached_limit = False

        while not reached_limit:
            try:
                # Fetch explicit batches of messages to avoid paging/reconnect bugs in Telethon generator
                messages = await client.get_messages(chat, limit=limit, offset_id=offset_id)
            except Exception as exc:
                print(f"Error fetching batch: {exc}. Retrying in 2s...")
                await asyncio.sleep(2.0)
                continue

            if not messages:
                print("No more messages returned by Telegram API.")
                break

            print(f"  Fetched batch of {len(messages)} messages. Oldest in batch: {messages[-1].date}")

            for message in messages:
                if message.date < limit_date:
                    print(f"  Reached limit date: {message.date}. Stopping fetch.")
                    reached_limit = True
                    break

                count_fetched += 1
                raw_text = (message.raw_text or "").strip()
                if len(raw_text) < 3:
                    continue

                all_channel_messages.append({
                    "source": "telegram",
                    "source_channel": channel_label,
                    "raw_text": raw_text,
                    "url": f"https://t.me/{username}/{message.id}" if username else None,
                    "published_at": message.date,
                })

            offset_id = messages[-1].id

        print(f"Fetched {count_fetched} messages from channel {channel_name}.")

    # Disconnect from Telegram immediately to release session file and free resources
    await client.disconnect()
    print("Disconnected from Telegram. SQLite session file unlocked.")

    if not all_channel_messages:
        print("No historical messages found to process.")
        return

    # Process messages from oldest to newest
    all_channel_messages.reverse()
    print(f"\nProcessing {len(all_channel_messages)} messages chronologically...")

    # Step 2: Initialize DB Pool
    await init_pool()
    pool = get_pool()

    count_ingested = 0
    count_skipped = 0

    async with pool.acquire() as conn:
        for idx, payload in enumerate(all_channel_messages, 1):
            raw_text = payload["raw_text"]
            content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

            exists = await check_hash_exists(content_hash)
            if exists:
                count_skipped += 1
                continue

            # Run the heuristic classifier (extremely fast, no LLM calls)
            llm = classify_news_heuristic(raw_text)

            try:
                await conn.execute(
                    """
                    INSERT INTO news (
                        source,
                        source_channel,
                        raw_text,
                        url,
                        content_hash,
                        summary,
                        category,
                        urgency,
                        sentiment,
                        instruments_affected,
                        matched_topics,
                        llm_processed,
                        published_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
                    )
                    """,
                    payload["source"],
                    payload["source_channel"],
                    raw_text,
                    payload["url"],
                    content_hash,
                    llm.get("summary"),
                    llm.get("category", "other"),
                    (llm.get("urgency") or "LOW").upper(),
                    (llm.get("sentiment") or "neutral").lower(),
                    llm.get("instruments_affected") or [],
                    [],
                    False, # llm_processed is False (ingested via fast path)
                    payload["published_at"]
                )
                count_ingested += 1
                if count_ingested % 100 == 0 or idx == len(all_channel_messages):
                    print(f"[{idx}/{len(all_channel_messages)}] Ingesting missing news: {raw_text[:100]}...")
            except Exception as e:
                # Handle duplicate keys gracefully in case of race conditions
                if "Duplicate entry" not in str(e):
                    print(f"  Error ingesting message: {e}")

    # Cleanup DB
    await close_pool()
    print(f"\nTelegram backfill completed successfully! Ingested {count_ingested} new items, skipped {count_skipped} existing items.")

if __name__ == "__main__":
    asyncio.run(main())
