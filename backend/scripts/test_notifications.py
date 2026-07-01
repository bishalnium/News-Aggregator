from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import init_pool, close_pool
from bot.email_notifier import send_email_alert
from bot.whatsapp_notifier import send_whatsapp_alert


async def run_test():
    print("--- Notification Test Tool ---")
    
    # 1. Test Email
    print("\n[1/2] Testing Email Alert...")
    email_subject = "🧪 News Codex: Notification Channel Test"
    email_body = """
    <h3>News Codex Test Message</h3>
    <p>If you are reading this email, your SMTP configuration is <b>working perfectly</b>!</p>
    <p>Time of check: Test Execution</p>
    """
    
    # Try sending email
    email_sent = await send_email_alert(email_subject, email_body)
    if email_sent:
        print("✅ Email sent successfully! Please check your inbox/spam folder.")
    else:
        print("❌ Email failed to send. Check if SMTP_HOST, SMTP_USER, and SMTP_PASSWORD are set correctly.")

    # 2. Test WhatsApp
    print("\n[2/2] Testing WhatsApp Alert...")
    whatsapp_msg = "🧪 *News Codex Alert Test*\n\nIf you receive this message, CallMeBot WhatsApp notifications are working!"
    
    whatsapp_sent = await send_whatsapp_alert(whatsapp_msg)
    if whatsapp_sent:
        print("✅ WhatsApp message sent successfully!")
    else:
        print("❌ WhatsApp failed to send. Check if WHATSAPP_PHONE and WHATSAPP_APIKEY are configured.")

    # 3. Test ntfy.sh
    print("\n[3/3] Testing ntfy.sh Push Notification...")
    from bot.ntfy_notifier import send_ntfy_alert
    ntfy_msg = "🧪 News Codex Alert Test: If you receive this notification, ntfy.sh push alerts are working!"
    ntfy_sent = await send_ntfy_alert(
        ntfy_msg,
        title="🧪 Test Alert",
        priority=3,
        tags="test,test_tube",
    )
    if ntfy_sent:
        print("✅ ntfy.sh push notification sent successfully!")
    else:
        print("❌ ntfy.sh failed to send. Check if NTFY_TOPIC is configured in .env.")


if __name__ == "__main__":
    asyncio.run(run_test())
