from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from database import get_pool
from models import NewsItem


router = APIRouter(prefix="/news", tags=["news"])


@router.get("", response_model=list[NewsItem])
async def list_news(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    source: str | None = None,
    urgency: str | None = None,
    search: str | None = None,
) -> list[dict]:
    offset = (page - 1) * limit
    where: list[str] = []
    params: list = []
    index = 1

    if source:
        where.append(f"source = ${index}")
        params.append(source)
        index += 1

    if urgency:
        where.append(f"urgency = ${index}")
        params.append(urgency.upper())
        index += 1

    if search:
        where.append(f"(raw_text ILIKE ${index} OR summary ILIKE ${index})")
        params.append(f"%{search}%")
        index += 1

    sql = """
    SELECT id, source, source_channel, raw_text, url, summary, category,
           urgency, sentiment, instruments_affected, matched_topics,
           llm_processed, fetched_at, published_at
    FROM news
    """

    if where:
        sql += " WHERE " + " AND ".join(where)

    sql += f" ORDER BY fetched_at DESC LIMIT ${index} OFFSET ${index + 1}"
    params.extend([limit, offset])

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    return [dict(row) for row in rows]


@router.get("/{news_id}", response_model=NewsItem)
async def get_news(news_id: int) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, source, source_channel, raw_text, url, summary, category,
                   urgency, sentiment, instruments_affected, matched_topics,
                   llm_processed, fetched_at, published_at
            FROM news
            WHERE id = $1
            """,
            news_id,
        )

    if not row:
        raise HTTPException(status_code=404, detail="News item not found")

    return dict(row)
