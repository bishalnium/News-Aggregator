import os
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import List

from dotenv import load_dotenv


load_dotenv()


ALLOWED_SUMMARY_INTERVALS = {
    30,
    60,
    120,
    300,
    600,
    900,
    1800,
    3600,
    7200,
    86400,
}


def _parse_csv(value: str) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except Exception:
        return default


@dataclass
class Settings:
    app_name: str
    app_host: str
    app_port: int
    frontend_url: str
    database_url: str

    telegram_api_id: int | None
    telegram_api_hash: str
    telegram_phone: str
    telegram_channels: List[str]
    telegram_fast_classification: bool
    fast_summary_mode: bool

    twitter_username: str
    twitter_email: str
    twitter_password: str
    twitter_totp_secret: str
    twitter_handles: List[str]
    twitter_poll_seconds: int
    twitter_cookies_file: str

    cerebras_api_key: str
    cerebras_model: str
    groq_api_keys: List[str]
    groq_model: str
    groq_base_url: str
    gemini_api_keys: List[str]
    gemini_primary_model: str
    gemini_fallback_model: str
    gemini_primary_rpm: int
    gemini_fallback_rpm: int
    gemini_requests_per_day: int
    gemini_key_cooldown_seconds: int

    alert_bot_token: str
    alert_chat_id: str
    summary_bot_token: str
    summary_chat_id: str

    startup_summary_interval_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        startup_interval = int(os.getenv("SUMMARY_INTERVAL_SECONDS", "120"))
        if startup_interval not in ALLOWED_SUMMARY_INTERVALS:
            startup_interval = 120

        telegram_api_id = os.getenv("TELEGRAM_API_ID")

        return cls(
            app_name=os.getenv("APP_NAME", "News Codex Aggregator"),
            app_host=os.getenv("APP_HOST", "0.0.0.0"),
            app_port=int(os.getenv("APP_PORT", "8000")),
            frontend_url=os.getenv("FRONTEND_URL", "http://localhost:5173"),
            database_url=os.getenv(
                "DATABASE_URL",
                "mysql://newscodex:newscodex@localhost:3306/newscodex",
            ),
            telegram_api_id=int(telegram_api_id) if telegram_api_id else None,
            telegram_api_hash=os.getenv("TELEGRAM_API_HASH", ""),
            telegram_phone=os.getenv("TELEGRAM_PHONE", ""),
            telegram_channels=_parse_csv(os.getenv("TELEGRAM_CHANNELS", "")),
            telegram_fast_classification=_parse_bool(
                os.getenv("TELEGRAM_FAST_CLASSIFICATION"),
                default=True,
            ),
            fast_summary_mode=_parse_bool(
                os.getenv("FAST_SUMMARY_MODE"),
                default=True,
            ),
            twitter_username=os.getenv("TWITTER_USERNAME", ""),
            twitter_email=os.getenv("TWITTER_EMAIL", ""),
            twitter_password=os.getenv("TWITTER_PASSWORD", ""),
            twitter_totp_secret=os.getenv("TWITTER_TOTP_SECRET", ""),
            twitter_handles=_parse_csv(
                os.getenv("TWITTER_HANDLES", "FirstSquawk,MarketNewsF")
            ),
            twitter_poll_seconds=int(os.getenv("TWITTER_POLL_SECONDS", "15")),
            twitter_cookies_file=os.getenv(
                "TWITTER_COOKIES_FILE", "data/twitter_cookies.json"
            ),
            cerebras_api_key=os.getenv("CEREBRAS_API_KEY", ""),
            cerebras_model=os.getenv(
                "CEREBRAS_MODEL", "qwen-3-235b-a22b-instruct-2507"
            ),
            groq_api_keys=_parse_csv(
                os.getenv("GROQ_API_KEYS", os.getenv("GROQ_API_KEY", ""))
            ),
            groq_model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
            groq_base_url=os.getenv(
                "GROQ_BASE_URL", "https://api.groq.com/openai/v1"
            ),
            gemini_api_keys=_parse_csv(os.getenv("GEMINI_API_KEYS", "")),
            gemini_primary_model=os.getenv("GEMINI_PRIMARY_MODEL", "gemini-2.5-flash-lite"),
            gemini_fallback_model=os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash"),
            gemini_primary_rpm=_parse_int(os.getenv("GEMINI_PRIMARY_RPM"), 30),
            gemini_fallback_rpm=_parse_int(os.getenv("GEMINI_FALLBACK_RPM"), 15),
            gemini_requests_per_day=_parse_int(os.getenv("GEMINI_REQUESTS_PER_DAY"), 1500),
            gemini_key_cooldown_seconds=_parse_int(os.getenv("GEMINI_KEY_COOLDOWN_SECONDS"), 90),
            alert_bot_token=os.getenv("ALERT_BOT_TOKEN", ""),
            alert_chat_id=os.getenv("ALERT_CHAT_ID", ""),
            summary_bot_token=os.getenv("SUMMARY_BOT_TOKEN", ""),
            summary_chat_id=os.getenv("SUMMARY_CHAT_ID", ""),
            startup_summary_interval_seconds=startup_interval,
        )


class RuntimeState:
    def __init__(self, summary_interval_seconds: int) -> None:
        self._summary_interval_seconds = summary_interval_seconds
        self._summary_anchor_utc = datetime.now(timezone.utc)
        self._schedule_version = 0
        self._lock = Lock()

    def get_summary_interval_seconds(self) -> int:
        with self._lock:
            return self._summary_interval_seconds

    def get_summary_schedule(self) -> tuple[int, datetime, int]:
        with self._lock:
            return (
                self._summary_interval_seconds,
                self._summary_anchor_utc,
                self._schedule_version,
            )

    def set_summary_interval_seconds(self, value: int) -> int:
        if value not in ALLOWED_SUMMARY_INTERVALS:
            raise ValueError(
                f"Invalid summary interval: {value}. Allowed: {sorted(ALLOWED_SUMMARY_INTERVALS)}"
            )
        with self._lock:
            self._summary_interval_seconds = value
            self._summary_anchor_utc = datetime.now(timezone.utc)
            self._schedule_version += 1
            return self._summary_interval_seconds


settings = Settings.from_env()
runtime_state = RuntimeState(settings.startup_summary_interval_seconds)
