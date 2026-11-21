"""Test fixtures.

The environment is prepared before ``app.main`` is imported so the app points at
a throwaway SQLite file and an in-memory cache, and no real provider is called.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any

import pytest

_TMP_DIR = tempfile.mkdtemp(prefix="chat-gateway-tests-")

os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{_TMP_DIR}/test.db",
        "LLM_API_KEYS": "test-key-one,test-key-two",
        "LLM_MODELS": "gemini/gemini-2.0-flash",
        "ALLOWED_ORIGINS": "http://localhost:3000",
        "ENABLE_FAILURE_SIMULATION": "true",
        "CACHE_TYPE": "memory",
        "LOG_LEVEL": "ERROR",
        "DEFAULT_SYSTEM_PROMPT": "You are a test assistant.",
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

LIVE_CALL_DELAY = 0.05  # makes "cache hits are faster" measurable


class FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content: str, model: str, prompt_tokens: int) -> None:
        self.choices = [FakeChoice(content)]
        self.usage = FakeUsage(prompt_tokens, max(1, len(content) // 4))
        self.model = model.split("/")[-1]


class FakeProvider:
    """Stands in for ``litellm.completion``.

    ``fail[api_key]`` makes that key raise, which is how real quota/5xx errors
    are simulated without the X-Simulate-Failure hook.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail: dict[str, BaseException] = {}
        self.empty: set[str] = set()
        self.delay = LIVE_CALL_DELAY

    def __call__(self, model: str, messages: list[dict], api_key: str, **kwargs: Any):
        self.calls.append(
            {"model": model, "messages": messages, "api_key": api_key, "kwargs": kwargs}
        )
        error = self.fail.get(api_key)
        if error is not None:
            raise error
        if api_key in self.empty:
            return FakeResponse("", model, 1)
        if self.delay:
            time.sleep(self.delay)
        prompt_tokens = sum(len(m.get("content", "")) for m in messages) // 4 + 1
        last = messages[-1]["content"] if messages else ""
        # the reply never contains the key itself (see test_api_key_is_never_leaked)
        return FakeResponse(f"answer to: {last}", model, prompt_tokens)

    @property
    def last_messages(self) -> list[dict]:
        return self.calls[-1]["messages"]


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(message)


@pytest.fixture(autouse=True)
def fresh_state():
    """Empty database and empty cache before every test."""
    main.db.init()
    with main.db.connect() as conn:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM sessions")
    (
        main.gateway.cache,
        main.gateway.cache_enabled,
        main.gateway.cache_backend,
    ) = main.gateway._build_cache()
    yield


@pytest.fixture
def fake_llm(monkeypatch) -> FakeProvider:
    provider = FakeProvider()
    monkeypatch.setattr(main.gateway, "_completion_fn", provider)
    return provider


@pytest.fixture
def client(fake_llm) -> TestClient:
    with TestClient(main.app, raise_server_exceptions=False) as test_client:
        yield test_client
