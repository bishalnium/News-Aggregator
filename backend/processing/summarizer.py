from __future__ import annotations

import asyncio
from html import escape
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from api.websocket import websocket_manager
from bot.telegram_notifier import send_summary_message
from config import runtime_state
from database import get_pool
from processing.llm_classifier import summarize_news_window


class RollingSummarizer:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False
        self._next_run_at: datetime | None = None
        self._last_schedule_version = -1

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="rolling-summarizer")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while self._running:
            interval, anchor_utc, schedule_version = runtime_state.get_summary_schedule()

            if schedule_version != self._last_schedule_version:
                self._last_schedule_version = schedule_version
                self._next_run_at = anchor_utc + timedelta(seconds=interval)

            now = datetime.now(timezone.utc)
            if self._next_run_at is None:
                self._next_run_at = now + timedelta(seconds=interval)

            if now >= self._next_run_at:
                window_end = self._next_run_at
                window_start = window_end - timedelta(seconds=interval)

                try:
                    await self.run_once(
                        window_seconds=interval,
                        window_start=window_start,
                        window_end=window_end,
                    )
                except Exception as exc:
                    print(f"Summary loop error: {exc}")
                finally:
                    self._next_run_at = self._next_run_at + timedelta(seconds=interval)

                continue

            sleep_seconds = min(0.25, (self._next_run_at - now).total_seconds())
            await asyncio.sleep(max(0.1, sleep_seconds))

    async def run_once(
        self,
        window_seconds: int,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> dict[str, Any] | None:
        resolved_window_end = window_end or datetime.now(timezone.utc)
        resolved_window_start = window_start or (
            resolved_window_end - timedelta(seconds=window_seconds)
        )

        pool = get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, source, source_channel, raw_text, summary, fetched_at
                FROM news
                WHERE fetched_at >= $1 AND fetched_at <= $2
                ORDER BY fetched_at ASC
                LIMIT 500
                """,
                resolved_window_start,
                resolved_window_end,
            )

        if not rows:
            return None

        row_dicts = [dict(row) for row in rows]
        digest = await summarize_news_window(row_dicts, window_seconds)
        sources = sorted({str(row.get("source", "unknown")) for row in row_dicts})
        source_channels = sorted(
            {
                str(row.get("source_channel"))
                for row in row_dicts
                if row.get("source_channel")
            }
        )

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO summary_batches(
                    window_seconds,
                    window_start,
                    window_end,
                    summary_text,
                    item_count,
                    sources,
                    source_channels
                )
                VALUES($1, $2, $3, $4, $5, $6, $7)
                """,
                window_seconds,
                resolved_window_start,
                resolved_window_end,
                digest,
                len(rows),
                sources,
                source_channels,
            )

            batch_id = await conn.fetchval("SELECT LAST_INSERT_ID()")
            batch = await conn.fetchrow(
                """
                SELECT id, window_seconds, window_start, window_end,
                       summary_text, item_count, sources, source_channels, created_at
                FROM summary_batches
                WHERE id = $1
                """,
                batch_id,
            )

        if not batch:
            return None

        batch_payload = _record_to_json(dict(batch))

        summary_message = _format_summary_message(batch_payload)
        await send_summary_message(summary_message, parse_mode="HTML")

        await websocket_manager.broadcast_json(
            {
                "type": "summary_batch",
                "data": batch_payload,
            }
        )

        return batch_payload


def _record_to_json(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _format_summary_message(batch_payload: dict[str, Any]) -> str:
    item_count = int(batch_payload.get("item_count") or 0)
    summary_text = _clean_summary_text(str(batch_payload.get("summary_text") or ""))
    level, label = _detect_critical_level(summary_text, item_count)
    points = _to_bullet_points(summary_text)

    lines = [
        f"<b>Critical News Level {level} - {label}</b>",
        f"<b>Items:</b> {item_count}",
        "<b>Key Points:</b>",
    ]
    lines.extend(
        [
            f"{index}. {escape(point)}"
            for index, point in enumerate(points, start=1)
        ]
    )
    return "\n".join(lines)


def _clean_summary_text(text: str) -> str:
    cleaned = re.sub(r"\.{3,}|…+", " ", text or "")
    cleaned_lines: list[str] = []
    for line in cleaned.splitlines():
        stripped = re.sub(r"^\s*[-*]\s*(telegram|twitter|source)\s*:\s*", "- ", line, flags=re.IGNORECASE)
        stripped = re.sub(r"^\s*sources?\s*:\s*.*$", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if stripped:
            cleaned_lines.append(stripped)
    return "\n".join(cleaned_lines)


def _to_bullet_points(text: str, max_points: int = 6) -> list[str]:
    cleaned_lines = [
        re.sub(r"^\s*[-*•]\s*", "", line).strip()
        for line in text.splitlines()
    ]
    cleaned_lines = [
        line for line in cleaned_lines if line and not line.lower().startswith("latest updates:")
    ]

    if len(cleaned_lines) <= 1:
        source = cleaned_lines[0] if cleaned_lines else text.strip()
        cleaned_lines = [
            part.strip(" -\n\t")
            for part in re.split(r"(?<=[.!?])\s+", source)
            if part and part.strip()
        ]

    unique: list[str] = []
    seen: set[str] = set()
    for line in cleaned_lines:
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(line)
        if len(unique) >= max_points:
            break

    if not unique:
        return ["No major updates in this cycle."]
    return unique


def _detect_critical_level(summary_text: str, item_count: int) -> tuple[int, str]:
    lowered = summary_text.lower()
    severe_terms = {
        "war",
        "missile",
        "attack",
        "airstrike",
        "drone",
        "explosion",
        "emergency",
        "nuclear",
        "evacuation",
        "martial law",
    }
    elevated_terms = {
        "fed",
        "rate",
        "inflation",
        "cpi",
        "ppi",
        "opec",
        "oil",
        "sanction",
        "talks",
        "negotiation",
        "recession",
        "default",
        "volatility",
        "crypto",
    }

    def _contains_term(term: str) -> bool:
        pattern = rf"\b{re.escape(term)}\b"
        return re.search(pattern, lowered) is not None

    severe_hits = sum(1 for term in severe_terms if _contains_term(term))
    elevated_hits = sum(1 for term in elevated_terms if _contains_term(term))

    if severe_hits >= 1:
        return 3, "Extreme"
    if elevated_hits >= 1 or item_count >= 15:
        return 2, "Elevated"
    return 1, "Normal"
