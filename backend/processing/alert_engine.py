from __future__ import annotations

import asyncio
import hashlib
from html import escape
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from bot.telegram_notifier import send_alert_message, send_context_alert_message
from database import get_pool, get_all_fcm_tokens
from bot.fcm_notifier import send_push_notification
from processing.llm_classifier import potentials_context_alert_match, verify_context_alert_match


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


# ---------------------------------------------------------------------------
# In-memory topic cache — refreshed every 30 seconds
# ---------------------------------------------------------------------------

_cached_topics: list[dict[str, Any]] = []
_cache_last_refresh: datetime | None = None
_CACHE_TTL_SECONDS = 30
_cache_lock = asyncio.Lock()

# Instant alert dedup: {(topic_id, content_hash): timestamp}
_instant_alert_sent: dict[tuple[int, str], datetime] = {}
_INSTANT_ALERT_COOLDOWN = timedelta(minutes=2)


async def get_cached_active_topics() -> list[dict[str, Any]]:
    """Return active topics from in-memory cache. Refreshes from DB every 30s."""
    global _cached_topics, _cache_last_refresh

    now = datetime.now(timezone.utc)

    if (
        _cache_last_refresh is not None
        and (now - _cache_last_refresh).total_seconds() < _CACHE_TTL_SECONDS
    ):
        return _cached_topics

    async with _cache_lock:
        # Double-check after acquiring lock
        if (
            _cache_last_refresh is not None
            and (now - _cache_last_refresh).total_seconds() < _CACHE_TTL_SECONDS
        ):
            return _cached_topics

        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, topic_name, keywords, alert_urgency_threshold, active
                    FROM topics
                    WHERE active = true
                    """
                )
            _cached_topics = [dict(row) for row in rows]
            _cache_last_refresh = now
        except Exception as exc:
            print(f"Topic cache refresh failed: {exc}")
            # Return stale cache if available
            if not _cached_topics:
                _cached_topics = []

    return _cached_topics


def invalidate_topic_cache() -> None:
    """Call after topic CRUD operations to force cache refresh on next check."""
    global _cache_last_refresh
    _cache_last_refresh = None


def _cleanup_instant_dedup(now: datetime) -> None:
    """Remove expired instant alert dedup entries."""
    expired = [
        key
        for key, sent_at in _instant_alert_sent.items()
        if now - sent_at > _INSTANT_ALERT_COOLDOWN
    ]
    for key in expired:
        _instant_alert_sent.pop(key, None)



async def trigger_push_alert(title: str, body: str, alert_type: str) -> None:
    try:
        tokens = await get_all_fcm_tokens()
        if tokens:
            asyncio.create_task(send_push_notification(tokens, title, body, alert_type))
    except Exception as e:
        print(f"FCM: Failed to trigger push alert: {e}")


# ---------------------------------------------------------------------------
# Instant keyword alert — fires BEFORE pipeline processing
# ---------------------------------------------------------------------------

async def instant_keyword_alert(raw_text: str, content_hash: str) -> list[str]:
    """Check raw text against ALL active topic keywords instantly.

    Called from enqueue_news() BEFORE the message enters the processing queue.
    Pure regex matching — no LLM, no DB query for the match itself.
    Returns list of matched topic names.
    """
    topics = await get_cached_active_topics()
    if not topics:
        return []

    now = datetime.now(timezone.utc)
    _cleanup_instant_dedup(now)

    matched_topic_names: list[str] = []

    for topic in topics:
        topic_id = topic["id"]
        keywords = topic.get("keywords") or []

        # Check dedup: don't re-alert same topic+message within cooldown
        dedup_key = (topic_id, content_hash)
        if dedup_key in _instant_alert_sent:
            continue

        hits = _find_keyword_hits(raw_text, keywords)
        if not hits:
            continue

        matched_topic_names.append(topic["topic_name"])
        _instant_alert_sent[dedup_key] = now

        # Fire the alert immediately
        message = (
            f"<b>⚡ INSTANT ALERT</b>\n"
            f"<b>Topic:</b> {escape(str(topic['topic_name']))}\n"
            f"<b>Matched Keywords:</b> {escape(', '.join(hits[:6]))}\n"
            f"<b>Message:</b>\n"
            f"{_clip_text(raw_text, max_chars=600)}"
        )

        try:
            delivered = await send_alert_message(message, parse_mode="HTML")
            if delivered:
                # Log to alert_log (fire-and-forget, don't block)
                asyncio.create_task(
                    _log_instant_alert(topic_id, content_hash, message)
                )
                # Send FCM push notification (fire-and-forget, don't block)
                push_body = f"Keywords: {', '.join(hits[:6])}\n{raw_text[:150]}"
                if len(raw_text) > 150:
                    push_body += "..."
                asyncio.create_task(
                    trigger_push_alert(
                        title=f"⚡ Instant Alert: {topic['topic_name']}",
                        body=push_body,
                        alert_type="keyword"
                    )
                )
        except Exception as exc:
            print(f"Instant alert delivery failed for topic {topic_id}: {exc}")

    return matched_topic_names


async def _log_instant_alert(
    topic_id: int,
    content_hash: str,
    message_text: str,
) -> None:
    """Log instant alert to alert_log. Best-effort, non-blocking."""
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            # Try to find the news_id by content_hash (might not exist yet)
            news_id = await conn.fetchval(
                "SELECT id FROM news WHERE content_hash = $1",
                content_hash,
            )
            await conn.execute(
                """
                INSERT INTO alert_log(news_id, topic_id, channel, message_text)
                VALUES($1, $2, 'telegram-instant', $3)
                """,
                news_id,  # May be None if news not stored yet — that's OK
                topic_id,
                message_text,
            )
    except Exception as exc:
        print(f"Instant alert log failed: {exc}")


# ---------------------------------------------------------------------------
# Keyword matching helpers (shared by instant + post-DB alerts)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Priority signal detection
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Post-DB alert check (runs AFTER news is stored and classified)
# ---------------------------------------------------------------------------

async def check_and_trigger_alerts(
    news_id: int,
    raw_text: str,
    summary: str | None,
    urgency: str | None,
    already_alerted_topics: list[str] | None = None,
) -> list[str]:
    """Post-DB alert check with urgency threshold filtering.

    Skips topics that already fired an instant alert for this news item.
    """
    combined_text = f"{raw_text}\n{summary or ''}"
    normalized_urgency = (urgency or "LOW").upper()
    now = datetime.now(timezone.utc)
    signature = _build_event_signature(combined_text)
    repeated_hits = _record_signal_hit(signature, now)
    priority_signals = _detect_priority_signals(combined_text)

    # Topics already alerted by instant_keyword_alert
    skip_topics = set(already_alerted_topics or [])

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
                # Send FCM push notification (fire-and-forget, don't block)
                push_body = f"Reason: {'; '.join(signal_reason_parts)}\n{summary or raw_text}"
                if len(push_body) > 150:
                    push_body = push_body[:150] + "..."
                asyncio.create_task(
                    trigger_push_alert(
                        title=f"📡 Signal Alert ({normalized_urgency})",
                        body=push_body,
                        alert_type="keyword"
                    )
                )

        topics = await conn.fetch(
            """
            SELECT id, topic_name, keywords, alert_urgency_threshold
            FROM topics
            WHERE active = true
            """
        )

        for topic in topics:
            topic_name = topic["topic_name"]

            # Skip if instant alert already fired for this topic
            if topic_name in skip_topics:
                matched_topic_names.append(topic_name)
                continue

            topic_threshold = (topic["alert_urgency_threshold"] or "MEDIUM").upper()
            if URGENCY_RANK.get(normalized_urgency, 1) < URGENCY_RANK.get(topic_threshold, 2):
                continue

            keywords = topic["keywords"] or []
            hits = _find_keyword_hits(combined_text, keywords)
            if not hits:
                continue

            matched_topic_names.append(topic_name)

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
                # Send FCM push notification (fire-and-forget, don't block)
                push_body = f"Keywords: {', '.join(hits[:6])}\n{summary or raw_text}"
                if len(push_body) > 150:
                    push_body = push_body[:150] + "..."
                asyncio.create_task(
                    trigger_push_alert(
                        title=f"🎯 Topic Alert: {topic_name}",
                        body=push_body,
                        alert_type="keyword"
                    )
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


# ---------------------------------------------------------------------------
# Context/Phrase-based matching alert system
# ---------------------------------------------------------------------------

_context_alert_cooldowns: dict[int, datetime] = {}
_CONTEXT_ALERT_COOLDOWN = timedelta(minutes=3)


async def check_context_alerts(news_id: int, raw_text: str, summary: str | None) -> None:
    """Check a newly ingested news item against all active context-based alerts.
    
    1. Fetch active context alerts.
    2. Filter candidates using potentials_context_alert_match (GPT-OSS).
    3. Verify candidate matches using verify_context_alert_match (GLM 4.7).
    4. If verified, send Telegram notification.
    """
    now = datetime.now(timezone.utc)
    # clean up expired cooldowns
    expired_ids = [
        alert_id for alert_id, cooldown_until in _context_alert_cooldowns.items()
        if now > cooldown_until
    ]
    for alert_id in expired_ids:
        _context_alert_cooldowns.pop(alert_id, None)

    pool = get_pool()
    async with pool.acquire() as conn:
        active_alerts = await conn.fetch(
            "SELECT id, context_description FROM context_alerts WHERE active = true"
        )
        if not active_alerts:
            return

        # 1. GPT-OSS filter candidates
        news_content = f"{raw_text}\n{summary or ''}"
        candidate_ids = await potentials_context_alert_match(news_content, active_alerts)
        if not candidate_ids:
            return

        # Map candidate IDs back to alerts
        alert_map = {alert["id"]: alert for alert in active_alerts}

        for alert_id in candidate_ids:
            alert = alert_map.get(alert_id)
            if not alert:
                continue

            # check cooldown
            cooldown_until = _context_alert_cooldowns.get(alert_id)
            if cooldown_until and now <= cooldown_until:
                continue

            # 2. GLM 4.7 strict verify
            context_description = alert["context_description"]
            is_match = await verify_context_alert_match(news_content, context_description)
            if not is_match:
                continue

            # Set cooldown immediately to avoid concurrent duplicates
            _context_alert_cooldowns[alert_id] = now + _CONTEXT_ALERT_COOLDOWN

            # 3. Send Telegram Alert
            short_desc = context_description
            if len(short_desc) > 120:
                short_desc = short_desc[:120] + "..."
            message = (
                f"🎯 <b>SITUATION ALERT</b>\n\n"
                f"<b>Summary:</b>\n"
                f"{_bulletize_text(summary or raw_text, max_points=3)}\n\n"
                f"<b>Matched Alert:</b> {escape(short_desc)}"
            )

            try:
                delivered = await send_context_alert_message(message, parse_mode="HTML")
                if delivered:
                    # Log to database
                    await conn.execute(
                        """
                        INSERT INTO alert_log (news_id, topic_id, channel, message_text)
                        VALUES ($1, NULL, 'telegram-context', $2)
                        """,
                        news_id,
                        message
                    )
                    # Send FCM push notification (fire-and-forget, don't block)
                    push_body = f"Alert: {short_desc}\n{summary or raw_text}"
                    if len(push_body) > 150:
                        push_body = push_body[:150] + "..."
                    asyncio.create_task(
                        trigger_push_alert(
                            title="🎯 Situation Alert",
                            body=push_body,
                            alert_type="context"
                        )
                    )
            except Exception as exc:
                print(f"Failed to deliver context alert {alert_id}: {exc}")
