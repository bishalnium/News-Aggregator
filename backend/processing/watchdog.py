from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI

from config import settings
from database import get_pool
from bot.telegram_notifier import send_alert_message
from bot.email_notifier import send_email_alert


class SystemWatchdog:
    """Monitors background task health, database ingestion, and triggers alerts/recovery."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._task: asyncio.Task | None = None
        self._running = False
        self._check_interval_seconds = settings.watchdog_check_interval_seconds
        self._no_news_threshold_seconds = settings.watchdog_no_news_threshold_seconds
        self._last_alert_sent_at: datetime | None = None
        self._alert_cooldown_seconds = settings.watchdog_check_interval_seconds
        self._health_status: dict[str, Any] = {}
        self._restart_count: dict[str, int] = defaultdict(int)
        self._max_restarts_per_hour = 3
        self._restart_timestamps: dict[str, deque[datetime]] = defaultdict(lambda: deque(maxlen=10))

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="system-watchdog")
        print(f"System watchdog started (check interval: {self._check_interval_seconds}s)")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        print("System watchdog stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                # Perform check
                await self._perform_health_check()
            except Exception as exc:
                print(f"Watchdog error in check iteration: {exc}")

            # Sleep until next check
            await asyncio.sleep(self._check_interval_seconds)

    async def _perform_health_check(self) -> None:
        now = datetime.now(timezone.utc)
        task_statuses: dict[str, Any] = {}

        # 1. Check direct background tasks (telegram-listener, twitter-poller)
        background_tasks = getattr(self._app.state, "background_tasks", [])
        for task in background_tasks:
            name = task.get_name()
            if task.done():
                exc = task.exception() if not task.cancelled() else None
                task_statuses[name] = {
                    "alive": False,
                    "cancelled": task.cancelled(),
                    "exception": str(exc) if exc else None,
                }
            else:
                task_statuses[name] = {"alive": True}

        # 2. Check NewsPipeline workers
        pipeline = getattr(self._app.state, "pipeline", None)
        if pipeline and hasattr(pipeline, "_workers"):
            for task in pipeline._workers:
                name = task.get_name()
                if task.done():
                    exc = task.exception() if not task.cancelled() else None
                    task_statuses[name] = {
                        "alive": False,
                        "cancelled": task.cancelled(),
                        "exception": str(exc) if exc else None,
                    }
                else:
                    task_statuses[name] = {"alive": True}

        # 3. Check RollingSummarizer task
        summarizer = getattr(self._app.state, "summarizer", None)
        if summarizer and hasattr(summarizer, "_task") and summarizer._task:
            task = summarizer._task
            name = task.get_name()
            if task.done():
                exc = task.exception() if not task.cancelled() else None
                task_statuses[name] = {
                    "alive": False,
                    "cancelled": task.cancelled(),
                    "exception": str(exc) if exc else None,
                }
            else:
                task_statuses[name] = {"alive": True}

        # 4. Check Database ingestion in the last 60 minutes
        recent_news_count = 0
        try:
            pool = get_pool()
            cutoff = now - timedelta(seconds=self._no_news_threshold_seconds)
            async with pool.acquire() as conn:
                count_row = await conn.fetchval(
                    "SELECT COUNT(*) FROM news WHERE fetched_at >= $1",
                    cutoff,
                )
            recent_news_count = int(count_row or 0)
        except Exception as exc:
            print(f"Watchdog failed to query news count: {exc}")

        # 5. Check queue depth
        queue_size = -1
        if pipeline and hasattr(pipeline, "_queue"):
            queue_size = pipeline._queue.qsize()

        # Build initial status snapshot
        self._health_status = {
            "checked_at": now.isoformat(),
            "tasks": task_statuses,
            "recent_news_count": recent_news_count,
            "no_news_threshold_seconds": self._no_news_threshold_seconds,
            "pipeline_queue_size": queue_size,
            "restart_counts": dict(self._restart_count),
            "is_healthy": True,
        }

        # Find dead tasks
        dead_tasks = [name for name, status in task_statuses.items() if not status["alive"]]
        restarted = []

        # 6. Auto-restart dead tasks
        for task_name in dead_tasks:
            if self._can_restart(task_name, now):
                success = await self._restart_task(task_name)
                if success:
                    restarted.append(task_name)

        # Update final health status
        is_healthy = len(dead_tasks) == 0 and recent_news_count > 0
        self._health_status["is_healthy"] = is_healthy
        self._health_status["dead_tasks"] = dead_tasks
        self._health_status["restarted_tasks"] = restarted

        if not is_healthy:
            await self._send_health_alert(dead_tasks, restarted, recent_news_count, now)
        else:
            print(f"Watchdog check: System healthy. {recent_news_count} news items in last hour. All tasks alive.")

    def _can_restart(self, task_name: str, now: datetime) -> bool:
        # Check safety restart limit (max 3 per hour)
        timestamps = self._restart_timestamps[task_name]
        cutoff = now - timedelta(hours=1)

        # Clean old timestamps
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        if len(timestamps) >= self._max_restarts_per_hour:
            print(f"Watchdog: Rate limit reached for task '{task_name}' auto-restart! ({len(timestamps)} in last hour)")
            return False

        return True

    async def _restart_task(self, task_name: str) -> bool:
        now = datetime.now(timezone.utc)
        self._restart_timestamps[task_name].append(now)
        self._restart_count[task_name] += 1

        try:
            if task_name == "telegram-listener":
                listener = getattr(self._app.state, "telegram_listener", None)
                if listener:
                    await listener.stop()
                    new_task = asyncio.create_task(listener.run(), name="telegram-listener")
                    
                    # Update background_tasks list
                    bg_tasks = getattr(self._app.state, "background_tasks", [])
                    self._app.state.background_tasks = [
                        new_task if t.get_name() == "telegram-listener" else t
                        for t in bg_tasks
                    ]
                    print(f"Watchdog: Auto-restarted telegram-listener (restart count: {self._restart_count[task_name]})")
                    return True

            elif task_name == "twitter-poller":
                poller = getattr(self._app.state, "twitter_poller", None)
                if poller:
                    # twitter poller doesn't hold open long socket client like telethon, just create new task
                    new_task = asyncio.create_task(poller.run(), name="twitter-poller")
                    
                    # Update background_tasks list
                    bg_tasks = getattr(self._app.state, "background_tasks", [])
                    self._app.state.background_tasks = [
                        new_task if t.get_name() == "twitter-poller" else t
                        for t in bg_tasks
                    ]
                    print(f"Watchdog: Auto-restarted twitter-poller (restart count: {self._restart_count[task_name]})")
                    return True

        except Exception as exc:
            print(f"Watchdog failed to restart task '{task_name}': {exc}")

        return False

    async def _send_health_alert(
        self,
        dead_tasks: list[str],
        restarted: list[str],
        recent_news_count: int,
        now: datetime,
    ) -> None:
        # Check alert cooldown
        if (
            self._last_alert_sent_at is not None
            and (now - self._last_alert_sent_at).total_seconds() < self._alert_cooldown_seconds
        ):
            return

        parts = ["⚠️ <b>NEWS CODEX WATCHDOG ALERT</b>\n"]

        if dead_tasks:
            restarted_set = set(restarted)
            parts.append("<b>Task Statuses:</b>")
            for task_name in dead_tasks:
                if task_name in restarted_set:
                    parts.append(f"🔄 <code>{task_name}</code>: CRASHED → Auto-restarted")
                else:
                    parts.append(f"❌ <code>{task_name}</code>: CRASHED (Auto-restart not available)")
        else:
            parts.append("✅ All background tasks are running.")

        if recent_news_count == 0:
            parts.append(
                f"\n📭 <b>Zero news items</b> ingested in the last {self._no_news_threshold_seconds // 60} minutes!"
            )
        else:
            parts.append(f"\n📬 Ingestion active: {recent_news_count} news items in last hour.")

        parts.append(f"\n🕐 Time: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        message = "\n".join(parts)

        # 1. Send Telegram alert
        try:
            await send_alert_message(message, parse_mode="HTML")
        except Exception as exc:
            print(f"Watchdog: Failed to send Telegram alert: {exc}")

        # 2. Send FCM push notification
        try:
            from processing.alert_engine import trigger_push_alert
            push_body = f"Dead tasks: {', '.join(dead_tasks) or 'None'}. News in last hour: {recent_news_count}"
            await trigger_push_alert(
                title="⚠️ News Codex: System Health Alert",
                body=push_body,
                alert_type="keyword",
            )
        except Exception as exc:
            print(f"Watchdog: Failed to trigger FCM push alert: {exc}")

        # 3. Send Email alert as a secondary out-of-band notification channel
        try:
            email_subject = "⚠️ News Codex: System Health Alert"
            # Format plain text HTML message for email
            email_body = f"""
            <h3>System Health Alert</h3>
            <p>The watchdog has detected that the system is unhealthy.</p>
            <ul>
                {"".join([f"<li><b>{task_name}</b>: CRASHED</li>" for task_name in dead_tasks]) if dead_tasks else "<li>All background tasks are running.</li>"}
            </ul>
            <p><b>Ingestion Status:</b> {recent_news_count} news items in the last {self._no_news_threshold_seconds // 60} minutes.</p>
            <p><b>Time of check:</b> {now.strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
            <hr/>
            <p><i>This is an automated alert from your News Codex Watchdog.</i></p>
            """
            await send_email_alert(email_subject, email_body)
        except Exception as exc:
            print(f"Watchdog: Failed to send email alert: {exc}")

        self._last_alert_sent_at = now

    def get_health_status(self) -> dict[str, Any]:
        return self._health_status
