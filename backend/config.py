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
    1200,
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


def _parse_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value.strip())
    except Exception:
        return default


def _parse_provider_order(value: str) -> List[str]:
    allowed = {"groq", "gemini", "cerebras"}
    ordered = [item.lower() for item in _parse_csv(value)]
    filtered = [item for item in ordered if item in allowed]
    return filtered or ["groq", "cerebras", "gemini"]


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
    news_dedupe_window_seconds: int
    news_dedupe_similarity: float

    twitter_username: str
    twitter_email: str
    twitter_password: str
    twitter_totp_secret: str
    twitter_handles: List[str]
    twitter_poll_seconds: int
    twitter_cookies_file: str

    # Cerebras — multi-key support
    cerebras_api_keys: List[str]
    cerebras_model: str
    cerebras_chat_model: str
    cerebras_rpm: int
    cerebras_rph: int
    cerebras_rpd: int
    cerebras_key_cooldown_seconds: int

    # Groq — multi-key with rate limits
    groq_api_keys: List[str]
    groq_model: str
    groq_base_url: str
    groq_rpm: int
    groq_rpd: int
    groq_key_cooldown_seconds: int

    # Gemini — last resort
    gemini_api_keys: List[str]
    gemini_primary_model: str
    gemini_fallback_model: str
    gemini_primary_rpm: int
    gemini_fallback_rpm: int
    gemini_requests_per_day: int
    gemini_key_cooldown_seconds: int

    # Provider ordering
    llm_provider_order: List[str]
    summary_provider_order: List[str]
    classification_provider_order: List[str]
    chat_default_model: str
    chat_groq_model: str

    alert_bot_token: str
    alert_chat_id: str
    summary_bot_token: str
    summary_chat_id: str
    context_bot_token: str
    context_chat_id: str

    app_passcode: str
    groq_context_api_keys: List[str]
    cerebras_context_api_keys: List[str]

    # Proxy settings (optional — only needed when Telegram is blocked)
    proxy_enabled: bool
    proxy_type: str       # "socks5" or "http"
    proxy_host: str
    proxy_port: int
    proxy_username: str
    proxy_password: str

    startup_summary_interval_seconds: int

    # Mobile App Settings
    fcm_credentials_path: str
    mobile_bypass_token: str

    # Watchdog & Email settings
    watchdog_check_interval_seconds: int
    watchdog_no_news_threshold_seconds: int
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_to: str

    # CallMeBot WhatsApp settings
    whatsapp_phone: str
    whatsapp_apikey: str


    @classmethod
    def from_env(cls) -> "Settings":
        startup_interval = int(os.getenv("SUMMARY_INTERVAL_SECONDS", "120"))
        if startup_interval not in ALLOWED_SUMMARY_INTERVALS:
            startup_interval = 120

        telegram_api_id = os.getenv("TELEGRAM_API_ID")

        # Cerebras: support both CEREBRAS_API_KEYS (new) and CEREBRAS_API_KEY (old)
        cerebras_keys_raw = os.getenv(
            "CEREBRAS_API_KEYS",
            os.getenv("CEREBRAS_API_KEY", ""),
        )

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
                default=False,
            ),
            news_dedupe_window_seconds=max(
                _parse_int(os.getenv("NEWS_DEDUPE_WINDOW_SECONDS"), 180),
                30,
            ),
            news_dedupe_similarity=min(
                max(_parse_float(os.getenv("NEWS_DEDUPE_SIMILARITY"), 0.88), 0.5),
                1.0,
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
            # Cerebras multi-key
            cerebras_api_keys=_parse_csv(cerebras_keys_raw),
            cerebras_model=os.getenv(
                "CEREBRAS_MODEL", "qwen-3-235b-a22b-instruct-2507"
            ),
            cerebras_chat_model=os.getenv("CEREBRAS_CHAT_MODEL", "zai-glm-4.7"),
            cerebras_rpm=_parse_int(os.getenv("CEREBRAS_RPM"), 5),
            cerebras_rph=_parse_int(os.getenv("CEREBRAS_RPH"), 150),
            cerebras_rpd=_parse_int(os.getenv("CEREBRAS_RPD"), 2400),
            cerebras_key_cooldown_seconds=_parse_int(
                os.getenv("CEREBRAS_KEY_COOLDOWN_SECONDS"), 90
            ),
            # Groq multi-key with rate limits
            groq_api_keys=_parse_csv(
                os.getenv("GROQ_API_KEYS", os.getenv("GROQ_API_KEY", ""))
            ),
            groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            groq_base_url=os.getenv(
                "GROQ_BASE_URL", "https://api.groq.com/openai/v1"
            ),
            groq_rpm=_parse_int(os.getenv("GROQ_RPM"), 30),
            groq_rpd=_parse_int(os.getenv("GROQ_RPD"), 1000),
            groq_key_cooldown_seconds=_parse_int(
                os.getenv("GROQ_KEY_COOLDOWN_SECONDS"), 120
            ),
            # Gemini (last resort)
            gemini_api_keys=_parse_csv(os.getenv("GEMINI_API_KEYS", "")),
            gemini_primary_model=os.getenv("GEMINI_PRIMARY_MODEL", "gemini-2.5-flash-lite"),
            gemini_fallback_model=os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash"),
            gemini_primary_rpm=_parse_int(os.getenv("GEMINI_PRIMARY_RPM"), 30),
            gemini_fallback_rpm=_parse_int(os.getenv("GEMINI_FALLBACK_RPM"), 15),
            gemini_requests_per_day=_parse_int(os.getenv("GEMINI_REQUESTS_PER_DAY"), 1500),
            gemini_key_cooldown_seconds=_parse_int(os.getenv("GEMINI_KEY_COOLDOWN_SECONDS"), 90),
            # Provider ordering — Gemini always last
            llm_provider_order=_parse_provider_order(
                os.getenv("LLM_PROVIDER_ORDER", "groq,cerebras,gemini")
            ),
            summary_provider_order=_parse_provider_order(
                os.getenv(
                    "SUMMARY_PROVIDER_ORDER",
                    os.getenv("LLM_PROVIDER_ORDER", "groq,cerebras,gemini"),
                )
            ),
            classification_provider_order=_parse_provider_order(
                os.getenv(
                    "CLASSIFICATION_PROVIDER_ORDER",
                    os.getenv("LLM_PROVIDER_ORDER", "groq,cerebras,gemini"),
                )
            ),
            chat_default_model=os.getenv("CHAT_DEFAULT_MODEL", "groq_gpt_oss"),
            chat_groq_model=os.getenv("CHAT_GROQ_MODEL", "openai/gpt-oss-120b"),
            alert_bot_token=os.getenv("ALERT_BOT_TOKEN", ""),
            alert_chat_id=os.getenv("ALERT_CHAT_ID", ""),
            summary_bot_token=os.getenv("SUMMARY_BOT_TOKEN", ""),
            summary_chat_id=os.getenv("SUMMARY_CHAT_ID", ""),
            context_bot_token=os.getenv("CONTEXT_BOT_TOKEN", ""),
            context_chat_id=os.getenv("CONTEXT_CHAT_ID", ""),
            app_passcode=os.getenv("APP_PASSCODE", "7539"),
            groq_context_api_keys=_parse_csv(os.getenv("GROQ_CONTEXT_API_KEYS", "")),
            cerebras_context_api_keys=_parse_csv(os.getenv("CEREBRAS_CONTEXT_API_KEYS", "")),
            # Proxy settings
            proxy_enabled=_parse_bool(os.getenv("PROXY_ENABLED"), default=False),
            proxy_type=os.getenv("PROXY_TYPE", "socks5").strip().lower(),
            proxy_host=os.getenv("PROXY_HOST", ""),
            proxy_port=_parse_int(os.getenv("PROXY_PORT"), 0),
            proxy_username=os.getenv("PROXY_USERNAME", ""),
            proxy_password=os.getenv("PROXY_PASSWORD", ""),
            startup_summary_interval_seconds=startup_interval,
            fcm_credentials_path=os.getenv("FCM_CREDENTIALS_PATH", "").strip(),
            mobile_bypass_token=os.getenv("MOBILE_BYPASS_TOKEN", "").strip(),
            watchdog_check_interval_seconds=_parse_int(os.getenv("WATCHDOG_CHECK_INTERVAL_SECONDS"), 3600),
            watchdog_no_news_threshold_seconds=_parse_int(os.getenv("WATCHDOG_NO_NEWS_THRESHOLD_SECONDS"), 3600),
            smtp_host=os.getenv("SMTP_HOST", "").strip(),
            smtp_port=_parse_int(os.getenv("SMTP_PORT"), 587),
            smtp_user=os.getenv("SMTP_USER", "").strip(),
            smtp_password=os.getenv("SMTP_PASSWORD", "").strip(),
            smtp_to=os.getenv("SMTP_TO", "").strip(),
            whatsapp_phone=os.getenv("WHATSAPP_PHONE", "").strip(),
            whatsapp_apikey=os.getenv("WHATSAPP_APIKEY", "").strip(),
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


def chat_model_options() -> list[dict[str, str]]:
    return [
        {
            "id": "groq_gpt_oss",
            "label": "Groq GPT-OSS 120B",
            "provider": "groq",
            "model": settings.chat_groq_model,
        },
        {
            "id": "cerebras_glm_4_7",
            "label": "Cerebras GLM 4.7",
            "provider": "cerebras",
            "model": settings.cerebras_chat_model,
        },
        {
            "id": "gemini_flash",
            "label": "Gemini 2.5 Flash",
            "provider": "gemini",
            "model": settings.gemini_fallback_model,
        },
        {
            "id": "gemini_flash_lite",
            "label": "Gemini 2.5 Flash Lite",
            "provider": "gemini",
            "model": settings.gemini_primary_model,
        },
    ]


def resolve_chat_model(model_id: str | None) -> dict[str, str]:
    options = chat_model_options()
    requested = (model_id or settings.chat_default_model).strip()
    for option in options:
        if option["id"] == requested:
            return option
    return options[0]


def build_telethon_proxy() -> dict | None:
    """Build a proxy config dict for Telethon if proxy is enabled."""
    if not settings.proxy_enabled or not settings.proxy_host or not settings.proxy_port:
        return None

    try:
        from python_socks import ProxyType
    except ImportError:
        print("WARNING: python-socks not installed. Cannot use proxy for Telethon.")
        return None

    proxy_type_map = {
        "socks5": ProxyType.SOCKS5,
        "socks4": ProxyType.SOCKS4,
        "http": ProxyType.HTTP,
    }

    selected_type = proxy_type_map.get(settings.proxy_type, ProxyType.SOCKS5)

    proxy_config = {
        "proxy_type": selected_type,
        "addr": settings.proxy_host,
        "port": settings.proxy_port,
        "rdns": True,
    }

    if settings.proxy_username:
        proxy_config["username"] = settings.proxy_username
    if settings.proxy_password:
        proxy_config["password"] = settings.proxy_password

    return proxy_config


def build_httpx_proxy_url() -> str | None:
    """Build a SOCKS5 proxy URL string for httpx-socks if proxy is enabled."""
    if not settings.proxy_enabled or not settings.proxy_host or not settings.proxy_port:
        return None

    scheme = "socks5" if settings.proxy_type == "socks5" else "http"

    if settings.proxy_username and settings.proxy_password:
        return f"{scheme}://{settings.proxy_username}:{settings.proxy_password}@{settings.proxy_host}:{settings.proxy_port}"
    else:
        return f"{scheme}://{settings.proxy_host}:{settings.proxy_port}"
