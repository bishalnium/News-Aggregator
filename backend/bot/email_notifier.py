from __future__ import annotations

import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import settings


def _send_email_sync(subject: str, html_body: str) -> bool:
    """Synchronous implementation of sending SMTP email."""
    host = settings.smtp_host
    port = settings.smtp_port
    user = settings.smtp_user
    password = settings.smtp_password
    to_email = settings.smtp_to

    if not host or not to_email:
        # SMTP not configured, return False silently
        return False

    try:
        # Create message container
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = user or "News Codex Watchdog <watchdog@newscodex.local>"
        msg["To"] = to_email

        # Attach html body
        html_part = MIMEText(html_body, "html")
        msg.attach(html_part)

        # Connect to SMTP server
        # Support SSL or STARTTLS depending on port
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=10.0)
        else:
            server = smtplib.SMTP(host, port, timeout=10.0)
            server.ehlo()
            if server.has_extn("STARTTLS"):
                server.starttls()
                server.ehlo()

        if user and password:
            server.login(user, password)

        server.sendmail(msg["From"], [to_email], msg.as_string())
        server.quit()
        print(f"Email alert sent successfully to {to_email}")
        return True
    except Exception as exc:
        print(f"Email notifier error: {exc}")
        return False


async def send_email_alert(subject: str, html_body: str) -> bool:
    """Asynchronous wrapper to send SMTP email in a thread pool."""
    if not settings.smtp_host or not settings.smtp_to:
        return False
    return await asyncio.to_thread(_send_email_sync, subject, html_body)
