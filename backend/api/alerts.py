from __future__ import annotations

from fastapi import APIRouter, Query

from database import get_pool
from models import AlertItem


router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertItem])
async def list_alerts(limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
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
