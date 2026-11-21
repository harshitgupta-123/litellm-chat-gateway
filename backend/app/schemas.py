"""Request/response models. Validation failures surface as HTTP 422."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_SESSION_ID_LENGTH = 128
MAX_MESSAGE_LENGTH = 4000
MAX_SYSTEM_PROMPT_LENGTH = 8000


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str = Field(
        ..., description="Client generated session id, 1-128 characters"
    )
    message: str = Field(..., description="User message, 1-4000 characters")
    system_prompt: str | None = Field(
        default=None, description="Overrides the default system prompt for this session"
    )

    @field_validator("session_id")
    @classmethod
    def _validate_session_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("session_id must not be empty")
        if len(value) > MAX_SESSION_ID_LENGTH:
            raise ValueError(
                f"session_id must be 1-{MAX_SESSION_ID_LENGTH} characters"
            )
        return value

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be empty")
        if len(value) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"message must be 1-{MAX_MESSAGE_LENGTH} characters")
        return value

    @field_validator("system_prompt")
    @classmethod
    def _validate_system_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if len(value) > MAX_SYSTEM_PROMPT_LENGTH:
            raise ValueError(
                f"system_prompt must be at most {MAX_SYSTEM_PROMPT_LENGTH} characters"
            )
        return value


class ChatMetadata(BaseModel):
    resolution_source: Literal["LIVE_INFERENCE", "LLM_CACHE"]
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: str | None = None
    fallback_attempts: int = 0
    # extra, non-contract fields that make the demo easier to narrate
    cache_backend: str | None = None
    provider_model: str | None = None
    simulated_failures: dict[str, int] | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata


class StoredMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    metadata: dict[str, Any] | None = None
    created_at: str | None = None


class SessionHistoryResponse(BaseModel):
    session_id: str
    messages: list[StoredMessage]


class HealthResponse(BaseModel):
    status: str
    keys_configured: int
    cache_enabled: bool
    # extra diagnostics
    database: str | None = None
    cache_backend: str | None = None
    models: list[str] | None = None
    failure_simulation_enabled: bool | None = None
