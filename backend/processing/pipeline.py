from __future__ import annotations

import asyncio
from collections import deque
from difflib import SequenceMatcher
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from api.websocket import websocket_manager
from config import settings
from database import get_pool
from processing.alert_engine import check_and_trigger_alerts
from processing.llm_classifier import classify_news, classify_news_heuristic


class NewsPipeline:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)
        self._workers: list[asyncio.Task] = []
        self._recent_items: deque[tuple[str, str, datetime]] = deque(maxlen=1000)
        self._dedupe_lock = asyncio.Lock()
        self._running = False

    async def start(self, worker_count: int = 2) -> None:
        if self._running:
            return
        self._running = True
        self._workers = [
            asyncio.create_task(self._worker_loop(index), name=f"pipeline-worker-{index}")
            for index in range(worker_count)
        ]

    async def stop(self) -> None:
        self._running = False
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._workers.clear()

    async def enqueue_news(self, payload: dict[str, Any]) -> None:
        raw_text = (payload.get("raw_text") or "").strip()
        if len(raw_text) < 3:
            return
        payload["raw_text"] = raw_text
        content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        payload["content_hash"] = content_hash

        if await self._is_recent_memory_duplicate(payload):
            return

        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            print("Pipeline queue is full, dropping incoming message")
            return

        if payload.get("source") == "telegram":
            await self._broadcast_raw_news(payload, content_hash)

    async def _is_recent_memory_duplicate(self, payload: dict[str, Any]) -> bool:
        normalized = _normalize_for_dedupe(payload.get("raw_text") or "")
        if len(normalized) < 12:
            return False

        now = datetime.now(timezone.utc)
        window = timedelta(seconds=settings.news_dedupe_window_seconds)
        source_key = _source_key(payload)

        async with self._dedupe_lock:
            cutoff = now - window
            while self._recent_items and self._recent_items[0][2] < cutoff:
                self._recent_items.popleft()

            for previous_source_key, previous_text, _ in self._recent_items:
                if previous_source_key != source_key:
                    continue
                if _is_near_duplicate(previous_text, normalized):
                    print(f"Dropped near-duplicate news item from {source_key}")
                    return True

            self._recent_items.append((source_key, normalized, now))
            return False

    async def _is_recent_database_duplicate(self, payload: dict[str, Any]) -> bool:
        normalized = _normalize_for_dedupe(payload.get("raw_text") or "")
        if len(normalized) < 12:
            return False

        pool = get_pool()
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=settings.news_dedupe_window_seconds
        )
        source = payload.get("source", "unknown")
        source_channel = payload.get("source_channel", "unknown")

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, raw_text
                FROM news
                WHERE source = $1 AND source_channel = $2 AND fetched_at >= $3
                ORDER BY fetched_at DESC
                LIMIT 50
                """,
                source,
                source_channel,
                cutoff,
            )

        for row in rows:
            previous_text = _normalize_for_dedupe(row.get("raw_text") or "")
            if _is_near_duplicate(previous_text, normalized):
                print(
                    "Dropped near-duplicate news item already stored "
                    f"as news id {row.get('id')}"
                )
                return True

        return False

    async def _worker_loop(self, worker_index: int) -> None:
        while True:
            payload = await self._queue.get()
            try:
                await self._process_payload(payload)
            except Exception as exc:
                print(f"Pipeline worker {worker_index} error: {exc}")
            finally:
                self._queue.task_done()

    async def _process_payload(self, payload: dict[str, Any]) -> None:
        raw_text = payload["raw_text"]
        content_hash = payload.get("content_hash") or hashlib.sha256(
            raw_text.encode("utf-8")
        ).hexdigest()

        pool = get_pool()
        async with pool.acquire() as conn:
            existing_row = await conn.fetchrow(
                """
                SELECT id, content_hash, source, source_channel, raw_text, url, summary,
                       category, urgency, sentiment, instruments_affected,
                       matched_topics, llm_processed, fetched_at, published_at
                FROM news
                WHERE content_hash = $1
                """,
                content_hash,
            )

        if existing_row:
            await self._broadcast_enriched_news(dict(existing_row))
            return

        if await self._is_recent_database_duplicate(payload):
            return

        use_fast_telegram_path = (
            payload.get("source") == "telegram" and settings.telegram_fast_classification
        )

        if use_fast_telegram_path:
            llm = classify_news_heuristic(raw_text)
            llm_processed = False
        else:
            llm = await classify_news(raw_text)
            llm_processed = True

        published_at = _normalize_datetime(payload.get("published_at"))

        async with pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO news (
                        source,
                        source_channel,
                        raw_text,
                        url,
                        content_hash,
                        summary,
                        category,
                        urgency,
                        sentiment,
                        instruments_affected,
                        matched_topics,
                        llm_processed,
                        published_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
                    )
                    """,
                    payload.get("source", "unknown"),
                    payload.get("source_channel", "unknown"),
                    raw_text,
                    payload.get("url"),
                    content_hash,
                    llm.get("summary"),
                    llm.get("category", "other"),
                    (llm.get("urgency") or "LOW").upper(),
                    (llm.get("sentiment") or "neutral").lower(),
                    llm.get("instruments_affected") or [],
                    [],
                    llm_processed,
                    published_at,
                )
            except Exception as exc:
                if not _is_duplicate_hash_error(exc):
                    raise

            news_row = await conn.fetchrow(
                """
                SELECT id, content_hash, source, source_channel, raw_text, url, summary,
                       category, urgency, sentiment, instruments_affected,
                       matched_topics, llm_processed, fetched_at, published_at
                FROM news
                WHERE content_hash = $1
                """,
                content_hash,
            )

        if not news_row:
            return

        matched_topics = await check_and_trigger_alerts(
            news_id=int(news_row["id"]),
            raw_text=news_row["raw_text"],
            summary=news_row["summary"],
            urgency=news_row["urgency"],
        )

        payload_json = _record_to_json(dict(news_row))
        payload_json["matched_topics"] = matched_topics
        payload_json["provisional"] = False

        await self._broadcast_enriched_news(payload_json)

    async def _broadcast_raw_news(
        self,
        payload: dict[str, Any],
        content_hash: str,
    ) -> None:
        published_at = _normalize_datetime(payload.get("published_at"))
        provisional_payload = {
            "id": None,
            "content_hash": content_hash,
            "source": payload.get("source", "unknown"),
            "source_channel": payload.get("source_channel", "unknown"),
            "raw_text": payload.get("raw_text", ""),
            "url": payload.get("url"),
            "summary": payload.get("raw_text", ""),
            "category": "other",
            "urgency": "LOW",
            "sentiment": "neutral",
            "instruments_affected": [],
            "matched_topics": [],
            "llm_processed": False,
            "fetched_at": datetime.now(timezone.utc),
            "published_at": published_at,
            "provisional": True,
            "client_temp_id": f"tmp-{content_hash[:16]}",
        }

        await websocket_manager.broadcast_json(
            {
                "type": "news_item",
                "data": _record_to_json(provisional_payload),
            }
        )

    async def _broadcast_enriched_news(self, payload_json: dict[str, Any]) -> None:
        payload_json["provisional"] = False

        await websocket_manager.broadcast_json(
            {
                "type": "news_item",
                "data": payload_json,
            }
        )


def _normalize_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    if isinstance(value, str):
        txt = value.strip()
        if not txt:
            return None
        txt = txt.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(txt)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None

    return None


def _record_to_json(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _is_duplicate_hash_error(exc: Exception) -> bool:
    text = str(exc)
    return "1062" in text and "news.content_hash" in text


def _source_key(payload: dict[str, Any]) -> str:
    source = str(payload.get("source") or "unknown").strip().lower()
    channel = str(payload.get("source_channel") or "unknown").strip().lower()
    return f"{source}:{channel}"


def _normalize_for_dedupe(text: str) -> str:
    cleaned = re.sub(r"https?://\S+", " ", text or "")
    cleaned = re.sub(r"[^0-9a-zA-Z$%]+", " ", cleaned).lower()
    tokens = [token for token in cleaned.split() if len(token) > 1]
    return " ".join(tokens)


def _is_near_duplicate(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    if previous == current:
        return True

    max_len = max(len(previous), len(current))
    if max_len == 0:
        return False
    length_gap = abs(len(previous) - len(current)) / max_len
    if length_gap > 0.35:
        return False

    ratio = SequenceMatcher(None, previous, current, autojunk=False).ratio()
    return ratio >= settings.news_dedupe_similarity
