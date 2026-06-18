from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

from bot.telegram_notifier import send_alert_message, send_context_alert_message
from config import ALLOWED_SUMMARY_INTERVALS, runtime_state, settings
from database import get_pool, save_summary_interval, save_proxy_setting, save_fcm_token, get_fcm_preferences, update_fcm_preferences
from models import (
    LlmUsageItem,
    SummaryBatch,
    SummaryIntervalRequest,
    SummaryIntervalResponse,
    PasscodeVerifyRequest,
    ProxyToggleRequest,
    BypassVerifyRequest,
    FcmRegisterRequest,
    FcmPreferencesRequest,
)


router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SummaryIntervalResponse)
async def get_settings() -> SummaryIntervalResponse:
    current = runtime_state.get_summary_interval_seconds()
    return SummaryIntervalResponse(
        interval_seconds=current,
        allowed_values=sorted(ALLOWED_SUMMARY_INTERVALS),
        message=f"Current summary interval is {current} seconds",
    )


@router.post("/summary-interval", response_model=SummaryIntervalResponse)
async def update_summary_interval(
    payload: SummaryIntervalRequest,
    request: Request,
) -> SummaryIntervalResponse:
    if payload.interval_seconds not in ALLOWED_SUMMARY_INTERVALS:
        raise HTTPException(
            status_code=400,
            detail=f"Allowed intervals: {sorted(ALLOWED_SUMMARY_INTERVALS)}",
        )

    updated = await save_summary_interval(payload.interval_seconds)

    summarizer = getattr(request.app.state, "summarizer", None)
    if summarizer is not None:
        asyncio.create_task(_run_summary_once_now(summarizer, updated))

    return SummaryIntervalResponse(
        interval_seconds=updated,
        allowed_values=sorted(ALLOWED_SUMMARY_INTERVALS),
        message=(
            "Summary window changed to "
            f"{updated} seconds. Generating an updated batch now."
        ),
    )


@router.get("/summary-batches", response_model=list[SummaryBatch])
async def get_summary_batches(
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, window_seconds, window_start, window_end,
                 summary_text, item_count, sources, source_channels, created_at
            FROM summary_batches
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )

    return [dict(row) for row in rows]


@router.get("/llm-usage", response_model=list[LlmUsageItem])
async def get_llm_usage(
    limit: int = Query(default=200, ge=1, le=1000),
    bucket_type: str | None = Query(default=None),
) -> list[dict]:
    normalized_bucket = (bucket_type or "").strip().lower()
    if normalized_bucket and normalized_bucket not in {"minute", "day"}:
        raise HTTPException(status_code=400, detail="bucket_type must be 'minute' or 'day'")

    pool = get_pool()
    async with pool.acquire() as conn:
        if normalized_bucket:
            rows = await conn.fetch(
                """
                SELECT provider, model_name, api_key_label, bucket_type,
                       bucket_start, request_count, updated_at
                FROM llm_api_usage
                WHERE bucket_type = $1
                ORDER BY updated_at DESC
                LIMIT $2
                """,
                normalized_bucket,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT provider, model_name, api_key_label, bucket_type,
                       bucket_start, request_count, updated_at
                FROM llm_api_usage
                ORDER BY updated_at DESC
                LIMIT $1
                """,
                limit,
            )

    return [dict(row) for row in rows]


@router.post("/test-alert")
async def test_alert_delivery(alert_type: str = Query("keyword")) -> dict[str, str | bool]:
    if alert_type == "context":
        sent = await send_context_alert_message("Context alert bot test: configuration is working.")
        success_msg = "Test alert delivered to CONTEXT_CHAT_ID."
        fail_msg = "Context alert bot send failed. Check CONTEXT_BOT_TOKEN and CONTEXT_CHAT_ID."
    else:
        sent = await send_alert_message("Alert bot test: configuration is working.")
        success_msg = "Test alert delivered to ALERT_CHAT_ID."
        fail_msg = "Alert bot send failed. Check ALERT_BOT_TOKEN and ALERT_CHAT_ID."

    if sent:
        return {
            "ok": True,
            "message": success_msg,
        }
    return {
        "ok": False,
        "message": fail_msg,
    }


async def _run_summary_once_now(summarizer, window_seconds: int) -> None:
    try:
        await summarizer.run_once(window_seconds=window_seconds)
    except Exception as exc:
        print(f"Immediate summary generation failed: {exc}")


@router.post("/verify-passcode")
async def verify_passcode(payload: PasscodeVerifyRequest) -> dict:
    if payload.passcode == settings.app_passcode:
        return {"ok": True}
    raise HTTPException(status_code=401, detail="Invalid passcode")


@router.get("/proxy")
async def get_proxy_status() -> dict:
    return {
        "proxy_enabled": settings.proxy_enabled,
        "proxy_type": settings.proxy_type,
        "proxy_host": settings.proxy_host,
        "proxy_port": settings.proxy_port,
    }


@router.post("/proxy-toggle")
async def toggle_proxy(payload: ProxyToggleRequest, request: Request) -> dict:
    try:
        await save_proxy_setting(payload.enabled)
        
        listener = getattr(request.app.state, "telegram_listener", None)
        if listener is not None:
            print(f"Proxy toggle requested: enabled={payload.enabled}. Restarting Telegram listener...")
            await listener.stop()
            
        return {
            "ok": True,
            "message": f"Proxy successfully {'enabled' if payload.enabled else 'disabled'}.",
            "proxy_enabled": settings.proxy_enabled,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save proxy setting: {str(e)}"
        )


@router.post("/verify-bypass")
async def verify_bypass(payload: BypassVerifyRequest) -> dict:
    if not settings.mobile_bypass_token:
        raise HTTPException(
            status_code=401,
            detail="Mobile bypass token is not configured on the server"
        )
    if payload.token == settings.mobile_bypass_token:
        return {"ok": True}
    raise HTTPException(status_code=401, detail="Invalid bypass token")


@router.post("/register-fcm-token")
async def register_fcm_token(payload: FcmRegisterRequest) -> dict:
    try:
        await save_fcm_token(payload.fcm_token, payload.device_name)
        return {"ok": True, "message": "FCM token registered successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to register FCM token: {str(e)}"
        )


@router.get("/fcm-preferences")
async def get_fcm_prefs(token: str = Query(...)) -> dict:
    try:
        prefs = await get_fcm_preferences(token)
        if prefs is None:
            # Token not found yet or new device, return default preferences
            return {"push_keyword": True, "push_context": True}
        return prefs
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch FCM preferences: {str(e)}"
        )


@router.post("/fcm-preferences")
async def update_fcm_prefs(payload: FcmPreferencesRequest) -> dict:
    try:
        await update_fcm_preferences(
            payload.fcm_token,
            payload.push_keyword,
            payload.push_context
        )
        return {"ok": True, "message": "FCM preferences updated successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update FCM preferences: {str(e)}"
        )


