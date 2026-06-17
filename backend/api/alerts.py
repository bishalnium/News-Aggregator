from __future__ import annotations

from fastapi import APIRouter, Query

from database import get_pool
from models import AlertItem


router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertItem])
async def list_alerts(
    limit: int = Query(default=100, ge=1, le=500),
    alert_type: str | None = Query(default=None),
) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        if alert_type == "keyword":
            rows = await conn.fetch(
                """
                SELECT
                    al.id,
                    al.news_id,
                    al.topic_id,
                    t.topic_name,
                    n.urgency,
                    n.summary AS news_summary,
                    al.channel,
                    al.sent_at,
                    al.message_text
                FROM alert_log al
                LEFT JOIN topics t ON t.id = al.topic_id
                LEFT JOIN news n ON n.id = al.news_id
                WHERE al.channel IN ('telegram-instant', 'telegram-alert', 'telegram-signal')
                ORDER BY al.sent_at DESC
                LIMIT $1
                """,
                limit,
            )
        elif alert_type == "context":
            rows = await conn.fetch(
                """
                SELECT
                    al.id,
                    al.news_id,
                    al.topic_id,
                    t.topic_name,
                    n.urgency,
                    n.summary AS news_summary,
                    al.channel,
                    al.sent_at,
                    al.message_text
                FROM alert_log al
                LEFT JOIN topics t ON t.id = al.topic_id
                LEFT JOIN news n ON n.id = al.news_id
                WHERE al.channel = 'telegram-context'
                ORDER BY al.sent_at DESC
                LIMIT $1
                """,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT
                    al.id,
                    al.news_id,
                    al.topic_id,
                    t.topic_name,
                    n.urgency,
                    n.summary AS news_summary,
                    al.channel,
                    al.sent_at,
                    al.message_text
                FROM alert_log al
                LEFT JOIN topics t ON t.id = al.topic_id
                LEFT JOIN news n ON n.id = al.news_id
                ORDER BY al.sent_at DESC
                LIMIT $1
                """,
                limit,
            )

    return [dict(row) for row in rows]

