from __future__ import annotations

import httpx
from urllib.parse import quote

from config import settings


async def send_whatsapp_alert(message: str) -> bool:
    """Send a WhatsApp notification to the user via CallMeBot API."""
    phone = settings.whatsapp_phone
    apikey = settings.whatsapp_apikey

    if not phone or not apikey:
        # WhatsApp CallMeBot not configured
        return False

    # URL encode the message
    encoded_message = quote(message)
    url = f"https://api.callmebot.com/whatsapp.php?phone={phone}&text={encoded_message}&apikey={apikey}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            # CallMeBot returns 200 OK even if parameters are wrong in some cases,
            # but we check for status code first.
            response.raise_for_status()
            content = response.text
            if "error" in content.lower():
                print(f"WhatsApp notifier API error response: {content}")
                return False
            print(f"WhatsApp alert sent successfully to {phone}")
            return True
    except Exception as exc:
        print(f"WhatsApp notifier network/delivery error: {exc}")
        return False
