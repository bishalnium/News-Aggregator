from datetime import datetime
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


UrgencyType = Literal["HIGH", "MEDIUM", "LOW"]
SentimentType = Literal["bullish", "bearish", "neutral"]


class NewsItem(BaseModel):
    id: int
    source: str
    source_channel: str | None = None
    raw_text: str
    url: str | None = None
    summary: str | None = None
    category: str | None = None
    urgency: UrgencyType | None = None
    sentiment: SentimentType | None = None
    instruments_affected: List[str] = Field(default_factory=list)
    matched_topics: List[str] = Field(default_factory=list)
    llm_processed: bool = False
    fetched_at: datetime
    published_at: datetime | None = None


class SummaryBatch(BaseModel):
    id: int
    window_seconds: int
    window_start: datetime
    window_end: datetime
    summary_text: str
    item_count: int
    sources: List[str] = Field(default_factory=list)
    source_channels: List[str] = Field(default_factory=list)
    created_at: datetime


class TopicCreate(BaseModel):
    topic_name: str = Field(min_length=2, max_length=200)
    keywords: List[str] = Field(default_factory=list, min_length=1)
    alert_urgency_threshold: UrgencyType = "MEDIUM"
    active: bool = True


class TopicUpdate(BaseModel):
    topic_name: str | None = Field(default=None, min_length=2, max_length=200)
    keywords: List[str] | None = None
    alert_urgency_threshold: UrgencyType | None = None
    active: bool | None = None


class TopicItem(BaseModel):
    id: int
    topic_name: str
    keywords: List[str]
    alert_urgency_threshold: UrgencyType
    active: bool
    created_at: datetime
    updated_at: datetime


class AlertProposalRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    message: str = Field(min_length=3, max_length=1500)
    model_id: str = "groq_gpt_oss"


class AlertProposalResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    topic_name: str
    keywords: List[str]
    alert_urgency_threshold: UrgencyType
    rationale: str = ""
    context_items: int = 0
    model_id: str
    model_label: str


class AlertItem(BaseModel):
    id: int
    news_id: int | None = None
    topic_id: int | None = None
    topic_name: str | None = None
    urgency: UrgencyType | None = None
    news_summary: str | None = None
    channel: str
    sent_at: datetime
    message_text: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    message: str = Field(min_length=2, max_length=1500)
    model_id: str = "groq_gpt_oss"
    timeframe_mode: str = "dynamic"
    start_time: str | None = None
    end_time: str | None = None
    enable_search: bool = True


class ChatModelOption(BaseModel):
    id: str
    label: str
    provider: str
    model: str


class ChatResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    answer: str
    used_news_items: int
    window_used: str
    model_id: str
    model_label: str
    month_buckets: dict[str, int] | None = None
    week_buckets: dict[str, int] | None = None
    day_buckets: dict[str, int] | None = None
    keywords_used: list[str] | None = None


class SummaryIntervalRequest(BaseModel):
    interval_seconds: int


class SummaryIntervalResponse(BaseModel):
    interval_seconds: int
    allowed_values: List[int]
    message: str


class LlmUsageItem(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    provider: str
    model_name: str
    api_key_label: str
    bucket_type: str
    bucket_start: datetime
    request_count: int
    updated_at: datetime


class ContextAlertCreate(BaseModel):
    context_description: str = Field(min_length=5)
    active: bool = True


class ContextAlertUpdate(BaseModel):
    context_description: str | None = Field(default=None, min_length=5)
    active: bool | None = None


class ContextAlertItem(BaseModel):
    id: int
    context_description: str
    active: bool
    created_at: datetime
    updated_at: datetime


class ContextAlertProposalRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=1000)


class ContextAlertProposalResponse(BaseModel):
    proposed_description: str


class PasscodeVerifyRequest(BaseModel):
    passcode: str


class ProxyToggleRequest(BaseModel):
    enabled: bool


class BypassVerifyRequest(BaseModel):
    token: str


class FcmRegisterRequest(BaseModel):
    fcm_token: str
    device_name: str | None = None


class FcmPreferencesRequest(BaseModel):
    fcm_token: str
    push_keyword: bool
    push_context: bool


