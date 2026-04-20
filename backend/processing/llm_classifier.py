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


_client: Any | None = None
_client_import_error_logged = False
_gemini_import_error_logged = False
_DEFAULT_TOP_P = 0.8
_groq_active_key_index = 0
_groq_exhausted_keys: set[int] = set()
_gemini_key_cooldown_until: dict[int, datetime] = {}
_gemini_minute_usage: dict[tuple[int, str, datetime], int] = {}
_gemini_day_usage: dict[tuple[int, str, date], int] = {}


class _GroqRequestError(Exception):
    def __init__(self, status_code: int | None, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _log_missing_sdk_once() -> None:
    global _client_import_error_logged
    if _client_import_error_logged:
        return
    _client_import_error_logged = True
    print(
        "Cerebras SDK is not available. Install 'cerebras-cloud-sdk' "
        "or fallback heuristics will be used."
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


def _get_client() -> Any | None:
    global _client
    if not settings.cerebras_api_key:
        return None
    if Cerebras is None:
        _log_missing_sdk_once()
        return None
    if _client is None:
        _client = Cerebras(api_key=settings.cerebras_api_key)
    return _client


def _normalized_gemini_keys() -> list[str]:
    normalized: list[str] = []
    for raw_key in settings.gemini_api_keys:
        if not raw_key:
            continue
        key = raw_key.strip().strip('"').strip("'")
        if key:
            normalized.append(key)
    return normalized


def _gemini_models_with_limits() -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []

    primary_model = (settings.gemini_primary_model or "").strip()
    fallback_model = (settings.gemini_fallback_model or "").strip()

    if primary_model:
        items.append((primary_model, max(settings.gemini_primary_rpm, 1)))

    if fallback_model and fallback_model != primary_model:
        items.append((fallback_model, max(settings.gemini_fallback_rpm, 1)))

    return items


def _has_llm_provider() -> bool:
    return (
        bool(_normalized_gemini_keys())
        or bool(_normalized_groq_keys())
        or _get_client() is not None
    )


def _normalized_groq_keys() -> list[str]:
    normalized: list[str] = []
    for raw_key in settings.groq_api_keys:
        if not raw_key:
            continue
        key = raw_key.strip().strip('"').strip("'")
        if key:
            normalized.append(key)
    return normalized


def _get_active_groq_key() -> tuple[int, str] | tuple[None, None]:
    global _groq_active_key_index

    keys = _normalized_groq_keys()
    if not keys:
        return None, None

    if _groq_active_key_index >= len(keys):
        _groq_active_key_index = 0

    if _groq_active_key_index not in _groq_exhausted_keys:
        return _groq_active_key_index, keys[_groq_active_key_index]

    for index, key in enumerate(keys):
        if index not in _groq_exhausted_keys:
            _groq_active_key_index = index
            return index, key

    # If all keys are exhausted, keep using the active one and rely on provider reset.
    return _groq_active_key_index, keys[_groq_active_key_index]


def _mark_groq_key_exhausted(exhausted_index: int, reason: str) -> None:
    global _groq_active_key_index

    keys = _normalized_groq_keys()
    if not keys:
        return

    _groq_exhausted_keys.add(exhausted_index)

    for offset in range(1, len(keys) + 1):
        next_index = (exhausted_index + offset) % len(keys)
        if next_index not in _groq_exhausted_keys:
            _groq_active_key_index = next_index
            print(
                "Groq key switched to next configured key "
                f"(index {next_index + 1}/{len(keys)}) due to: {reason}"
            )
            return

    print(
        "All configured Groq keys look exhausted or invalid. "
        "Continuing with fallback behavior until quota resets."
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
        # Treat generic throttling as transient; key rotation is reserved for quota/key failures.
        return not _is_groq_key_exhausted_error(status_code, lowered)

    transient_signals = [
        "temporarily unavailable",
        "timeout",
        "connection reset",
        "try again",
    ]
    return any(signal in lowered for signal in transient_signals)


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


def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
    sections: list[str] = []
    for message in messages:
        role = str(message.get("role", "user")).upper()
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        sections.append(f"{role}:\n{content}")
    return "\n\n".join(sections).strip()


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


def _create_completion_sync(
    messages: list[dict[str, str]],
    *,
    max_completion_tokens: int,
    temperature: float,
) -> Any | None:
    client = _get_client()
    if client is None:
        return None

    return client.chat.completions.create(
        messages=messages,
        model=settings.cerebras_model,
        max_completion_tokens=max_completion_tokens,
        temperature=temperature,
        top_p=_DEFAULT_TOP_P,
        stream=False,
    )


async def _create_groq_completion_async(
    messages: list[dict[str, str]],
    *,
    max_completion_tokens: int,
    temperature: float,
) -> dict[str, Any] | None:
    keys = _normalized_groq_keys()
    if not keys:
        return None

    # One key at a time. Rotate only when key is invalid or quota-exhausted.
    max_attempts = max(3, len(keys) + 1)
    for attempt in range(1, max_attempts + 1):
        key_index, api_key = _get_active_groq_key()
        if api_key is None or key_index is None:
            return None

        url = settings.groq_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": settings.groq_model,
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


async def _create_completion_async(
    messages: list[dict[str, str]],
    *,
    max_completion_tokens: int,
    temperature: float,
) -> Any | None:
    if _normalized_gemini_keys():
        try:
            gemini_response = await _create_gemini_completion_async(
                messages,
                max_completion_tokens=max_completion_tokens,
                temperature=temperature,
            )
            if gemini_response is not None:
                return gemini_response
        except Exception as exc:
            print(f"Gemini completion error: {exc}")

    if _normalized_groq_keys():
        try:
            groq_response = await _create_groq_completion_async(
                messages,
                max_completion_tokens=max_completion_tokens,
                temperature=temperature,
            )
            if groq_response is not None:
                return groq_response
        except Exception as exc:
            print(f"Groq completion error: {exc}")

    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.to_thread(
                _create_completion_sync,
                messages,
                max_completion_tokens=max_completion_tokens,
                temperature=temperature,
            )
        except Exception as exc:
            if attempt >= max_attempts or not _is_retryable_cerebras_error(exc):
                raise

            backoff_seconds = min(8, 2 ** (attempt - 1))
            print(
                f"Cerebras request retry {attempt}/{max_attempts} after "
                f"{backoff_seconds}s due to transient error: {exc}"
            )
            await asyncio.sleep(backoff_seconds)

    return None


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
    value = _strip_visual_ellipsis(str(value))
    return f"- {value}" if value else ""


def _build_fallback_window_summary(news_rows: list[dict[str, Any]]) -> str:
    lines = [_summary_line_from_row(row) for row in news_rows[-6:]]
    lines = [line for line in lines if line]
    if not lines:
        return "No major updates in this time window."
    return "Latest updates:\n" + "\n".join(lines)


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


async def summarize_news_window(news_rows: list[dict[str, Any]], window_seconds: int) -> str:
    if not news_rows:
        return "No major updates in this time window."

    if settings.fast_summary_mode or not _has_llm_provider():
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
        "Give one headline sentence and up to 5 bullet points. "
        "Mention notable risks and instruments if present."
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
        )
        if response is None:
            return _build_fallback_window_summary(news_rows)

        output = _extract_response_text(response)
        output = _strip_source_prefixes(_strip_visual_ellipsis(output.strip()))
        return output if output else "No major updates in this window."
    except Exception as exc:
        print(f"LLM summarize error: {exc}")
        return _build_fallback_window_summary(news_rows)


async def answer_with_news_context(
    question: str,
    news_rows: list[dict[str, Any]],
    *,
    time_bucket_digest: str = "",
) -> str:
    if not news_rows:
        return "I could not find matching news in the selected time range."

    context_lines = []
    for row in news_rows[:350]:
        context_lines.append(
            f"[{row.get('fetched_at')}] {row.get('source')} / {row.get('source_channel')}: {row.get('raw_text')}"
        )

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
        response = await _create_completion_async(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt[:14000]},
            ],
            temperature=0.2,
            max_completion_tokens=1200,
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
