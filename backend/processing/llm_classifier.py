from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

try:
    from google import genai
except Exception:
    genai = None

try:
    from cerebras.cloud.sdk import Cerebras
except Exception:
    Cerebras = None

from config import settings
from database import get_pool


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_DEFAULT_TOP_P = 0.8
_gemini_import_error_logged = False
_cerebras_import_error_logged = False

# Groq state
_groq_active_key_index = 0
_groq_key_cooldown_until: dict[int, datetime] = {}
_groq_minute_usage: dict[tuple[int, datetime], int] = {}
_groq_day_usage: dict[tuple[int, date], int] = {}

# Cerebras state
_cerebras_active_key_index = 0
_cerebras_key_cooldown_until: dict[int, datetime] = {}
_cerebras_minute_usage: dict[tuple[int, datetime], int] = {}
_cerebras_hour_usage: dict[tuple[int, datetime], int] = {}
_cerebras_day_usage: dict[tuple[int, date], int] = {}
_cerebras_clients: dict[int, Any] = {}

# Gemini state
_gemini_key_cooldown_until: dict[int, datetime] = {}
_gemini_minute_usage: dict[tuple[int, str, datetime], int] = {}
_gemini_day_usage: dict[tuple[int, str, date], int] = {}

# Groq Context state
_groq_context_active_key_index = 0
_groq_context_key_cooldown_until: dict[int, datetime] = {}
_groq_context_minute_usage: dict[tuple[int, datetime], int] = {}
_groq_context_day_usage: dict[tuple[int, date], int] = {}

# Cerebras Context state
_cerebras_context_active_key_index = 0
_cerebras_context_key_cooldown_until: dict[int, datetime] = {}
_cerebras_context_minute_usage: dict[tuple[int, datetime], int] = {}
_cerebras_context_hour_usage: dict[tuple[int, datetime], int] = {}
_cerebras_context_day_usage: dict[tuple[int, date], int] = {}
_cerebras_context_clients: dict[int, Any] = {}


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------

class _GroqRequestError(Exception):
    def __init__(self, status_code: int | None, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ---------------------------------------------------------------------------
# SDK availability logging
# ---------------------------------------------------------------------------

def _log_missing_cerebras_once() -> None:
    global _cerebras_import_error_logged
    if _cerebras_import_error_logged:
        return
    _cerebras_import_error_logged = True
    print(
        "Cerebras SDK is not available. Install 'cerebras-cloud-sdk' "
        "or fallback providers will be used."
    )


def _log_missing_gemini_once() -> None:
    global _gemini_import_error_logged
    if _gemini_import_error_logged:
        return
    _gemini_import_error_logged = True
    print(
        "Gemini SDK is not available. Install 'google-genai' "
        "or fallback providers will be used."
    )


# ---------------------------------------------------------------------------
# Key normalization helpers
# ---------------------------------------------------------------------------

def _normalized_groq_keys() -> list[str]:
    normalized: list[str] = []
    for raw_key in settings.groq_api_keys:
        if not raw_key:
            continue
        key = raw_key.strip().strip('"').strip("'")
        if key:
            normalized.append(key)
    return normalized


def _normalized_gemini_keys() -> list[str]:
    normalized: list[str] = []
    for raw_key in settings.gemini_api_keys:
        if not raw_key:
            continue
        key = raw_key.strip().strip('"').strip("'")
        if key:
            normalized.append(key)
    return normalized


def _normalized_cerebras_keys() -> list[str]:
    normalized: list[str] = []
    for raw_key in settings.cerebras_api_keys:
        if not raw_key:
            continue
        key = raw_key.strip().strip('"').strip("'")
        if key:
            normalized.append(key)
    return normalized


def _normalized_groq_context_keys() -> list[str]:
    normalized: list[str] = []
    for raw_key in settings.groq_context_api_keys:
        if not raw_key:
            continue
        key = raw_key.strip().strip('"').strip("'")
        if key:
            normalized.append(key)
    return normalized


def _normalized_cerebras_context_keys() -> list[str]:
    normalized: list[str] = []
    for raw_key in settings.cerebras_context_api_keys:
        if not raw_key:
            continue
        key = raw_key.strip().strip('"').strip("'")
        if key:
            normalized.append(key)
    return normalized


# ---------------------------------------------------------------------------
# Provider availability check
# ---------------------------------------------------------------------------

def _has_llm_provider(provider_order: list[str] | None = None) -> bool:
    order = provider_order or settings.llm_provider_order
    for provider in order:
        if provider == "groq" and _normalized_groq_keys():
            return True
        if provider == "gemini" and _normalized_gemini_keys():
            return True
        if provider == "cerebras" and _normalized_cerebras_keys():
            if Cerebras is not None:
                return True
    return False


# ===================================================================
# GROQ — multi-key rotation with RPM/RPD tracking
# ===================================================================

def _cleanup_groq_state(now: datetime) -> None:
    stale_minute_cutoff = now - timedelta(minutes=3)
    for key in list(_groq_minute_usage.keys()):
        if key[1] < stale_minute_cutoff:
            _groq_minute_usage.pop(key, None)

    for key in list(_groq_day_usage.keys()):
        if key[1] < now.date():
            _groq_day_usage.pop(key, None)

    for key_index, cooldown_until in list(_groq_key_cooldown_until.items()):
        if cooldown_until <= now:
            _groq_key_cooldown_until.pop(key_index, None)


def _is_groq_key_available(key_index: int, now: datetime) -> bool:
    cooldown_until = _groq_key_cooldown_until.get(key_index)
    if cooldown_until and cooldown_until > now:
        return False

    minute_bucket = now.replace(second=0, microsecond=0)
    minute_count = _groq_minute_usage.get((key_index, minute_bucket), 0)
    if minute_count >= max(settings.groq_rpm, 1):
        return False

    day_bucket = now.date()
    day_count = _groq_day_usage.get((key_index, day_bucket), 0)
    if day_count >= max(settings.groq_rpd, 1):
        return False

    return True


def _increment_groq_usage(key_index: int, now: datetime) -> None:
    minute_bucket = now.replace(second=0, microsecond=0)
    day_bucket = now.date()

    minute_key = (key_index, minute_bucket)
    day_key = (key_index, day_bucket)

    _groq_minute_usage[minute_key] = _groq_minute_usage.get(minute_key, 0) + 1
    _groq_day_usage[day_key] = _groq_day_usage.get(day_key, 0) + 1


def _get_active_groq_key() -> tuple[int, str] | tuple[None, None]:
    global _groq_active_key_index

    keys = _normalized_groq_keys()
    if not keys:
        return None, None

    now = datetime.now(timezone.utc)
    _cleanup_groq_state(now)

    if _groq_active_key_index >= len(keys):
        _groq_active_key_index = 0

    # Try current key first
    if _is_groq_key_available(_groq_active_key_index, now):
        return _groq_active_key_index, keys[_groq_active_key_index]

    # Rotate to find available key
    for index in range(len(keys)):
        if _is_groq_key_available(index, now):
            _groq_active_key_index = index
            return index, keys[index]

    # All keys at limit — return least recently used
    soonest_index = _groq_active_key_index
    return soonest_index, keys[soonest_index]


def _mark_groq_key_exhausted(exhausted_index: int, reason: str) -> None:
    global _groq_active_key_index

    keys = _normalized_groq_keys()
    if not keys:
        return

    lowered = reason.lower()
    cooldown_seconds = settings.groq_key_cooldown_seconds
    if "invalid" in lowered or "revoked" in lowered or "deactivated" in lowered:
        cooldown_seconds = 6 * 3600

    _groq_key_cooldown_until[exhausted_index] = datetime.now(timezone.utc) + timedelta(
        seconds=cooldown_seconds
    )

    for offset in range(1, len(keys) + 1):
        next_index = (exhausted_index + offset) % len(keys)
        if next_index not in _groq_key_cooldown_until:
            _groq_active_key_index = next_index
            print(
                "Groq key switched to next configured key "
                f"(index {next_index + 1}/{len(keys)}) due to: {reason}"
            )
            return

    print(
        "All configured Groq keys are cooling down. "
        "Continuing with the least recently blocked key until quota resets."
    )


def _is_groq_key_exhausted_error(status_code: int | None, message: str) -> bool:
    lowered = (message or "").lower()
    if status_code in {401, 403}:
        return True

    key_exhaustion_signals = [
        "insufficient_quota",
        "exceeded your current quota",
        "daily limit",
        "quota exceeded",
        "api key is invalid",
        "invalid api key",
        "deactivated",
        "revoked",
    ]
    return any(signal in lowered for signal in key_exhaustion_signals)


def _is_groq_retryable_error(status_code: int | None, message: str) -> bool:
    if status_code in {500, 502, 503, 504}:
        return True

    lowered = (message or "").lower()
    if status_code == 429:
        return not _is_groq_key_exhausted_error(status_code, lowered)

    transient_signals = [
        "temporarily unavailable",
        "timeout",
        "connection reset",
        "try again",
    ]
    return any(signal in lowered for signal in transient_signals)


async def _create_groq_completion_async(
    messages: list[dict[str, str]],
    *,
    max_completion_tokens: int,
    temperature: float,
    model_name: str | None = None,
) -> dict[str, Any] | None:
    keys = _normalized_groq_keys()
    if not keys:
        return None

    max_attempts = max(3, len(keys) + 1)
    for attempt in range(1, max_attempts + 1):
        key_index, api_key = _get_active_groq_key()
        if api_key is None or key_index is None:
            return None

        url = settings.groq_base_url.rstrip("/") + "/chat/completions"
        resolved_model_name = model_name or settings.groq_model
        payload = {
            "model": resolved_model_name,
            "messages": messages,
            "temperature": temperature,
            "top_p": _DEFAULT_TOP_P,
            "max_tokens": max_completion_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code >= 400:
                detail = response.text
                try:
                    body = response.json()
                    error = body.get("error", {}) if isinstance(body, dict) else {}
                    if isinstance(error, dict):
                        detail = str(error.get("message") or detail)
                except Exception:
                    pass

                raise _GroqRequestError(response.status_code, detail)

            body = response.json()
            if not isinstance(body, dict):
                raise _GroqRequestError(response.status_code, "Unexpected Groq response shape")

            usage_time = datetime.now(timezone.utc)
            _increment_groq_usage(key_index, usage_time)
            await _record_llm_usage(
                provider="groq",
                model_name=resolved_model_name,
                api_key_label=f"groq-key-{key_index + 1}",
                now=usage_time,
            )
            return body

        except _GroqRequestError as exc:
            if _is_groq_key_exhausted_error(exc.status_code, exc.message):
                _mark_groq_key_exhausted(key_index, exc.message)
                continue

            if attempt < max_attempts and _is_groq_retryable_error(exc.status_code, exc.message):
                backoff_seconds = min(8, 2 ** (attempt - 1))
                print(
                    f"Groq transient error retry {attempt}/{max_attempts} after "
                    f"{backoff_seconds}s: {exc.message}"
                )
                await asyncio.sleep(backoff_seconds)
                continue

            raise

        except Exception as exc:
            message = str(exc)
            if attempt < max_attempts and _is_groq_retryable_error(None, message):
                backoff_seconds = min(8, 2 ** (attempt - 1))
                print(
                    f"Groq transient error retry {attempt}/{max_attempts} after "
                    f"{backoff_seconds}s: {message}"
                )
                await asyncio.sleep(backoff_seconds)
                continue
            raise

    return None


# ===================================================================
# CEREBRAS — multi-key rotation with RPM/RPH/RPD tracking
# ===================================================================

def _get_cerebras_client(key_index: int) -> Any | None:
    keys = _normalized_cerebras_keys()
    if not keys or key_index >= len(keys):
        return None
    if Cerebras is None:
        _log_missing_cerebras_once()
        return None
    if key_index not in _cerebras_clients:
        _cerebras_clients[key_index] = Cerebras(api_key=keys[key_index])
    return _cerebras_clients[key_index]


def _cleanup_cerebras_state(now: datetime) -> None:
    stale_minute_cutoff = now - timedelta(minutes=3)
    for key in list(_cerebras_minute_usage.keys()):
        if key[1] < stale_minute_cutoff:
            _cerebras_minute_usage.pop(key, None)

    stale_hour_cutoff = now - timedelta(hours=2)
    for key in list(_cerebras_hour_usage.keys()):
        if key[1] < stale_hour_cutoff:
            _cerebras_hour_usage.pop(key, None)

    for key in list(_cerebras_day_usage.keys()):
        if key[1] < now.date():
            _cerebras_day_usage.pop(key, None)

    for key_index, cooldown_until in list(_cerebras_key_cooldown_until.items()):
        if cooldown_until <= now:
            _cerebras_key_cooldown_until.pop(key_index, None)


def _is_cerebras_key_available(key_index: int, now: datetime) -> bool:
    cooldown_until = _cerebras_key_cooldown_until.get(key_index)
    if cooldown_until and cooldown_until > now:
        return False

    minute_bucket = now.replace(second=0, microsecond=0)
    minute_count = _cerebras_minute_usage.get((key_index, minute_bucket), 0)
    if minute_count >= max(settings.cerebras_rpm, 1):
        return False

    hour_bucket = now.replace(minute=0, second=0, microsecond=0)
    hour_count = _cerebras_hour_usage.get((key_index, hour_bucket), 0)
    if hour_count >= max(settings.cerebras_rph, 1):
        return False

    day_bucket = now.date()
    day_count = _cerebras_day_usage.get((key_index, day_bucket), 0)
    if day_count >= max(settings.cerebras_rpd, 1):
        return False

    return True


def _increment_cerebras_usage(key_index: int, now: datetime) -> None:
    minute_bucket = now.replace(second=0, microsecond=0)
    hour_bucket = now.replace(minute=0, second=0, microsecond=0)
    day_bucket = now.date()

    minute_key = (key_index, minute_bucket)
    hour_key = (key_index, hour_bucket)
    day_key = (key_index, day_bucket)

    _cerebras_minute_usage[minute_key] = _cerebras_minute_usage.get(minute_key, 0) + 1
    _cerebras_hour_usage[hour_key] = _cerebras_hour_usage.get(hour_key, 0) + 1
    _cerebras_day_usage[day_key] = _cerebras_day_usage.get(day_key, 0) + 1


def _get_active_cerebras_key() -> tuple[int, str] | tuple[None, None]:
    global _cerebras_active_key_index

    keys = _normalized_cerebras_keys()
    if not keys:
        return None, None

    now = datetime.now(timezone.utc)
    _cleanup_cerebras_state(now)

    if _cerebras_active_key_index >= len(keys):
        _cerebras_active_key_index = 0

    if _is_cerebras_key_available(_cerebras_active_key_index, now):
        return _cerebras_active_key_index, keys[_cerebras_active_key_index]

    for index in range(len(keys)):
        if _is_cerebras_key_available(index, now):
            _cerebras_active_key_index = index
            return index, keys[index]

    soonest_index = _cerebras_active_key_index
    return soonest_index, keys[soonest_index]


def _mark_cerebras_key_exhausted(exhausted_index: int, reason: str) -> None:
    global _cerebras_active_key_index

    keys = _normalized_cerebras_keys()
    if not keys:
        return

    lowered = reason.lower()
    cooldown_seconds = settings.cerebras_key_cooldown_seconds
    if "invalid" in lowered or "revoked" in lowered or "deactivated" in lowered:
        cooldown_seconds = 6 * 3600

    _cerebras_key_cooldown_until[exhausted_index] = datetime.now(timezone.utc) + timedelta(
        seconds=cooldown_seconds
    )

    # Invalidate cached client for this key
    _cerebras_clients.pop(exhausted_index, None)

    for offset in range(1, len(keys) + 1):
        next_index = (exhausted_index + offset) % len(keys)
        if next_index not in _cerebras_key_cooldown_until:
            _cerebras_active_key_index = next_index
            print(
                "Cerebras key switched to next configured key "
                f"(index {next_index + 1}/{len(keys)}) due to: {reason}"
            )
            return

    print(
        "All configured Cerebras keys are cooling down. "
        "Continuing with the least recently blocked key until quota resets."
    )


def _is_retryable_cerebras_error(exc: Exception) -> bool:
    text = str(exc).lower()
    retry_signals = [
        "429",
        "queue_exceeded",
        "high traffic",
        "rate limit",
        "temporarily unavailable",
        "timeout",
    ]
    return any(signal in text for signal in retry_signals)


def _is_cerebras_key_exhausted_error(exc: Exception) -> bool:
    text = str(exc).lower()
    exhaustion_signals = [
        "invalid api key",
        "api key is invalid",
        "deactivated",
        "revoked",
        "unauthorized",
        "403",
        "401",
        "quota",
        "exceeded",
    ]
    return any(signal in text for signal in exhaustion_signals)


def _create_cerebras_completion_sync(
    messages: list[dict[str, str]],
    *,
    max_completion_tokens: int,
    temperature: float,
    model_name: str | None = None,
    key_index: int = 0,
) -> Any | None:
    client = _get_cerebras_client(key_index)
    if client is None:
        return None

    return client.chat.completions.create(
        messages=messages,
        model=model_name or settings.cerebras_model,
        max_completion_tokens=max_completion_tokens,
        temperature=temperature,
        top_p=_DEFAULT_TOP_P,
        stream=False,
    )


async def _create_cerebras_completion_async(
    messages: list[dict[str, str]],
    *,
    max_completion_tokens: int,
    temperature: float,
    model_name: str | None = None,
) -> dict[str, Any] | None:
    keys = _normalized_cerebras_keys()
    if not keys:
        return None
    if Cerebras is None:
        _log_missing_cerebras_once()
        return None

    max_attempts = max(3, len(keys) + 1)
    for attempt in range(1, max_attempts + 1):
        key_index, api_key = _get_active_cerebras_key()
        if api_key is None or key_index is None:
            return None

        try:
            response = await asyncio.to_thread(
                _create_cerebras_completion_sync,
                messages,
                max_completion_tokens=max_completion_tokens,
                temperature=temperature,
                model_name=model_name,
                key_index=key_index,
            )
            if response is None:
                continue

            usage_time = datetime.now(timezone.utc)
            _increment_cerebras_usage(key_index, usage_time)
            await _record_llm_usage(
                provider="cerebras",
                model_name=model_name or settings.cerebras_model,
                api_key_label=f"cerebras-key-{key_index + 1}",
                now=usage_time,
            )

            # Normalize response to dict format
            text = _extract_response_text(response)
            if not text:
                continue

            return {
                "choices": [
                    {
                        "message": {
                            "content": text,
                        }
                    }
                ]
            }

        except Exception as exc:
            if _is_cerebras_key_exhausted_error(exc):
                _mark_cerebras_key_exhausted(key_index, str(exc))
                continue

            if attempt < max_attempts and _is_retryable_cerebras_error(exc):
                backoff_seconds = min(8, 2 ** (attempt - 1))
                print(
                    f"Cerebras request retry {attempt}/{max_attempts} after "
                    f"{backoff_seconds}s due to transient error: {exc}"
                )
                await asyncio.sleep(backoff_seconds)
                continue

            print(f"Cerebras completion error: {exc}")
            break

    return None


# ===================================================================
# GEMINI — multi-key rotation with RPM/daily tracking
# ===================================================================

def _gemini_models_with_limits() -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []

    primary_model = (settings.gemini_primary_model or "").strip()
    fallback_model = (settings.gemini_fallback_model or "").strip()

    if primary_model:
        items.append((primary_model, max(settings.gemini_primary_rpm, 1)))

    if fallback_model and fallback_model != primary_model:
        items.append((fallback_model, max(settings.gemini_fallback_rpm, 1)))

    return items


def _cleanup_gemini_state(now: datetime) -> None:
    stale_minute_cutoff = now - timedelta(minutes=3)
    for key in list(_gemini_minute_usage.keys()):
        if key[2] < stale_minute_cutoff:
            _gemini_minute_usage.pop(key, None)

    for key in list(_gemini_day_usage.keys()):
        if key[2] < now.date():
            _gemini_day_usage.pop(key, None)

    for key_index, cooldown_until in list(_gemini_key_cooldown_until.items()):
        if cooldown_until <= now:
            _gemini_key_cooldown_until.pop(key_index, None)


def _gemini_key_label(key_index: int) -> str:
    return f"gemini-key-{key_index + 1}"


def _is_gemini_key_available(
    key_index: int,
    model_name: str,
    rpm_limit: int,
    now: datetime,
) -> bool:
    cooldown_until = _gemini_key_cooldown_until.get(key_index)
    if cooldown_until and cooldown_until > now:
        return False

    minute_bucket = now.replace(second=0, microsecond=0)
    minute_count = _gemini_minute_usage.get((key_index, model_name, minute_bucket), 0)
    if minute_count >= max(rpm_limit, 1):
        return False

    day_bucket = now.date()
    day_count = _gemini_day_usage.get((key_index, model_name, day_bucket), 0)
    if day_count >= max(settings.gemini_requests_per_day, 1):
        return False

    return True


def _mark_gemini_key_cooldown(key_index: int, reason: str) -> None:
    cooldown_seconds = max(settings.gemini_key_cooldown_seconds, 20)
    lowered = reason.lower()
    if "daily" in lowered or "quota" in lowered:
        cooldown_seconds = max(cooldown_seconds, 3600)

    _gemini_key_cooldown_until[key_index] = datetime.now(timezone.utc) + timedelta(
        seconds=cooldown_seconds
    )
    print(
        f"Gemini key {key_index + 1} cooling down for {cooldown_seconds}s due to: {reason}"
    )


def _is_gemini_key_exhausted_error(message: str) -> bool:
    lowered = message.lower()
    signals = [
        "api key not valid",
        "permission denied",
        "quota",
        "daily limit",
        "exceeded",
        "resource exhausted",
        "429",
    ]
    return any(signal in lowered for signal in signals)


def _is_gemini_retryable_error(message: str) -> bool:
    lowered = message.lower()
    transient_signals = [
        "temporarily unavailable",
        "timeout",
        "deadline exceeded",
        "internal",
        "try again",
        "connection",
    ]
    return any(signal in lowered for signal in transient_signals)


def _increment_gemini_usage(key_index: int, model_name: str, now: datetime) -> None:
    minute_bucket = now.replace(second=0, microsecond=0)
    day_bucket = now.date()

    minute_key = (key_index, model_name, minute_bucket)
    day_key = (key_index, model_name, day_bucket)

    _gemini_minute_usage[minute_key] = _gemini_minute_usage.get(minute_key, 0) + 1
    _gemini_day_usage[day_key] = _gemini_day_usage.get(day_key, 0) + 1


def _extract_gemini_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    candidates = getattr(response, "candidates", None)
    if isinstance(candidates, list):
        parts: list[str] = []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            response_parts = getattr(content, "parts", None)
            if not response_parts:
                continue
            for part in response_parts:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text.strip():
                    parts.append(part_text.strip())
        if parts:
            return "\n".join(parts).strip()

    return ""


def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
    sections: list[str] = []
    for message in messages:
        role = str(message.get("role", "user")).upper()
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        sections.append(f"{role}:\n{content}")
    return "\n\n".join(sections).strip()


def _create_gemini_completion_sync(
    api_key: str,
    model_name: str,
    prompt: str,
    *,
    max_completion_tokens: int,
    temperature: float,
) -> str:
    if genai is None:
        return ""

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config={
            "temperature": temperature,
            "max_output_tokens": max_completion_tokens,
            "top_p": _DEFAULT_TOP_P,
        },
    )
    return _extract_gemini_text(response)


async def _create_gemini_completion_async(
    messages: list[dict[str, str]],
    *,
    max_completion_tokens: int,
    temperature: float,
    model_name: str | None = None,
) -> dict[str, Any] | None:
    keys = _normalized_gemini_keys()
    if not keys:
        return None

    if genai is None:
        _log_missing_gemini_once()
        return None

    prompt = _messages_to_prompt(messages)
    if not prompt:
        return None

    if model_name:
        model_limits = [(model_name, max(settings.gemini_primary_rpm, 1))]
    else:
        model_limits = _gemini_models_with_limits()
    if not model_limits:
        return None

    for model_name, rpm_limit in model_limits:
        for key_index, api_key in enumerate(keys):
            now = datetime.now(timezone.utc)
            _cleanup_gemini_state(now)

            if not _is_gemini_key_available(key_index, model_name, rpm_limit, now):
                continue

            try:
                text = await asyncio.to_thread(
                    _create_gemini_completion_sync,
                    api_key,
                    model_name,
                    prompt,
                    max_completion_tokens=max_completion_tokens,
                    temperature=temperature,
                )
                if not text:
                    continue

                usage_time = datetime.now(timezone.utc)
                _increment_gemini_usage(key_index, model_name, usage_time)
                await _record_llm_usage(
                    provider="gemini",
                    model_name=model_name,
                    api_key_label=_gemini_key_label(key_index),
                    now=usage_time,
                )

                return {
                    "choices": [
                        {
                            "message": {
                                "content": text,
                            }
                        }
                    ]
                }

            except Exception as exc:
                message = str(exc)
                if _is_gemini_key_exhausted_error(message):
                    _mark_gemini_key_cooldown(key_index, message)
                    continue

                if _is_gemini_retryable_error(message):
                    await asyncio.sleep(1.0)
                    continue

                print(f"Gemini request error ({model_name}, key {key_index + 1}): {message}")

    return None


# ===================================================================
# Unified completion dispatcher
# ===================================================================

def _extract_text_content(content: Any) -> str:
    if content is None:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(parts).strip()

    return str(content)


def _extract_response_text(response: Any) -> str:
    if isinstance(response, str):
        return response.strip()

    if isinstance(response, dict):
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if isinstance(message, dict):
            return _extract_text_content(message.get("content")).strip()
        return ""

    direct_text = getattr(response, "text", None)
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text.strip()

    try:
        choice = response.choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            return ""
        return _extract_text_content(getattr(message, "content", None)).strip()
    except Exception:
        return ""


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None

    return None


async def _record_llm_usage(
    *,
    provider: str,
    model_name: str,
    api_key_label: str,
    now: datetime,
) -> None:
    try:
        pool = get_pool()
    except RuntimeError:
        return

    minute_bucket = now.replace(second=0, microsecond=0)
    day_bucket = datetime(
        now.year,
        now.month,
        now.day,
        tzinfo=timezone.utc,
    )

    async with pool.acquire() as conn:
        for bucket_type, bucket_start in (("minute", minute_bucket), ("day", day_bucket)):
            await conn.execute(
                """
                INSERT INTO llm_api_usage(
                    provider,
                    model_name,
                    api_key_label,
                    bucket_type,
                    bucket_start,
                    request_count
                )
                VALUES($1, $2, $3, $4, $5, 1) AS incoming
                ON DUPLICATE KEY UPDATE
                    request_count = llm_api_usage.request_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                provider,
                model_name,
                api_key_label,
                bucket_type,
                bucket_start,
            )


async def _create_completion_async(
    messages: list[dict[str, str]],
    *,
    max_completion_tokens: int,
    temperature: float,
    provider_order: list[str] | None = None,
    model_provider: str | None = None,
    model_name: str | None = None,
) -> Any | None:
    if model_provider:
        ordered_providers = [model_provider.lower()]
    else:
        ordered_providers = provider_order or settings.llm_provider_order

    for provider in ordered_providers:
        if provider == "groq":
            if not _normalized_groq_keys():
                continue
            try:
                groq_response = await _create_groq_completion_async(
                    messages,
                    max_completion_tokens=max_completion_tokens,
                    temperature=temperature,
                    model_name=model_name if model_provider == "groq" else None,
                )
                if groq_response is not None:
                    return groq_response
            except Exception as exc:
                print(f"Groq completion error: {exc}")
                continue

        if provider == "cerebras":
            if not _normalized_cerebras_keys() or Cerebras is None:
                continue
            try:
                cerebras_response = await _create_cerebras_completion_async(
                    messages,
                    max_completion_tokens=max_completion_tokens,
                    temperature=temperature,
                    model_name=model_name if model_provider == "cerebras" else None,
                )
                if cerebras_response is not None:
                    return cerebras_response
            except Exception as exc:
                print(f"Cerebras completion error: {exc}")
                continue

        if provider == "gemini":
            if not _normalized_gemini_keys():
                continue
            try:
                gemini_response = await _create_gemini_completion_async(
                    messages,
                    max_completion_tokens=max_completion_tokens,
                    temperature=temperature,
                    model_name=model_name if model_provider == "gemini" else None,
                )
                if gemini_response is not None:
                    return gemini_response
            except Exception as exc:
                print(f"Gemini completion error: {exc}")
                continue

    return None


# ===================================================================
# Chat cross-provider fallback wrapper
# ===================================================================

async def _create_completion_with_fallback(
    messages: list[dict[str, str]],
    *,
    max_completion_tokens: int,
    temperature: float,
    model_provider: str | None = None,
    model_name: str | None = None,
) -> Any | None:
    """Try the selected provider first, then fall back through the full chain.

    This is used by chat and alert proposal endpoints where a specific model
    is selected by the user but we don't want to give up if that provider fails.
    Fallback order: groq -> cerebras -> gemini (Gemini always last).
    """
    # First try the selected provider
    if model_provider:
        try:
            response = await _create_completion_async(
                messages,
                max_completion_tokens=max_completion_tokens,
                temperature=temperature,
                model_provider=model_provider,
                model_name=model_name,
            )
            if response is not None:
                return response
        except Exception as exc:
            print(f"Primary provider ({model_provider}) failed: {exc}")

    # Fall back through the full chain, skipping the already-tried provider
    fallback_order = [p for p in settings.llm_provider_order if p != model_provider]
    if fallback_order:
        try:
            response = await _create_completion_async(
                messages,
                max_completion_tokens=max_completion_tokens,
                temperature=temperature,
                provider_order=fallback_order,
            )
            if response is not None:
                return response
        except Exception as exc:
            print(f"Fallback chain failed: {exc}")

    return None


# ===================================================================
# Instrument extraction and text utilities
# ===================================================================

def _extract_instruments(text: str) -> list[str]:
    mapping = {
        "BTC": ["btc", "bitcoin"],
        "ETH": ["eth", "ethereum"],
        "XAUUSD": ["xauusd", "gold"],
        "WTI": ["oil", "wti", "brent"],
        "USD": ["dollar", "usd", "dxy"],
        "SPX": ["spx", "s&p", "sp500", "s&p 500"],
        "NIFTY": ["nifty", "sensex"],
    }
    lowered = text.lower()
    matched = []
    for instrument, keys in mapping.items():
        if any(key in lowered for key in keys):
            matched.append(instrument)
    return matched


def _strip_visual_ellipsis(text: str) -> str:
    # Remove repeated ellipsis patterns so output stays clean in the UI/bot.
    cleaned = re.sub(r"\.{3,}|…+", " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _summary_line_from_row(row: dict[str, Any]) -> str:
    value = row.get("summary") or row.get("raw_text") or ""
    return _strip_visual_ellipsis(str(value)) if value else ""


def _build_fallback_window_summary(news_rows: list[dict[str, Any]]) -> str:
    lines = [_summary_line_from_row(row) for row in news_rows[-6:]]
    lines = [line for line in lines if line]
    if not lines:
        return "No major updates in this time window."
    numbered = [f"{index}. {line}" for index, line in enumerate(lines, start=1)]
    return "Latest updates:\n" + "\n".join(numbered)


# ===================================================================
# Alert topic proposal
# ===================================================================

_ALERT_KEYWORD_STOPWORDS = {
    "alert",
    "about",
    "and",
    "alerts",
    "allotron",
    "based",
    "bot",
    "chatbot",
    "context",
    "create",
    "draft",
    "for",
    "from",
    "high",
    "keep",
    "keyword",
    "keywords",
    "low",
    "medium",
    "minimum",
    "need",
    "notification",
    "notifications",
    "okay",
    "on",
    "or",
    "set",
    "setup",
    "should",
    "the",
    "this",
    "topic",
    "to",
    "urgency",
    "with",
}


def _normalize_alert_urgency(value: Any, source_text: str = "") -> str:
    candidate = str(value or "").strip().upper()
    if candidate in {"HIGH", "MEDIUM", "LOW"}:
        return candidate

    lowered = source_text.lower()
    if re.search(r"\b(high|urgent|breaking|critical|red alert)\b", lowered):
        return "HIGH"
    if re.search(r"\b(low|watch only|FYI|fyi)\b", lowered):
        return "LOW"
    return "MEDIUM"


def _clean_alert_keyword(value: Any) -> str:
    keyword = re.sub(r"\s+", " ", str(value or "")).strip(" \t\n\r,.;:-")
    if not keyword:
        return ""

    normalized = keyword.lower().strip("#$")
    if len(normalized) < 2 or normalized in _ALERT_KEYWORD_STOPWORDS:
        return ""

    # Keep phrase keywords short enough for reliable regex matching later.
    return keyword[:80]


def _keyword_candidates_from_text(text: str, max_keywords: int = 10) -> list[str]:
    candidates: list[str] = []

    quoted_phrases = re.findall(r"[\"']([^\"']{2,80})[\"']", text or "")
    candidates.extend(quoted_phrases)

    cleaned = re.sub(r"https?://\S+", " ", text or "")
    tokens = re.findall(r"[$#]?[A-Za-z][A-Za-z0-9&.-]{2,}", cleaned)
    candidates.extend(tokens)

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        keyword = _clean_alert_keyword(candidate)
        key = keyword.lower()
        if not keyword or key in seen:
            continue
        seen.add(key)
        unique.append(keyword)
        if len(unique) >= max_keywords:
            break

    return unique


def _fallback_alert_proposal(instruction: str) -> dict[str, Any]:
    keywords = _keyword_candidates_from_text(instruction, max_keywords=8)
    if not keywords:
        keywords = ["breaking news"]

    topic_words = keywords[:4]
    topic_name = " ".join(topic_words).strip()
    if topic_name:
        topic_name = topic_name[:80].title()
    else:
        topic_name = "Custom News Alert"

    return {
        "topic_name": topic_name,
        "keywords": keywords,
        "alert_urgency_threshold": _normalize_alert_urgency(None, instruction),
        "rationale": "Drafted locally from the alert request because an LLM response was unavailable.",
    }


def _normalize_alert_proposal(
    proposal: dict[str, Any] | None,
    instruction: str,
) -> dict[str, Any]:
    fallback = _fallback_alert_proposal(instruction)
    if not isinstance(proposal, dict):
        return fallback

    topic_name = re.sub(r"\s+", " ", str(proposal.get("topic_name") or "")).strip()
    if len(topic_name) < 2:
        topic_name = fallback["topic_name"]
    topic_name = topic_name[:200]

    raw_keywords = proposal.get("keywords")
    if isinstance(raw_keywords, str):
        keyword_inputs: list[Any] = re.split(r"[,\n]", raw_keywords)
    elif isinstance(raw_keywords, list):
        keyword_inputs = raw_keywords
    else:
        keyword_inputs = []

    keywords: list[str] = []
    seen: set[str] = set()
    for raw_keyword in keyword_inputs:
        keyword = _clean_alert_keyword(raw_keyword)
        key = keyword.lower()
        if not keyword or key in seen:
            continue
        seen.add(key)
        keywords.append(keyword)
        if len(keywords) >= 10:
            break

    if not keywords:
        keywords = fallback["keywords"]

    rationale = re.sub(r"\s+", " ", str(proposal.get("rationale") or "")).strip()
    if not rationale:
        rationale = fallback["rationale"]

    return {
        "topic_name": topic_name,
        "keywords": keywords,
        "alert_urgency_threshold": _normalize_alert_urgency(
            proposal.get("alert_urgency_threshold"),
            instruction,
        ),
        "rationale": rationale[:280],
    }


async def propose_alert_topic_from_context(
    instruction: str,
    news_rows: list[dict[str, Any]],
    *,
    model_provider: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Alert builder: GLM 4.7 first, GPT-OSS second, Gemini last."""
    fallback = _fallback_alert_proposal(instruction)

    # Default alert builder order: cerebras -> groq -> gemini
    if not model_provider:
        model_provider = "cerebras"
        model_name = settings.cerebras_chat_model

    provider_order = [model_provider] if model_provider else settings.llm_provider_order
    if not _has_llm_provider(provider_order) and not _has_llm_provider():
        return fallback

    context_lines: list[str] = []
    for row in news_rows[:120]:
        fetched_at = row.get("fetched_at")
        if isinstance(fetched_at, datetime):
            ts = fetched_at.isoformat()
        else:
            ts = str(fetched_at)
        text = _strip_visual_ellipsis(str(row.get("raw_text") or row.get("summary") or ""))
        if text:
            context_lines.append(
                f"[{ts}] {row.get('source_channel') or row.get('source')}: {text[:500]}"
            )

    system_prompt = """You draft backend alert-topic configurations for a Telegram news monitor.
Return ONLY JSON with fields:
topic_name, keywords, alert_urgency_threshold, rationale.
Allowed alert_urgency_threshold values: HIGH, MEDIUM, LOW.
Choose 4 to 10 short keywords or phrases that are likely to appear in incoming news messages.
Use the user's request first, then use the supplied recent context to add useful aliases or related terms.
Do not save the topic. Do not ask a follow-up question. The UI will ask for confirmation.
"""

    user_prompt = (
        f"User alert request:\n{instruction}\n\n"
        "Recent stored news context:\n"
        + ("\n".join(context_lines) if context_lines else "No stored news context available.")
    )

    try:
        response = await _create_completion_with_fallback(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt[:14000]},
            ],
            temperature=0.1,
            max_completion_tokens=700,
            model_provider=model_provider,
            model_name=model_name,
        )
        if response is None:
            return fallback

        content = _extract_response_text(response)
        result = _extract_json_payload(content)
        return _normalize_alert_proposal(result, instruction)
    except Exception as exc:
        print(f"LLM alert proposal error: {exc}")
        return fallback


# ===================================================================
# News classification
# ===================================================================

def _strip_source_prefixes(text: str) -> str:
    if not text:
        return ""

    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\s*[-*]\s*(telegram|twitter|source)\s*:\s*", "- ", line, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\s*sources?\s*:\s*.*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        if cleaned:
            lines.append(cleaned)

    return "\n".join(lines).strip()


def _heuristic_classification(raw_text: str) -> dict[str, Any]:
    lowered = raw_text.lower()

    category = "other"
    if any(token in lowered for token in ["fed", "powell", "rate", "ecb", "boe"]):
        category = "central_bank"
    elif any(token in lowered for token in ["inflation", "cpi", "ppi"]):
        category = "inflation"
    elif any(token in lowered for token in ["war", "missile", "attack", "sanction"]):
        category = "geopolitics"
    elif any(token in lowered for token in ["earnings", "guidance", "revenue"]):
        category = "earnings"
    elif any(token in lowered for token in ["bitcoin", "ethereum", "crypto", "etf"]):
        category = "crypto"

    urgency = "LOW"
    if any(token in lowered for token in ["breaking", "urgent", "rate decision", "war", "halt"]):
        urgency = "HIGH"
    elif any(token in lowered for token in ["expects", "outlook", "watch", "update"]):
        urgency = "MEDIUM"

    sentiment = "neutral"
    if any(token in lowered for token in ["surge", "beat", "rally", "gain", "up"]):
        sentiment = "bullish"
    elif any(token in lowered for token in ["drop", "miss", "selloff", "down", "risk-off"]):
        sentiment = "bearish"

    clean_summary = _strip_visual_ellipsis(re.sub(r"\s+", " ", raw_text).strip())

    return {
        "summary": clean_summary,
        "category": category,
        "urgency": urgency,
        "sentiment": sentiment,
        "instruments_affected": _extract_instruments(raw_text),
    }


def classify_news_heuristic(raw_text: str) -> dict[str, Any]:
    return _heuristic_classification(raw_text)


async def classify_news(raw_text: str) -> dict[str, Any]:
    """Classification: GPT-OSS first, GLM 4.7 second, Gemini last."""
    system_prompt = """You are a financial news classifier.
Return ONLY JSON with fields:
summary, category, urgency, sentiment, instruments_affected.
Allowed urgency: HIGH, MEDIUM, LOW.
Allowed sentiment: bullish, bearish, neutral.
Return full summary text without truncating content.
"""

    try:
        response = await _create_completion_async(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_text[:2500]},
            ],
            temperature=0.1,
            max_completion_tokens=700,
            provider_order=settings.classification_provider_order,
        )
        if response is None:
            return _heuristic_classification(raw_text)

        content = _extract_response_text(response)
        result = _extract_json_payload(content)
        if result is None:
            return _heuristic_classification(raw_text)

        merged = _heuristic_classification(raw_text)
        merged.update({
            "summary": result.get("summary") or merged["summary"],
            "category": result.get("category") or merged["category"],
            "urgency": (result.get("urgency") or merged["urgency"]).upper(),
            "sentiment": (result.get("sentiment") or merged["sentiment"]).lower(),
            "instruments_affected": result.get("instruments_affected")
            or merged["instruments_affected"],
        })

        if merged["urgency"] not in {"HIGH", "MEDIUM", "LOW"}:
            merged["urgency"] = "LOW"
        if merged["sentiment"] not in {"bullish", "bearish", "neutral"}:
            merged["sentiment"] = "neutral"

        return merged
    except Exception as exc:
        print(f"LLM classify error: {exc}")
        return _heuristic_classification(raw_text)


# ===================================================================
# Summarization
# ===================================================================

async def summarize_news_window(news_rows: list[dict[str, Any]], window_seconds: int) -> str:
    """Summaries: GPT-OSS first, GLM 4.7 second, Gemini last."""
    if not news_rows:
        return "No major updates in this time window."

    if settings.fast_summary_mode or not _has_llm_provider(settings.summary_provider_order):
        return _build_fallback_window_summary(news_rows)

    digest_input = []
    for row in news_rows[-30:]:
        fetched_at = row.get("fetched_at")
        if isinstance(fetched_at, datetime):
            ts = fetched_at.isoformat()
        else:
            ts = str(fetched_at)
        digest_input.append(
            f"[{ts}] {row.get('source')} / {row.get('source_channel')}: {row.get('raw_text')}"
        )

    system_prompt = (
        "You summarize a stream of market news. Return plain text only. "
        "Return a numbered list using 1., 2., 3. and never use asterisks or bullet glyphs. "
        "Include up to 6 concise points and mention notable risks and instruments if present."
    )

    user_prompt = (
        f"Window: {window_seconds} seconds.\n"
        f"Items count: {len(news_rows)}\n"
        "News stream:\n"
        + "\n".join(digest_input)
    )

    try:
        response = await _create_completion_async(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt[:12000]},
            ],
            temperature=0.2,
            max_completion_tokens=1200,
            provider_order=settings.summary_provider_order,
        )
        if response is None:
            return _build_fallback_window_summary(news_rows)

        output = _extract_response_text(response)
        output = _strip_source_prefixes(_strip_visual_ellipsis(output.strip()))
        return output if output else "No major updates in this window."
    except Exception as exc:
        print(f"LLM summarize error: {exc}")
        return _build_fallback_window_summary(news_rows)


# ===================================================================
# Chat — answer with news context + cross-provider fallback
# ===================================================================

async def answer_with_news_context(
    question: str,
    news_rows: list[dict[str, Any]],
    *,
    time_bucket_digest: str = "",
    model_provider: str | None = None,
    model_name: str | None = None,
) -> str:
    """Chat: user picks model, fallback chain if selected provider fails."""
    if not news_rows:
        return "I could not find matching news in the selected time range."

    context_lines = []
    for row in news_rows[:350]:
        context_lines.append(
            f"[{row.get('fetched_at')}] {row.get('source')} / {row.get('source_channel')}: {row.get('raw_text')}"
        )

    # Check if ANY provider is available (not just the selected one)
    if not _has_llm_provider():
        return (
            "Local fallback answer: I found "
            f"{len(news_rows)} relevant updates. Latest item: "
            f"{news_rows[0].get('summary') or news_rows[0].get('raw_text', '')}"
        )

    system_prompt = (
        "You are a market-news assistant. Answer only from supplied database context. "
        "If uncertain, say so. Prefer concise, factual output. "
        "When timeframe buckets are provided, use them to reason about month/week/day trends."
    )
    user_prompt = (
        f"Question: {question}\n\n"
        + (f"Timeframe buckets:\n{time_bucket_digest}\n\n" if time_bucket_digest else "")
        + "Database context:\n"
        + "\n".join(context_lines)
    )

    try:
        # Use fallback wrapper: tries selected provider, then full chain
        response = await _create_completion_with_fallback(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt[:14000]},
            ],
            temperature=0.2,
            max_completion_tokens=1200,
            model_provider=model_provider,
            model_name=model_name,
        )
        if response is None:
            return (
                "I found related records, but no LLM provider is available. "
                "Please check Gemini, Groq, or Cerebras API configuration."
            )

        answer = _extract_response_text(response)
        answer = answer.strip()
        return answer or "I could not generate an answer from the available context."
    except Exception as exc:
        print(f"LLM chat error: {exc}")
        return (
            "I found related records, but the AI response failed right now. "
            "Please try again in a moment."
        )


# ===================================================================
# Context Alerts Pipeline LLM Handlers
# ===================================================================

def _cleanup_groq_context_state(now: datetime) -> None:
    stale_minute_cutoff = now - timedelta(minutes=3)
    for key in list(_groq_context_minute_usage.keys()):
        if key[1] < stale_minute_cutoff:
            _groq_context_minute_usage.pop(key, None)

    for key in list(_groq_context_day_usage.keys()):
        if key[1] < now.date():
            _groq_context_day_usage.pop(key, None)

    for key_index, cooldown_until in list(_groq_context_key_cooldown_until.items()):
        if cooldown_until <= now:
            _groq_context_key_cooldown_until.pop(key_index, None)


def _is_groq_context_key_available(key_index: int, now: datetime) -> bool:
    cooldown_until = _groq_context_key_cooldown_until.get(key_index)
    if cooldown_until and cooldown_until > now:
        return False

    minute_bucket = now.replace(second=0, microsecond=0)
    minute_count = _groq_context_minute_usage.get((key_index, minute_bucket), 0)
    if minute_count >= max(settings.groq_rpm, 1):
        return False

    day_bucket = now.date()
    day_count = _groq_context_day_usage.get((key_index, day_bucket), 0)
    if day_count >= max(settings.groq_rpd, 1):
        return False

    return True


def _increment_groq_context_usage(key_index: int, now: datetime) -> None:
    minute_bucket = now.replace(second=0, microsecond=0)
    day_bucket = now.date()

    minute_key = (key_index, minute_bucket)
    day_key = (key_index, day_bucket)

    _groq_context_minute_usage[minute_key] = _groq_context_minute_usage.get(minute_key, 0) + 1
    _groq_context_day_usage[day_key] = _groq_context_day_usage.get(day_key, 0) + 1


def _get_active_groq_context_key() -> tuple[int, str] | tuple[None, None]:
    global _groq_context_active_key_index

    keys = _normalized_groq_context_keys()
    if not keys:
        return None, None

    now = datetime.now(timezone.utc)
    _cleanup_groq_context_state(now)

    if _groq_context_active_key_index >= len(keys):
        _groq_context_active_key_index = 0

    if _is_groq_context_key_available(_groq_context_active_key_index, now):
        return _groq_context_active_key_index, keys[_groq_context_active_key_index]

    for index in range(len(keys)):
        if _is_groq_context_key_available(index, now):
            _groq_context_active_key_index = index
            return index, keys[index]

    soonest_index = _groq_context_active_key_index
    return soonest_index, keys[soonest_index]


def _mark_groq_context_key_exhausted(exhausted_index: int, reason: str) -> None:
    global _groq_context_active_key_index

    keys = _normalized_groq_context_keys()
    if not keys:
        return

    lowered = reason.lower()
    cooldown_seconds = settings.groq_key_cooldown_seconds
    if "invalid" in lowered or "revoked" in lowered or "deactivated" in lowered:
        cooldown_seconds = 6 * 3600

    _groq_context_key_cooldown_until[exhausted_index] = datetime.now(timezone.utc) + timedelta(
        seconds=cooldown_seconds
    )

    for offset in range(1, len(keys) + 1):
        next_index = (exhausted_index + offset) % len(keys)
        if next_index not in _groq_context_key_cooldown_until:
            _groq_context_active_key_index = next_index
            print(
                "Groq context key switched to next configured key "
                f"(index {next_index + 1}/{len(keys)}) due to: {reason}"
            )
            return

    print("All configured Groq context keys are cooling down. Continuing with active key.")


def _get_cerebras_context_client(key_index: int) -> Any | None:
    keys = _normalized_cerebras_context_keys()
    if not keys or key_index >= len(keys):
        return None
    if Cerebras is None:
        _log_missing_cerebras_once()
        return None
    if key_index not in _cerebras_context_clients:
        _cerebras_context_clients[key_index] = Cerebras(api_key=keys[key_index])
    return _cerebras_context_clients[key_index]


def _cleanup_cerebras_context_state(now: datetime) -> None:
    stale_minute_cutoff = now - timedelta(minutes=3)
    for key in list(_cerebras_context_minute_usage.keys()):
        if key[1] < stale_minute_cutoff:
            _cerebras_context_minute_usage.pop(key, None)

    stale_hour_cutoff = now - timedelta(hours=2)
    for key in list(_cerebras_context_hour_usage.keys()):
        if key[1] < stale_hour_cutoff:
            _cerebras_context_hour_usage.pop(key, None)

    for key in list(_cerebras_context_day_usage.keys()):
        if key[1] < now.date():
            _cerebras_context_day_usage.pop(key, None)

    for key_index, cooldown_until in list(_cerebras_context_key_cooldown_until.items()):
        if cooldown_until <= now:
            _cerebras_context_key_cooldown_until.pop(key_index, None)


def _is_cerebras_context_key_available(key_index: int, now: datetime) -> bool:
    cooldown_until = _cerebras_context_key_cooldown_until.get(key_index)
    if cooldown_until and cooldown_until > now:
        return False

    minute_bucket = now.replace(second=0, microsecond=0)
    minute_count = _cerebras_context_minute_usage.get((key_index, minute_bucket), 0)
    if minute_count >= max(settings.cerebras_rpm, 1):
        return False

    hour_bucket = now.replace(minute=0, second=0, microsecond=0)
    hour_count = _cerebras_context_hour_usage.get((key_index, hour_bucket), 0)
    if hour_count >= max(settings.cerebras_rph, 1):
        return False

    day_bucket = now.date()
    day_count = _cerebras_context_day_usage.get((key_index, day_bucket), 0)
    if day_count >= max(settings.cerebras_rpd, 1):
        return False

    return True


def _increment_cerebras_context_usage(key_index: int, now: datetime) -> None:
    minute_bucket = now.replace(second=0, microsecond=0)
    hour_bucket = now.replace(minute=0, second=0, microsecond=0)
    day_bucket = now.date()

    minute_key = (key_index, minute_bucket)
    hour_key = (key_index, hour_bucket)
    day_key = (key_index, day_bucket)

    _cerebras_context_minute_usage[minute_key] = _cerebras_context_minute_usage.get(minute_key, 0) + 1
    _cerebras_context_hour_usage[hour_key] = _cerebras_context_hour_usage.get(hour_key, 0) + 1
    _cerebras_context_day_usage[day_key] = _cerebras_context_day_usage.get(day_key, 0) + 1


def _get_active_cerebras_context_key() -> tuple[int, str] | tuple[None, None]:
    global _cerebras_context_active_key_index

    keys = _normalized_cerebras_context_keys()
    if not keys:
        return None, None

    now = datetime.now(timezone.utc)
    _cleanup_cerebras_context_state(now)

    if _cerebras_context_active_key_index >= len(keys):
        _cerebras_context_active_key_index = 0

    if _is_cerebras_context_key_available(_cerebras_context_active_key_index, now):
        return _cerebras_context_active_key_index, keys[_cerebras_context_active_key_index]

    for index in range(len(keys)):
        if _is_cerebras_context_key_available(index, now):
            _cerebras_context_active_key_index = index
            return index, keys[index]

    soonest_index = _cerebras_context_active_key_index
    return soonest_index, keys[soonest_index]


def _mark_cerebras_context_key_exhausted(exhausted_index: int, reason: str) -> None:
    global _cerebras_context_active_key_index

    keys = _normalized_cerebras_context_keys()
    if not keys:
        return

    lowered = reason.lower()
    cooldown_seconds = settings.cerebras_key_cooldown_seconds
    if "invalid" in lowered or "revoked" in lowered or "deactivated" in lowered:
        cooldown_seconds = 6 * 3600

    _cerebras_context_key_cooldown_until[exhausted_index] = datetime.now(timezone.utc) + timedelta(
        seconds=cooldown_seconds
    )

    _cerebras_context_clients.pop(exhausted_index, None)

    for offset in range(1, len(keys) + 1):
        next_index = (exhausted_index + offset) % len(keys)
        if next_index not in _cerebras_context_key_cooldown_until:
            _cerebras_context_active_key_index = next_index
            print(
                "Cerebras context key switched to next configured key "
                f"(index {next_index + 1}/{len(keys)}) due to: {reason}"
            )
            return

    print("All configured Cerebras context keys are cooling down. Continuing with active key.")


async def _create_groq_context_completion_async(
    messages: list[dict[str, str]],
    *,
    max_completion_tokens: int,
    temperature: float,
    model_name: str | None = None,
) -> dict[str, Any] | None:
    keys = _normalized_groq_context_keys()
    if not keys:
        keys = _normalized_groq_keys()
        if not keys:
            return None

    max_attempts = max(3, len(keys) + 1)
    for attempt in range(1, max_attempts + 1):
        if _normalized_groq_context_keys():
            key_index, api_key = _get_active_groq_context_key()
            label_prefix = "groq-context-key"
            exhausted_fn = _mark_groq_context_key_exhausted
            increment_fn = _increment_groq_context_usage
        else:
            key_index, api_key = _get_active_groq_key()
            label_prefix = "groq-key"
            exhausted_fn = _mark_groq_key_exhausted
            increment_fn = _increment_groq_usage

        if api_key is None or key_index is None:
            return None

        url = settings.groq_base_url.rstrip("/") + "/chat/completions"
        resolved_model_name = model_name or settings.groq_model
        payload = {
            "model": resolved_model_name,
            "messages": messages,
            "temperature": temperature,
            "top_p": _DEFAULT_TOP_P,
            "max_tokens": max_completion_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code >= 400:
                detail = response.text
                try:
                    body = response.json()
                    error = body.get("error", {}) if isinstance(body, dict) else {}
                    if isinstance(error, dict):
                        detail = str(error.get("message") or detail)
                except Exception:
                    pass

                raise _GroqRequestError(response.status_code, detail)

            body = response.json()
            if not isinstance(body, dict):
                raise _GroqRequestError(response.status_code, "Unexpected Groq response shape")

            usage_time = datetime.now(timezone.utc)
            increment_fn(key_index, usage_time)
            await _record_llm_usage(
                provider="groq-context",
                model_name=resolved_model_name,
                api_key_label=f"{label_prefix}-{key_index + 1}",
                now=usage_time,
            )
            return body

        except _GroqRequestError as exc:
            if _is_groq_key_exhausted_error(exc.status_code, exc.message):
                exhausted_fn(key_index, exc.message)
                continue

            if attempt < max_attempts and _is_groq_retryable_error(exc.status_code, exc.message):
                backoff_seconds = min(8, 2 ** (attempt - 1))
                await asyncio.sleep(backoff_seconds)
                continue

            raise

        except Exception as exc:
            message = str(exc)
            if attempt < max_attempts and _is_groq_retryable_error(None, message):
                backoff_seconds = min(8, 2 ** (attempt - 1))
                await asyncio.sleep(backoff_seconds)
                continue
            raise

    return None


def _create_cerebras_context_completion_sync(
    messages: list[dict[str, str]],
    *,
    max_completion_tokens: int,
    temperature: float,
    model_name: str | None = None,
    key_index: int = 0,
) -> Any | None:
    client = _get_cerebras_context_client(key_index)
    if client is None:
        return None

    return client.chat.completions.create(
        messages=messages,
        model=model_name or settings.cerebras_model,
        max_completion_tokens=max_completion_tokens,
        temperature=temperature,
        top_p=_DEFAULT_TOP_P,
        stream=False,
    )


async def _create_cerebras_context_completion_async(
    messages: list[dict[str, str]],
    *,
    max_completion_tokens: int,
    temperature: float,
    model_name: str | None = None,
) -> dict[str, Any] | None:
    keys = _normalized_cerebras_context_keys()
    if not keys:
        keys = _normalized_cerebras_keys()
        if not keys:
            return None
    if Cerebras is None:
        _log_missing_cerebras_once()
        return None

    max_attempts = max(3, len(keys) + 1)
    for attempt in range(1, max_attempts + 1):
        if _normalized_cerebras_context_keys():
            key_index, api_key = _get_active_cerebras_context_key()
            label_prefix = "cerebras-context-key"
            exhausted_fn = _mark_cerebras_context_key_exhausted
            increment_fn = _increment_cerebras_context_usage
            sync_fn = _create_cerebras_context_completion_sync
        else:
            key_index, api_key = _get_active_cerebras_key()
            label_prefix = "cerebras-key"
            exhausted_fn = _mark_cerebras_key_exhausted
            increment_fn = _increment_cerebras_usage
            sync_fn = _create_cerebras_completion_sync

        if api_key is None or key_index is None:
            return None

        try:
            response = await asyncio.to_thread(
                sync_fn,
                messages,
                max_completion_tokens=max_completion_tokens,
                temperature=temperature,
                model_name=model_name,
                key_index=key_index,
            )
            if response is None:
                continue

            usage_time = datetime.now(timezone.utc)
            increment_fn(key_index, usage_time)
            await _record_llm_usage(
                provider="cerebras-context",
                model_name=model_name or settings.cerebras_model,
                api_key_label=f"{label_prefix}-{key_index + 1}",
                now=usage_time,
            )

            text = _extract_response_text(response)
            if not text:
                continue

            return {
                "choices": [
                    {
                        "message": {
                            "content": text,
                        }
                    }
                ]
            }

        except Exception as exc:
            if _is_cerebras_key_exhausted_error(exc):
                exhausted_fn(key_index, str(exc))
                continue

            if attempt < max_attempts and _is_retryable_cerebras_error(exc):
                backoff_seconds = min(8, 2 ** (attempt - 1))
                await asyncio.sleep(backoff_seconds)
                continue

            print(f"Cerebras context completion error: {exc}")
            break

    return None


async def potentials_context_alert_match(news_text: str, context_alerts: list[dict[str, Any]]) -> list[int]:
    if not context_alerts:
        return []

    alerts_formatted = "\n".join([
        f"- ID: {alert['id']} | Context Alert Target: {alert['context_description']}"
        for alert in context_alerts
    ])

    system_prompt = (
        "You are a high-speed financial news alert filter.\n"
        "Given a news item and a list of active Context Alert targets (with their IDs), "
        "determine which targets MIGHT match the situation described in the news item.\n"
        "Be inclusive but reasonable. If a target is a potential match, include its ID.\n"
        "You MUST respond ONLY with a JSON list of integers representing the matched target IDs. "
        "No other text, conversational filler, or formatting. For example: [1, 3]"
    )

    user_content = (
        f"News Item:\n{news_text}\n\n"
        f"Active Context Alert Targets:\n{alerts_formatted}\n\n"
        "Output JSON list of matched IDs:"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]

    try:
        res = await _create_groq_context_completion_async(
            messages,
            max_completion_tokens=256,
            temperature=0.0,
            model_name=settings.groq_model
        )
        if res:
            text = res["choices"][0]["message"]["content"]
            cleaned = re.sub(r"```json\s*", "", text)
            cleaned = re.sub(r"```\s*", "", cleaned).strip()
            match_ids = json.loads(cleaned)
            if isinstance(match_ids, list):
                return [int(x) for x in match_ids if str(x).strip().isdigit() or isinstance(x, int)]
    except Exception as exc:
        print(f"Error in potentials_context_alert_match: {exc}")
    return []


async def verify_context_alert_match(news_text: str, context_description: str) -> bool:
    system_prompt = (
        "You are an expert financial news analyst. Your task is to verify if a news message "
        "matches a specified situation/context alert description with 100% confidence.\n"
        "Analyze the context and situation carefully. Do not assume or extrapolate. The match must be clear and direct.\n"
        "You MUST respond ONLY with the word 'YES' or 'NO'. No explanation, markdown, or other text."
    )

    user_content = (
        f"Context Alert Description:\n{context_description}\n\n"
        f"Incoming News Message:\n{news_text}\n\n"
        "Does this news message match the context alert description with 100% certainty? Output YES or NO:"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]

    try:
        res = await _create_cerebras_context_completion_async(
            messages,
            max_completion_tokens=2048,
            temperature=0.0,
            model_name=settings.cerebras_chat_model
        )
        if res:
            text = res["choices"][0]["message"]["content"].strip().upper()
            if "YES" in text:
                return True
    except Exception as exc:
        print(f"Error in verify_context_alert_match: {exc}")
    return False


async def propose_context_alert_description(instruction: str) -> str:
    system_prompt = (
        "You are an assistant that translates a user request for news alerts into a clean, precise, "
        "and comprehensive description of the target situation/context.\n"
        "This description will be matched against incoming news lines by an LLM.\n"
        "Expand the user's brief request to cover synonyms, key events, and clarity, "
        "while remaining very specific to the situation.\n"
        "Keep the output description concise (1-2 sentences), and do not include any extra text or intro."
    )

    user_content = (
        f"User Alert Request: '{instruction}'\n\n"
        "Proposed Context Description:"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]

    try:
        res = await _create_cerebras_context_completion_async(
            messages,
            max_completion_tokens=2048,
            temperature=0.5,
            model_name=settings.cerebras_chat_model
        )
        if res:
            return res["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print(f"Error in propose_context_alert_description: {exc}")
    return f"Alert for: {instruction}"
