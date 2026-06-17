from __future__ import annotations

import httpx

from config import build_httpx_proxy_url, settings


TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_MAX_TEXT = 3900


def _split_telegram_chunks(text: str, max_length: int = TELEGRAM_MAX_TEXT) -> list[str]:
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_length:
        split_at = remaining.rfind("\n", 0, max_length)
        if split_at < max_length // 2:
            split_at = max_length
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")

    if remaining:
        chunks.append(remaining)

    return chunks


async def _send_message(
    token: str,
    chat_id: str,
    text: str,
    parse_mode: str | None = None,
) -> bool:
    if not token or not chat_id:
        return False

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    chunks = _split_telegram_chunks(text or "")

    try:
        client_kwargs: dict = {"timeout": 10.0}

        proxy_url = build_httpx_proxy_url()
        if proxy_url:
            try:
                from httpx_socks import AsyncProxyTransport
                transport = AsyncProxyTransport.from_url(proxy_url)
                client_kwargs["transport"] = transport
            except ImportError:
                print("WARNING: httpx-socks not installed. Sending without proxy.")

        async with httpx.AsyncClient(**client_kwargs) as client:
            for chunk in chunks:
                payload = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                }
                if parse_mode:
                    payload["parse_mode"] = parse_mode
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                if not bool(data.get("ok")):
                    return False
            return True
    except Exception as exc:
        print(f"Telegram send error: {exc}")
        return False


async def send_alert_message(text: str, parse_mode: str | None = None) -> bool:
    return await _send_message(
        settings.alert_bot_token,
        settings.alert_chat_id,
        text,
        parse_mode=parse_mode,
    )


async def send_summary_message(text: str, parse_mode: str | None = None) -> bool:
    token = settings.summary_bot_token or settings.alert_bot_token
    chat_id = settings.summary_chat_id or settings.alert_chat_id
    return await _send_message(token, chat_id, text, parse_mode=parse_mode)


async def send_context_alert_message(text: str, parse_mode: str | None = None) -> bool:
    token = settings.context_bot_token or settings.alert_bot_token
    chat_id = settings.context_chat_id or settings.alert_chat_id
    return await _send_message(token, chat_id, text, parse_mode=parse_mode)
