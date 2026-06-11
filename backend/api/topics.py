from __future__ import annotations

from fastapi import APIRouter, HTTPException

from config import resolve_chat_model
from database import get_pool
from models import (
    AlertProposalRequest,
    AlertProposalResponse,
    TopicCreate,
    TopicItem,
    TopicUpdate,
    ContextAlertCreate,
    ContextAlertUpdate,
    ContextAlertItem,
    ContextAlertProposalRequest,
    ContextAlertProposalResponse,
)
from processing.alert_engine import invalidate_topic_cache
from processing.llm_classifier import propose_alert_topic_from_context, propose_context_alert_description


router = APIRouter(prefix="/topics", tags=["topics"])


def _clean_keywords(keywords: list[str]) -> list[str]:
    cleaned = [item.strip() for item in keywords if item and item.strip()]
    if not cleaned:
        raise HTTPException(status_code=400, detail="At least one keyword is required")
    return cleaned


@router.get("", response_model=list[TopicItem])
async def list_topics() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, topic_name, keywords, alert_urgency_threshold,
                   active, created_at, updated_at
            FROM topics
            ORDER BY created_at DESC
            """
        )
    return [dict(row) for row in rows]


@router.post("/ai-proposal", response_model=AlertProposalResponse)
async def propose_alert_topic(payload: AlertProposalRequest) -> AlertProposalResponse:
    selected_model = resolve_chat_model(payload.model_id)

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT fetched_at, source, source_channel, raw_text, summary, urgency, sentiment
            FROM news
            ORDER BY fetched_at DESC
            LIMIT 180
            """
        )

    records = [dict(row) for row in rows]
    proposal = await propose_alert_topic_from_context(
        payload.message,
        records,
        model_provider=selected_model["provider"],
        model_name=selected_model["model"],
    )

    return AlertProposalResponse(
        topic_name=proposal["topic_name"],
        keywords=proposal["keywords"],
        alert_urgency_threshold=proposal["alert_urgency_threshold"],
        rationale=proposal.get("rationale", ""),
        context_items=len(records),
        model_id=selected_model["id"],
        model_label=selected_model["label"],
    )


@router.post("", response_model=TopicItem)
async def create_topic(payload: TopicCreate) -> dict:
    keywords = _clean_keywords(payload.keywords)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO topics(topic_name, keywords, alert_urgency_threshold, active)
            VALUES($1, $2, $3, $4)
            """,
            payload.topic_name.strip(),
            keywords,
            payload.alert_urgency_threshold,
            payload.active,
        )

        topic_id = await conn.fetchval("SELECT LAST_INSERT_ID()")
        row = await conn.fetchrow(
            """
            SELECT id, topic_name, keywords, alert_urgency_threshold,
                   active, created_at, updated_at
            FROM topics
            WHERE id = $1
            """,
            topic_id,
        )

    invalidate_topic_cache()
    return dict(row)


@router.put("/{topic_id}", response_model=TopicItem)
async def update_topic(topic_id: int, payload: TopicUpdate) -> dict:
    keywords = None
    if payload.keywords is not None:
        keywords = _clean_keywords(payload.keywords)

    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT id FROM topics WHERE id = $1", topic_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Topic not found")

        await conn.execute(
            """
            UPDATE topics
            SET topic_name = COALESCE($1, topic_name),
                keywords = COALESCE($2, keywords),
                alert_urgency_threshold = COALESCE($3, alert_urgency_threshold),
                active = COALESCE($4, active),
                updated_at = NOW()
            WHERE id = $5
            """,
            payload.topic_name.strip() if payload.topic_name else None,
            keywords,
            payload.alert_urgency_threshold,
            payload.active,
            topic_id,
        )

        row = await conn.fetchrow(
            """
            SELECT id, topic_name, keywords, alert_urgency_threshold,
                   active, created_at, updated_at
            FROM topics
            WHERE id = $1
            """,
            topic_id,
        )

    invalidate_topic_cache()
    return dict(row)


@router.delete("/{topic_id}")
async def delete_topic(topic_id: int) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM topics WHERE id = $1", topic_id)

    if result.endswith("0"):
        raise HTTPException(status_code=404, detail="Topic not found")

    invalidate_topic_cache()
    return {"ok": True, "deleted_topic_id": topic_id}


# ---------------------------------------------------------------------------
# Context Alerts Endpoints
# ---------------------------------------------------------------------------

@router.get("/context", response_model=list[ContextAlertItem])
async def list_context_alerts() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, context_description, active, created_at, updated_at
            FROM context_alerts
            ORDER BY created_at DESC
            """
        )
    return [dict(row) for row in rows]


@router.post("/context", response_model=ContextAlertItem)
async def create_context_alert(payload: ContextAlertCreate) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO context_alerts(context_description, active)
            VALUES($1, $2)
            """,
            payload.context_description.strip(),
            payload.active,
        )

        alert_id = await conn.fetchval("SELECT LAST_INSERT_ID()")
        row = await conn.fetchrow(
            """
            SELECT id, context_description, active, created_at, updated_at
            FROM context_alerts
            WHERE id = $1
            """,
            alert_id,
        )
    return dict(row)


@router.put("/context/{alert_id}", response_model=ContextAlertItem)
async def update_context_alert(alert_id: int, payload: ContextAlertUpdate) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT id FROM context_alerts WHERE id = $1", alert_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Context alert not found")

        await conn.execute(
            """
            UPDATE context_alerts
            SET context_description = COALESCE($1, context_description),
                active = COALESCE($2, active),
                updated_at = NOW()
            WHERE id = $3
            """,
            payload.context_description.strip() if payload.context_description else None,
            payload.active,
            alert_id,
        )

        row = await conn.fetchrow(
            """
            SELECT id, context_description, active, created_at, updated_at
            FROM context_alerts
            WHERE id = $1
            """,
            alert_id,
        )
    return dict(row)


@router.delete("/context/{alert_id}")
async def delete_context_alert(alert_id: int) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM context_alerts WHERE id = $1", alert_id)

    if result.endswith("0"):
        raise HTTPException(status_code=404, detail="Context alert not found")

    return {"ok": True, "deleted_context_alert_id": alert_id}


@router.post("/context/ai-proposal", response_model=ContextAlertProposalResponse)
async def propose_context_alert(payload: ContextAlertProposalRequest) -> ContextAlertProposalResponse:
    proposed = await propose_context_alert_description(payload.instruction)
    return ContextAlertProposalResponse(proposed_description=proposed)
