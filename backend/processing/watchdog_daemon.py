import asyncio
import os
import sys
import socket
import httpx
from datetime import datetime, timezone, timedelta
from collections import deque

# Add parent directory to path so imports work correctly
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from bot.telegram_notifier import send_alert_message
from bot.email_notifier import send_email_alert
from bot.whatsapp_notifier import send_whatsapp_alert


# In-memory rate limiting for restarts and alerts
_restart_timestamps = deque(maxlen=10)
_last_alert_sent_at = None
_MAX_RESTARTS_PER_HOUR = 3
_ALERT_COOLDOWN = timedelta(seconds=settings.watchdog_check_interval_seconds)


def restart_backend_container() -> bool:
    """Restarts the backend container using raw HTTP over the Docker UNIX socket."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=1)

    # Clean old restart timestamps
    while _restart_timestamps and _restart_timestamps[0] < cutoff:
        _restart_timestamps.popleft()

    # Rate limiting check
    if len(_restart_timestamps) >= _MAX_RESTARTS_PER_HOUR:
        print(f"Watchdog Daemon: Max restarts limit ({_MAX_RESTARTS_PER_HOUR}/hour) reached! Skipping auto-restart.")
        return False

    _restart_timestamps.append(now)
    print("Watchdog Daemon: Initiating backend container restart via Docker socket...")

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect("/var/run/docker.sock")
        # Send Docker API POST request to restart container
        request = b"POST /containers/newscodex-backend/restart HTTP/1.1\r\nHost: localhost\r\n\r\n"
        s.sendall(request)
        response = s.recv(1024)
        s.close()
        print(f"Watchdog Daemon: Docker restart response: {response.decode('utf-8', errors='ignore')}")
        return True
    except Exception as exc:
        print(f"Watchdog Daemon: Failed to restart container: {exc}")
        return False


async def send_system_health_alerts(summary: str, details: str, now: datetime) -> None:
    """Sends coordinated alerts over Telegram, Email, and WhatsApp with cooldown protection."""
    global _last_alert_sent_at

    if _last_alert_sent_at is not None and (now - _last_alert_sent_at) < _ALERT_COOLDOWN:
        print("Watchdog Daemon: Alert cooldown active. Skipping notifications.")
        return

    _last_alert_sent_at = now

    # 1. Telegram
    try:
        await send_alert_message(f"⚠️ <b>NEWS CODEX DAEMON ALERT</b>\n\n{summary}\n\n<code>{details}</code>", parse_mode="HTML")
    except Exception as exc:
        print(f"Watchdog Daemon: Telegram alert failed: {exc}")

    # 2. Email
    try:
        email_body = f"""
        <h3>News Codex Watchdog Daemon Alert</h3>
        <p><b>Status:</b> {summary}</p>
        <pre>{details}</pre>
        <p><b>Time:</b> {now.strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
        <hr/>
        <p><i>Sent automatically from decoupled Watchdog Daemon.</i></p>
        """
        await send_email_alert("⚠️ News Codex: System Health Alert", email_body)
    except Exception as exc:
        print(f"Watchdog Daemon: Email alert failed: {exc}")

    # 3. WhatsApp
    try:
        whatsapp_msg = (
            "⚠️ *NEWS CODEX DAEMON ALERT*\n\n"
            f"Status: {summary}\n"
            f"Details: {details}\n"
            f"Time: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        await send_whatsapp_alert(whatsapp_msg)
    except Exception as exc:
        print(f"Watchdog Daemon: WhatsApp alert failed: {exc}")


async def check_health() -> None:
    """Queries the backend /health endpoint and takes recovery action if unhealthy."""
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] Watchdog Daemon: Performing health check...")

    urls = [
        "http://backend:8000/health",
        "http://127.0.0.1:8000/health"
    ]

    health_data = None
    last_error = None

    # Retry health check up to 3 times with a 5-second delay to handle transient boot latency
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(1, 4):
            for url in urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        health_data = resp.json()
                        break
                    else:
                        last_error = f"HTTP {resp.status_code} response from {url}"
                except Exception as e:
                    last_error = f"Connection failed to {url}: {e}"
            
            if health_data:
                break
            
            if attempt < 3:
                print(f"Watchdog Daemon: Health check attempt {attempt} failed ({last_error}). Retrying in 5 seconds...")
                await asyncio.sleep(5)

    # If backend is unresponsive or health data reports error
    if not health_data:
        summary = "Backend Service UNRESPONSIVE"
        details = f"Error details: {last_error or 'Unknown connection error'}"
        print(f"Watchdog Daemon: {summary}. {details}")
        
        await send_system_health_alerts(summary, details, now)
        restart_backend_container()
        return

    # Check is_healthy flag inside health data
    is_healthy = health_data.get("is_healthy", False)
    tasks = health_data.get("tasks", {})
    recent_news_count = health_data.get("recent_news_count", 0)

    dead_tasks = [name for name, t in tasks.items() if not t.get("alive", False)]

    if not is_healthy or dead_tasks:
        summary = "Backend health reporting UNHEALTHY state"
        details = f"Dead tasks: {', '.join(dead_tasks) or 'None'}\nRecent News (1h): {recent_news_count}"
        print(f"Watchdog Daemon: {summary}. {details}")
        
        await send_system_health_alerts(summary, details, now)
        restart_backend_container()
        return

    # Check for zero ingestion activity in last hour (warning only)
    no_news_threshold = settings.watchdog_no_news_threshold_seconds
    if recent_news_count == 0:
        summary = "Ingestion pipeline IDLE (Zero news last 60m)"
        details = f"All background tasks are alive, but 0 news items ingested.\nThreshold: {no_news_threshold}s"
        print(f"Watchdog Daemon: {summary}. {details}")
        await send_system_health_alerts(summary, details, now)
        return

    print(f"Watchdog Daemon: Backend healthy. News in last hour: {recent_news_count}. All background tasks alive.")


async def main() -> None:
    check_interval = settings.watchdog_check_interval_seconds
    print(f"Watchdog Daemon started. Running health check every {check_interval} seconds.")
    
    # Wait 30 seconds on startup to let backend boot
    await asyncio.sleep(30)

    while True:
        try:
            await check_health()
        except Exception as exc:
            print(f"Watchdog Daemon: Error in main loop iteration: {exc}")
        
        await asyncio.sleep(check_interval)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Watchdog Daemon terminated by user.")
