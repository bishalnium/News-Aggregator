from __future__ import annotations

import hashlib
from html import escape
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Iterable

from bot.telegram_notifier import send_alert_message
from database import get_pool


URGENCY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
_SIGNAL_REPEAT_WINDOW = timedelta(minutes=45)
_SIGNAL_REPEAT_THRESHOLD = 3
_SIGNAL_REPEAT_ALERT_COOLDOWN = timedelta(minutes=20)
_SIGNAL_SYMBOL_ALERT_COOLDOWN = timedelta(minutes=2)

_SIGNAL_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("\U0001F534", "red-dot symbol"),
    ("\U0001F6A8", "siren symbol"),
    ("\U0001F6D1", "stop-alert symbol"),
    ("\u2757", "high-priority mark"),
    ("\u203c", "double-emphasis mark"),
)
_SIGNAL_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bred\s*alert\b", "red alert phrase"),
    (r"\bhigh\s*alert\b", "high alert phrase"),
    (r"\burgent\b", "urgent keyword"),
    (r"\bbreaking\b", "breaking keyword"),
    (r"\bsiren\b", "siren keyword"),
    (r"\bemergency\b", "emergency keyword"),
)
_SIGNAL_STOPWORDS = {
    "the",
    "and",
    "that",
    "this",
    "with",
    "from",
    "have",
    "will",
    "just",
    "into",
    "your",
    "about",
    "alert",
    "breaking",
    "urgent",
}

_event_hits: dict[str, deque[datetime]] = defaultdict(deque)
_repeat_alert_sent_at: dict[str, datetime] = {}
_symbol_alert_sent_at: dict[str, datetime] = {}


def _keyword_pattern(keyword: str) -> str:
    escaped = re.escape(keyword.strip())
    escaped = escaped.replace(r"\ ", r"\s+")
    if "\\s+" in escaped:
        return escaped
    return rf"\b{escaped}\b"


def _find_keyword_hits(text: str, keywords: Iterable[str]) -> list[str]:
    hits: list[str] = []
    for keyword in keywords:
        if not keyword.strip():
            continue
        pattern = _keyword_pattern(keyword)
        if re.search(pattern, text, flags=re.IGNORECASE):
            hits.append(keyword)
    return hits


def _detect_priority_signals(text: str) -> list[str]:
    hits: list[str] = []
    if not text:
        return hits

    for symbol, label in _SIGNAL_SYMBOLS:
        if symbol in text:
            hits.append(label)

    for pattern, label in _SIGNAL_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            hits.append(label)

    # Preserve order while removing duplicates.
    return list(dict.fromkeys(hits))


def _build_event_signature(text: str) -> str:
    cleaned = re.sub(r"https?://\S+", " ", text or "")
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", cleaned).lower()
    words = [
        token
        for token in cleaned.split()
        if len(token) >= 3 and token not in _SIGNAL_STOPWORDS
    ]

    if words:
        return " ".join(words[:12])

    digest = hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]
    return f"event-{digest}"


def _record_signal_hit(signature: str, now: datetime) -> int:
    hits = _event_hits[signature]
    hits.append(now)

    cutoff = now - _SIGNAL_REPEAT_WINDOW
    while hits and hits[0] < cutoff:
        hits.popleft()

    if not hits:
        _event_hits.pop(signature, None)
        return 0

    return len(hits)


def _should_emit_symbol_alert(signature: str, now: datetime) -> bool:
    previous = _symbol_alert_sent_at.get(signature)
    if previous and now - previous < _SIGNAL_SYMBOL_ALERT_COOLDOWN:
        return False
    _symbol_alert_sent_at[signature] = now
    return True


def _should_emit_repeat_alert(signature: str, now: datetime) -> bool:
    previous = _repeat_alert_sent_at.get(signature)
    if previous and now - previous < _SIGNAL_REPEAT_ALERT_COOLDOWN:
        return False
    _repeat_alert_sent_at[signature] = now
    return True


def _clip_text(text: str | None, max_chars: int = 700) -> str:
    if not text:
        return ""
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3] + "..."


def _bulletize_text(text: str | None, max_points: int = 3) -> str:
    compact = _clip_text(text, max_chars=900)
    if not compact:
        return "1. No summary available"

    sentences = [
        part.strip(" -\n\t")
        for part in re.split(r"(?<=[.!?])\s+", compact)
        if part and part.strip()
    ]

    points = sentences[:max_points] if sentences else [compact]
    return "\n".join(
        [
            f"{index}. {escape(point)}"
            for index, point in enumerate(points, start=1)
        ]
    )


async def check_and_trigger_alerts(
    news_id: int,
    raw_text: str,
    summary: str | None,
    urgency: str | None,
) -> list[str]:
    combined_text = f"{raw_text}\n{summary or ''}"
    normalized_urgency = (urgency or "LOW").upper()
    now = datetime.now(timezone.utc)
    signature = _build_event_signature(combined_text)
    repeated_hits = _record_signal_hit(signature, now)
    priority_signals = _detect_priority_signals(combined_text)

    pool = get_pool()
    matched_topic_names: list[str] = []

    async with pool.acquire() as conn:
        signal_reason_parts: list[str] = []
        if priority_signals and _should_emit_symbol_alert(signature, now):
            signal_reason_parts.append(
                "priority markers detected: " + ", ".join(priority_signals[:4])
            )

        if (
            repeated_hits >= _SIGNAL_REPEAT_THRESHOLD
            and _should_emit_repeat_alert(signature, now)
        ):
            signal_reason_parts.append(
                "similar alert appeared "
                f"{repeated_hits} times in the last {int(_SIGNAL_REPEAT_WINDOW.total_seconds() // 60)} minutes"
            )

        if signal_reason_parts:
            immediate_message = (
                f"<b>Urgency:</b> {escape(normalized_urgency)}\n"
                f"<b>Reason:</b> {escape('; '.join(signal_reason_parts))}\n"
                "<b>Summary:</b>\n"
                f"{_bulletize_text(summary or raw_text, max_points=2)}"
            )
            delivered = await send_alert_message(immediate_message, parse_mode="HTML")
            if delivered:
                await conn.execute(
                    """
                    INSERT INTO alert_log(news_id, topic_id, channel, message_text)
                    VALUES($1, NULL, 'telegram-signal', $2)
                    """,
                    news_id,
                    immediate_message,
                )

        topics = await conn.fetch(
            """
            SELECT id, topic_name, keywords, alert_urgency_threshold
            FROM topics
            WHERE active = true
            """
        )

        for topic in topics:
            topic_threshold = (topic["alert_urgency_threshold"] or "MEDIUM").upper()
            if URGENCY_RANK.get(normalized_urgency, 1) < URGENCY_RANK.get(topic_threshold, 2):
                continue

            keywords = topic["keywords"] or []
            hits = _find_keyword_hits(combined_text, keywords)
            if not hits:
                continue

            matched_topic_names.append(topic["topic_name"])

            message = (
                f"<b>Topic:</b> {escape(str(topic['topic_name']))}\n"
                f"<b>Urgency:</b> {escape(normalized_urgency)}\n"
                f"<b>Matched Keywords:</b> {escape(', '.join(hits[:6]))}\n"
                "<b>Summary:</b>\n"
                f"{_bulletize_text(summary or raw_text, max_points=3)}"
            )

            delivered = await send_alert_message(message, parse_mode="HTML")
            if delivered:
                await conn.execute(
                    """
                    INSERT INTO alert_log(news_id, topic_id, channel, message_text)
                    VALUES($1, $2, 'telegram-alert', $3)
                    """,
                    news_id,
                    topic["id"],
                    message,
                )
            else:
                print(f"Alert delivery failed for topic {topic['id']}")

        if matched_topic_names:
            await conn.execute(
                "UPDATE news SET matched_topics = $1 WHERE id = $2",
                matched_topic_names,
                news_id,
            )

    return matched_topic_names
