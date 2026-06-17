from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import alerts, chat, news, settings as settings_api, topics, websocket
from config import runtime_state, settings
from database import close_pool, init_pool, init_schema, load_runtime_settings, load_proxy_setting
from ingestion.telegram_listener import TelegramListener
from ingestion.twitter_poller import TwitterPoller
from processing.pipeline import NewsPipeline
from processing.summarizer import RollingSummarizer


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    await init_schema()
    loaded_interval = await load_runtime_settings()
    await load_proxy_setting()


    pipeline = NewsPipeline()
    await pipeline.start(worker_count=4)

    summarizer = RollingSummarizer()
    await summarizer.start()

    telegram_listener = TelegramListener(pipeline.enqueue_news)
    twitter_poller = TwitterPoller(pipeline.enqueue_news)

    telegram_task = asyncio.create_task(telegram_listener.run(), name="telegram-listener")
    twitter_task = asyncio.create_task(twitter_poller.run(), name="twitter-poller")

    app.state.pipeline = pipeline
    app.state.summarizer = summarizer
    app.state.telegram_listener = telegram_listener
    app.state.twitter_poller = twitter_poller
    app.state.background_tasks = [telegram_task, twitter_task]

    print(
        "Backend started. "
        f"Summary interval: {loaded_interval}s | "
        f"Telegram channels: {settings.telegram_channels} | "
        f"Twitter handles: {settings.twitter_handles}"
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
    return {
        "status": "ok",
        "app": settings.app_name,
        "summary_interval_seconds": runtime_state.get_summary_interval_seconds(),
    }
