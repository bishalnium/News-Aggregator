from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from database import get_pool
from models import ChatRequest, ChatResponse
from processing.llm_classifier import answer_with_news_context


router = APIRouter(prefix="/chat", tags=["chat"])


_MONTH_LOOKUP = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _month_start_end(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = calendar.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc) + timedelta(seconds=1)
    return start, end


def _try_parse_month_scope(text: str, now: datetime) -> tuple[datetime, datetime, str] | None:
    lowered = text.lower()
    matched_month = None
    for month_name, month_num in _MONTH_LOOKUP.items():
        if month_name in lowered:
            matched_month = (month_name, month_num)
            break

    if not matched_month:
        return None

    month_name, month_num = matched_month
    year_match = re.search(r"\b(20\d{2})\b", lowered)
    if year_match:
        year = int(year_match.group(1))
    else:
        year = now.year
        if month_num > now.month:
            year -= 1

    start, end = _month_start_end(year, month_num)
    label = f"{month_name.title()} {year}"
    return start, end, label


def _detect_window_scope(message: str) -> tuple[datetime, datetime, str]:
    now = datetime.now(timezone.utc)
    lowered = message.lower()

    explicit_month = _try_parse_month_scope(lowered, now)
    if explicit_month:
        return explicit_month

    if "previous week" in lowered or "last week" in lowered:
        week_start = (now - timedelta(days=now.weekday() + 7)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        week_end = week_start + timedelta(days=7)
        return week_start, week_end, "previous week"

    if "this week" in lowered or "current week" in lowered:
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        week_end = now + timedelta(seconds=1)
        return week_start, week_end, "this week"

    if "2 month" in lowered or "two month" in lowered:
        return now - timedelta(days=60), now + timedelta(seconds=1), "last 2 months"
    if "month" in lowered:
        return now - timedelta(days=30), now + timedelta(seconds=1), "last 30 days"
    if "week" in lowered:
        return now - timedelta(days=7), now + timedelta(seconds=1), "last 7 days"
    if "yesterday" in lowered:
        return now - timedelta(days=2), now + timedelta(seconds=1), "last 48 hours"
    if "today" in lowered:
        return now - timedelta(days=1), now + timedelta(seconds=1), "last 24 hours"

    return now - timedelta(days=3), now + timedelta(seconds=1), "last 72 hours"


def _build_time_buckets(rows: list[dict]) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    month_buckets: dict[str, int] = {}
    week_buckets: dict[str, int] = {}
    day_buckets: dict[str, int] = {}

    for row in rows:
        fetched_at = row.get("fetched_at")
        if not isinstance(fetched_at, datetime):
            continue

        month_key = fetched_at.strftime("%Y-%m")
        iso_year, iso_week, _ = fetched_at.isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"
        day_key = fetched_at.strftime("%Y-%m-%d")

        month_buckets[month_key] = month_buckets.get(month_key, 0) + 1
        week_buckets[week_key] = week_buckets.get(week_key, 0) + 1
        day_buckets[day_key] = day_buckets.get(day_key, 0) + 1

    month_buckets = dict(sorted(month_buckets.items(), reverse=True))
    week_buckets = dict(sorted(week_buckets.items(), reverse=True))
    day_buckets = dict(sorted(day_buckets.items(), reverse=True))

    return month_buckets, week_buckets, day_buckets


def _format_bucket_digest(
    month_buckets: dict[str, int],
    week_buckets: dict[str, int],
    day_buckets: dict[str, int],
) -> str:
    month_text = ", ".join([f"{key}: {value}" for key, value in list(month_buckets.items())[:8]])
    week_text = ", ".join([f"{key}: {value}" for key, value in list(week_buckets.items())[:8]])
    day_text = ", ".join([f"{key}: {value}" for key, value in list(day_buckets.items())[:10]])

    return (
        f"Monthly buckets -> {month_text or 'none'}\n"
        f"Weekly buckets -> {week_text or 'none'}\n"
        f"Daily buckets -> {day_text or 'none'}"
    )


@router.post("", response_model=ChatResponse)
async def ask_chat(payload: ChatRequest) -> ChatResponse:
    from_time, to_time, window_label = _detect_window_scope(payload.message)
    is_broad_window = (to_time - from_time) >= timedelta(days=21)
    context_limit = 1600 if is_broad_window else 900

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT fetched_at, source, source_channel, raw_text, summary, urgency, sentiment
            FROM news
            WHERE fetched_at >= $1 AND fetched_at < $2
            ORDER BY fetched_at DESC
            LIMIT $3
            """,
            from_time,
            to_time,
            context_limit,
        )

    records = [dict(row) for row in rows]
    month_buckets, week_buckets, day_buckets = _build_time_buckets(records)
    bucket_digest = _format_bucket_digest(month_buckets, week_buckets, day_buckets)

    answer = await answer_with_news_context(
        payload.message,
        records,
        time_bucket_digest=bucket_digest,
    )

    return ChatResponse(
        answer=answer,
        used_news_items=len(records),
        window_used=window_label,
        month_buckets=month_buckets,
        week_buckets=week_buckets,
        day_buckets=day_buckets,
    )
