from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

import aiomysql

from config import runtime_state, settings


_pool: "DbPool" | None = None

_JSON_COLUMNS = {
    "instruments_affected",
    "matched_topics",
    "keywords",
    "sources",
    "source_channels",
}
_BOOL_COLUMNS = {"llm_processed", "active"}
_DATETIME_COLUMNS = {
    "fetched_at",
    "published_at",
    "window_start",
    "window_end",
    "bucket_start",
    "created_at",
    "updated_at",
    "sent_at",
}
_PARAM_RE = re.compile(r"\$\d+")
_CREATE_TABLE_RE = re.compile(
    r"^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    flags=re.IGNORECASE,
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS news (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    source VARCHAR(50) NOT NULL,
    source_channel VARCHAR(200),
    raw_text LONGTEXT NOT NULL,
    url LONGTEXT,
    content_hash VARCHAR(64) UNIQUE NOT NULL,
    summary LONGTEXT,
    category VARCHAR(100),
    urgency VARCHAR(20),
    sentiment VARCHAR(20),
    instruments_affected JSON NOT NULL,
    matched_topics JSON NOT NULL,
    llm_processed BOOLEAN DEFAULT FALSE,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    published_at DATETIME,
    INDEX idx_news_fetched_at (fetched_at),
    INDEX idx_news_urgency (urgency),
    INDEX idx_news_source (source),
    INDEX idx_news_hash (content_hash)
);

CREATE TABLE IF NOT EXISTS summary_batches (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    window_seconds INT NOT NULL,
    window_start DATETIME NOT NULL,
    window_end DATETIME NOT NULL,
    summary_text LONGTEXT NOT NULL,
    item_count INT NOT NULL,
    sources JSON NOT NULL,
    source_channels JSON NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_summary_batches_created_at (created_at)
);

CREATE TABLE IF NOT EXISTS topics (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    topic_name VARCHAR(200) NOT NULL,
    keywords JSON NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    alert_urgency_threshold VARCHAR(20) DEFAULT 'MEDIUM',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS context_alerts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    context_description LONGTEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alert_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    news_id BIGINT,
    topic_id BIGINT,
    channel VARCHAR(50) DEFAULT 'telegram',
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_text LONGTEXT NOT NULL,
    INDEX idx_alert_log_sent_at (sent_at),
    CONSTRAINT fk_alert_news FOREIGN KEY (news_id) REFERENCES news(id) ON DELETE SET NULL,
    CONSTRAINT fk_alert_topic FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    setting_key VARCHAR(120) PRIMARY KEY,
    value LONGTEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_api_usage (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    provider VARCHAR(40) NOT NULL,
    model_name VARCHAR(120) NOT NULL,
    api_key_label VARCHAR(80) NOT NULL,
    bucket_type VARCHAR(16) NOT NULL,
    bucket_start DATETIME NOT NULL,
    request_count INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_llm_api_usage_bucket (
        provider,
        model_name,
        api_key_label,
        bucket_type,
        bucket_start
    ),
    INDEX idx_llm_api_usage_updated_at (updated_at)
);
"""


def _parse_mysql_dsn(database_url: str) -> dict[str, Any]:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"mysql", "mysql+aiomysql"}:
        raise ValueError(
            "DATABASE_URL must use mysql:// or mysql+aiomysql:// when running MySQL backend"
        )

    database = (parsed.path or "").lstrip("/")
    if not database:
        raise ValueError("DATABASE_URL must include a database name")

    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "db": database,
        "charset": "utf8mb4",
        "autocommit": True,
    }


def _rewrite_sql(sql: str) -> str:
    rewritten = _PARAM_RE.sub("%s", sql)
    rewritten = re.sub(r"\bILIKE\b", "LIKE", rewritten, flags=re.IGNORECASE)
    return rewritten


def _extract_create_table_name(statement: str) -> str | None:
    match = _CREATE_TABLE_RE.match(statement.strip())
    if not match:
        return None
    return match.group(1).lower()


def _normalize_param(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return value


def _deserialize_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key in _JSON_COLUMNS:
            out[key] = _decode_json(value)
            continue

        if key in _BOOL_COLUMNS:
            out[key] = bool(value)
            continue

        if key in _DATETIME_COLUMNS and isinstance(value, datetime) and value.tzinfo is None:
            out[key] = value.replace(tzinfo=timezone.utc)
            continue

        out[key] = value
    return out


def _decode_json(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return []
    return value


class DbConnection:
    def __init__(self, raw_conn: aiomysql.Connection) -> None:
        self._raw_conn = raw_conn

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        rewritten = _rewrite_sql(sql)
        normalized = [_normalize_param(param) for param in params]
        async with self._raw_conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(rewritten, normalized)
            rows = await cursor.fetchall()
        return [_deserialize_row(dict(row)) for row in rows]

    async def fetchrow(self, sql: str, *params: Any) -> dict[str, Any] | None:
        rows = await self.fetch(sql, *params)
        if not rows:
            return None
        return rows[0]

    async def fetchval(self, sql: str, *params: Any) -> Any:
        row = await self.fetchrow(sql, *params)
        if not row:
            return None
        return next(iter(row.values()))

    async def execute(self, sql: str, *params: Any) -> str:
        rewritten = _rewrite_sql(sql)
        normalized = [_normalize_param(param) for param in params]
        async with self._raw_conn.cursor() as cursor:
            affected = await cursor.execute(rewritten, normalized)
        statement = rewritten.strip().split(maxsplit=1)[0].upper() if rewritten.strip() else "EXECUTE"
        return f"{statement} {affected}"


class _AcquireContext:
    def __init__(self, pool: "DbPool") -> None:
        self._pool = pool
        self._raw_conn: aiomysql.Connection | None = None

    async def __aenter__(self) -> DbConnection:
        self._raw_conn = await self._pool._raw_pool.acquire()
        return DbConnection(self._raw_conn)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._raw_conn is not None:
            self._pool._raw_pool.release(self._raw_conn)
            self._raw_conn = None


class DbPool:
    def __init__(self, raw_pool: aiomysql.Pool) -> None:
        self._raw_pool = raw_pool

    def acquire(self) -> _AcquireContext:
        return _AcquireContext(self)

    async def close(self) -> None:
        self._raw_pool.close()
        await self._raw_pool.wait_closed()


async def init_pool() -> DbPool:
    global _pool
    if _pool is not None:
        return _pool

    connect_kwargs = _parse_mysql_dsn(settings.database_url)
    raw_pool = await aiomysql.create_pool(minsize=1, maxsize=20, **connect_kwargs)
    _pool = DbPool(raw_pool)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
    _pool = None


def get_pool() -> DbPool:
    if _pool is None:
        raise RuntimeError("Database pool has not been initialized")
    return _pool


async def init_schema() -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        existing_rows = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            """
        )
        existing_tables: set[str] = set()
        for row in existing_rows:
            table_name = None
            if isinstance(row, dict):
                table_name = row.get("table_name") or row.get("TABLE_NAME")
                if table_name is None and row:
                    table_name = next(iter(row.values()))
            if table_name:
                existing_tables.add(str(table_name).lower())

        for statement in [part.strip() for part in SCHEMA_SQL.split(";") if part.strip()]:
            table_name = _extract_create_table_name(statement)
            if table_name and table_name in existing_tables:
                continue

            await conn.execute(statement)

            if table_name:
                existing_tables.add(table_name)


async def load_runtime_settings() -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM app_settings WHERE setting_key = $1",
            "summary_interval_seconds",
        )
        if row:
            value = int(row["value"])
            runtime_state.set_summary_interval_seconds(value)
            return value

        current = runtime_state.get_summary_interval_seconds()
        await conn.execute(
            """
            INSERT INTO app_settings(setting_key, value)
            VALUES($1, $2) AS incoming
            ON DUPLICATE KEY UPDATE value = incoming.value, updated_at = CURRENT_TIMESTAMP
            """,
            "summary_interval_seconds",
            str(current),
        )
        return current


async def save_summary_interval(seconds: int) -> int:
    runtime_state.set_summary_interval_seconds(seconds)
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings(setting_key, value)
            VALUES($1, $2) AS incoming
            ON DUPLICATE KEY UPDATE value = incoming.value, updated_at = CURRENT_TIMESTAMP
            """,
            "summary_interval_seconds",
            str(seconds),
        )
    return seconds


async def load_proxy_setting() -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM app_settings WHERE setting_key = $1",
            "proxy_enabled",
        )
        if row:
            value = row["value"].lower() == "true"
            settings.proxy_enabled = value
            return value

        current = settings.proxy_enabled
        await conn.execute(
            """
            INSERT INTO app_settings(setting_key, value)
            VALUES($1, $2) AS incoming
            ON DUPLICATE KEY UPDATE value = incoming.value, updated_at = CURRENT_TIMESTAMP
            """,
            "proxy_enabled",
            "true" if current else "false",
        )
        return current


async def save_proxy_setting(enabled: bool) -> bool:
    settings.proxy_enabled = enabled
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings(setting_key, value)
            VALUES($1, $2) AS incoming
            ON DUPLICATE KEY UPDATE value = incoming.value, updated_at = CURRENT_TIMESTAMP
            """,
            "proxy_enabled",
            "true" if enabled else "false",
        )
    return enabled

