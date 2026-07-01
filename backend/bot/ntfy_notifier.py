from __future__ import annotations

import httpx
from config import settings


async def send_ntfy_alert(
    message: str,
    title: str | None = None,
    priority: int = 3,
    tags: str | None = None,
) -> bool:
    """Send a push notification to the user via ntfy.sh public topic."""
    topic = settings.ntfy_topic

    if not topic:
        # ntfy topic not configured
        return False

    url = "https://ntfy.sh/"

    payload = {
        "topic": topic,
        "message": message,
        "priority": priority,
    }
    if title:
        payload["title"] = title
    if tags:
        payload["tags"] = [tag.strip() for tag in tags.split(",") if tag.strip()]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                json=payload,
            )
            response.raise_for_status()
            print(f"ntfy alert sent successfully to topic: {topic}")
            return True
    except Exception as exc:
        print(f"ntfy notifier network/delivery error: {exc}")
        return False
