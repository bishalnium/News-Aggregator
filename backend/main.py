from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import alerts, chat, news, settings as settings_api, topics, websocket
from config import runtime_state, settings
from database import close_pool, init_pool, init_schema, load_runtime_settings, load_proxy_setting
from ingestion.telegram_listener import TelegramListener
from processing.pipeline import NewsPipeline
from processing.summarizer import RollingSummarizer


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    await init_schema()
    loaded_interval = await load_runtime_settings()
    await load_proxy_setting()

    # Initialize Firebase Cloud Messaging
    from bot.fcm_notifier import init_fcm
    init_fcm()

    pipeline = NewsPipeline()
    await pipeline.start(worker_count=4)

    summarizer = RollingSummarizer()
    await summarizer.start()

    telegram_listener = TelegramListener(pipeline.enqueue_news)

    telegram_task = asyncio.create_task(telegram_listener.run(), name="telegram-listener")

    app.state.pipeline = pipeline
    app.state.summarizer = summarizer
    app.state.telegram_listener = telegram_listener
    app.state.background_tasks = [telegram_task]

    print(
        "Backend started. "
        f"Summary interval: {loaded_interval}s | "
        f"Telegram channels: {settings.telegram_channels}"
    )

    try:
        yield
    finally:
        for task in app.state.background_tasks:
            task.cancel()

        for task in app.state.background_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                print(f"Background task shutdown error: {exc}")

        await telegram_listener.stop()
        await summarizer.stop()
        await pipeline.stop()
        await close_pool()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

allowed_origins = [
    settings.frontend_url,
    "https://news-aggregator-seven-rho.vercel.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(dict.fromkeys(allowed_origins)),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(news.router, prefix="/api")
app.include_router(topics.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(settings_api.router, prefix="/api")
app.include_router(websocket.router, prefix="/api")


@app.get("/health")
async def health() -> dict:
    task_statuses = {}

    # 1. Check background tasks
    telegram_listener = getattr(app.state, "telegram_listener", None)
    if telegram_listener:
        was_started = getattr(telegram_listener, "was_started", False)
        is_active = getattr(telegram_listener, "is_active", False)
        task_statuses["telegram-listener"] = {
            "alive": is_active or not was_started,
            "was_started": was_started,
            "running": is_active
        }


    # 2. Check NewsPipeline workers
    pipeline = getattr(app.state, "pipeline", None)
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
    summarizer = getattr(app.state, "summarizer", None)
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

    # 4. Check database news count in last hour
    recent_news_count = 0
    try:
        from database import get_pool
        pool = get_pool()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT COUNT(*) FROM news WHERE fetched_at >= $1",
                cutoff
            )
            recent_news_count = int(val or 0)
    except Exception as exc:
        print(f"Health check failed to query news count: {exc}")

    # Determine overall health
    is_healthy = all(status.get("alive", False) for status in task_statuses.values())

    return {
        "status": "ok" if is_healthy else "error",
        "app": settings.app_name,
        "is_healthy": is_healthy,
        "tasks": task_statuses,
        "recent_news_count": recent_news_count,
        "summary_interval_seconds": runtime_state.get_summary_interval_seconds(),
    }
