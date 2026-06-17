from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from telethon import TelegramClient, events

from config import build_telethon_proxy, settings


NewsHandler = Callable[[dict[str, Any]], Awaitable[None]]


class TelegramListener:
    def __init__(self, on_news: NewsHandler) -> None:
        self._on_news = on_news
        self._client: TelegramClient | None = None
        session_dir = Path(__file__).resolve().parents[1] / "data"
        session_dir.mkdir(parents=True, exist_ok=True)
        self._session_file = session_dir / "news_aggregator_session"
        self._bootstrap_limit = 25

    async def run(self) -> None:
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            print("Telegram listener disabled: TELEGRAM_API_ID or TELEGRAM_API_HASH missing")
            return

        if not settings.telegram_channels:
            print("Telegram listener disabled: TELEGRAM_CHANNELS is empty")
            return

        while True:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                await self.stop()
                raise
            except Exception as exc:
                print(f"Telegram listener error: {exc}")
                await asyncio.sleep(5)

    async def _run_once(self) -> None:
        proxy = build_telethon_proxy()
        client_kwargs = {
            "session": str(self._session_file),
            "api_id": settings.telegram_api_id,
            "api_hash": settings.telegram_api_hash,
            "connection_retries": 5 if proxy else 1,
            "timeout": 10.0,
        }
        if proxy:
            client_kwargs["proxy"] = proxy
            print(f"Telegram listener using proxy: {settings.proxy_host}:{settings.proxy_port}")

        self._client = TelegramClient(**client_kwargs)

        await self._client.start(phone=settings.telegram_phone or None)

        channels = settings.telegram_channels
        resolved_chats = await self._resolve_chats(channels)
        event_chats = resolved_chats or channels

        if resolved_chats:
            await self._bootstrap_recent_messages(resolved_chats)

        @self._client.on(events.NewMessage(chats=event_chats))
        async def handler(event: events.NewMessage.Event) -> None:
            text = (event.raw_text or "").strip()
            if len(text) < 3:
                return

            chat = await event.get_chat()
            source_channel = self._channel_label(chat)

            await self._on_news(
                {
                    "raw_text": text,
                    "source": "telegram",
                    "source_channel": source_channel,
                    "published_at": event.date,
                    "url": None,
                }
            )

        print(f"Telegram listener started for channels: {channels}")
        await self._client.run_until_disconnected()

    async def _resolve_chats(self, channels: list[str]) -> list[Any]:
        if self._client is None:
            return []

        resolved: list[Any] = []
        for channel in channels:
            try:
                entity = await self._client.get_entity(channel)
                resolved.append(entity)
            except Exception as exc:
                print(f"Telegram channel resolve failed for '{channel}': {exc}")
        return resolved

    async def _bootstrap_recent_messages(self, chats: list[Any]) -> None:
        if self._client is None:
            return

        backfilled_count = 0
        for chat in chats:
            try:
                messages = await self._client.get_messages(chat, limit=self._bootstrap_limit)
            except Exception as exc:
                print(f"Telegram bootstrap failed for {self._channel_label(chat)}: {exc}")
                continue

            for message in reversed(messages):
                text = (getattr(message, "raw_text", "") or "").strip()
                if len(text) < 3:
                    continue

                await self._on_news(
                    {
                        "raw_text": text,
                        "source": "telegram",
                        "source_channel": self._channel_label(chat),
                        "published_at": getattr(message, "date", None),
                        "url": None,
                    }
                )
                backfilled_count += 1

        if backfilled_count:
            print(
                "Telegram bootstrap queued "
                f"{backfilled_count} recent messages across {len(chats)} channel(s)"
            )

    @staticmethod
    def _channel_label(chat: Any) -> str:
        return (
            getattr(chat, "username", None)
            or getattr(chat, "title", None)
            or "telegram"
        )

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None
