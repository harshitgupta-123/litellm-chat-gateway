"""LiteLLM gateway: key pool, automatic fallback, caching and token tracking.

Design notes
------------
* LiteLLM is the single gateway to every provider, so a model name such as
  ``gemini/gemini-2.0-flash`` or ``groq/llama-3.1-8b-instant`` works unchanged.
* The key pool is walked in order (``key_1``, ``key_2``, ...). On a 429, a quota
  error or any 5xx/transport error the next key is tried. The walk is explicit
  rather than delegated to ``litellm.Router`` because the API contract requires
  the exact key that answered (``key_id``) and the exact number of keys that
  failed first (``fallback_attempts``), plus deterministic behaviour for the
  ``X-Simulate-Failure`` test hook.
* Caching uses LiteLLM's built-in cache (``litellm.Cache``, disk or in-memory)
  with a deterministic key of ``model + the full message list`` that was sent to
  the LLM (system prompt + history + new message).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import litellm

from .config import Provider, Settings

try:  # LiteLLM moved the Cache class around between releases
    from litellm.caching.caching import Cache  # type: ignore
except Exception:  # pragma: no cover - older layouts
    try:
        from litellm.caching import Cache  # type: ignore
    except Exception:  # pragma: no cover
        from litellm import Cache  # type: ignore

logger = logging.getLogger("app.llm")

LIVE_INFERENCE = "LIVE_INFERENCE"
LLM_CACHE = "LLM_CACHE"

CACHE_KEY_PREFIX = "chat:v1"
SIMULATED_STATUSES = {429, 500, 502, 503}

# Error text that means "this key is spent / the provider is wobbling" and the
# request should be retried on the next key.
_FALLBACK_HINTS = (
    "rate limit",
    "ratelimit",
    "too many requests",
    "quota",
    "resource_exhausted",
    "resource has been exhausted",
    "insufficient_quota",
    "billing",
    "overloaded",
    "capacity",
    "unavailable",
    "service_unavailable",
    "internal server error",
    "bad gateway",
    "timeout",
    "timed out",
    "connection",
    "429",
    "500",
    "502",
    "503",
    "504",
)


class SimulatedProviderFailure(Exception):
    """Raised by the X-Simulate-Failure hook instead of calling the provider."""

    def __init__(self, key_id: str, status_code: int) -> None:
        self.status_code = status_code
        self.key_id = key_id
        super().__init__(f"simulated HTTP {status_code} from provider for {key_id}")


class EmptyCompletion(Exception):
    """The provider answered 200 but with no usable text."""


class _MemoryBackend:
    """Last-resort in-memory backend with LiteLLM's cache-backend interface."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def set_cache(self, key: str, value: Any, **kwargs: Any) -> None:
        self._store[key] = value

    def get_cache(self, key: str, **kwargs: Any) -> Any:
        return self._store.get(key)


class _DiskBackend:
    """``diskcache`` backend exposing LiteLLM's cache-backend interface.

    LiteLLM dropped its built-in ``disk`` cache type in newer releases, so this
    keeps the "cache survives a restart" behaviour on those versions. The
    gateway talks to both through the same two methods.
    """

    def __init__(self, directory: str, ttl: int) -> None:
        import diskcache  # imported here so a missing package degrades cleanly

        self._store = diskcache.Cache(directory)
        self._ttl = ttl

    def set_cache(self, key: str, value: Any, **kwargs: Any) -> None:
        self._store.set(key, value, expire=kwargs.get("ttl", self._ttl))

    def get_cache(self, key: str, **kwargs: Any) -> Any:
        return self._store.get(key)


class _CacheHandle:
    """Mirrors ``litellm.Cache``, whose backend hangs off ``.cache``."""

    def __init__(self, backend: Any) -> None:
        self.cache = backend


@dataclass
class Attempt:
    key_id: str
    model: str
    error_type: str
    error: str
    status_code: int | None = None
    simulated: bool = False


@dataclass
class Resolution:
    """Everything the API layer needs to answer one /chat request."""

    ok: bool
    reply: str = ""
    model: str = ""
    resolution_source: str = LIVE_INFERENCE
    input_tokens: int = 0
    output_tokens: int = 0
    key_id: str | None = None
    fallback_attempts: int = 0
    provider_model: str | None = None
    cached_at: str | None = None
    failed_attempts: list[Attempt] = field(default_factory=list)
    error: str | None = None


def parse_simulation_header(raw: str | None) -> dict[str, int]:
    """Parse ``X-Simulate-Failure: key_1=429,key_2=500`` / ``*=429``.

    Unknown statuses and malformed pairs are ignored, so a bad header can never
    break a request.
    """
    plan: dict[str, int] = {}
    if not raw:
        return plan
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        target, _, status_text = chunk.partition("=")
        target = target.strip().lower()
        try:
            status = int(status_text.strip())
        except ValueError:
            continue
        if not target or status not in SIMULATED_STATUSES:
            continue
        plan[target] = status
    return plan


def _status_code(exc: BaseException) -> int | None:
    for attribute in ("status_code", "code", "http_status"):
        value = getattr(exc, attribute, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    response = getattr(exc, "response", None)
    try:
        if response is not None and getattr(response, "status_code", None):
            return int(response.status_code)
    except (TypeError, ValueError):
        pass
    return None


def is_fallback_error(exc: BaseException) -> bool:
    """True when the error means 'try the next key'."""
    status = _status_code(exc)
    if status is not None:
        if status in (408, 409, 425, 429) or status >= 500:
            return True
        if status in (401, 403):  # dead or revoked key -> the pool should move on
            return True
    message = str(exc).lower()
    return any(hint in message for hint in _FALLBACK_HINTS)


def _get(obj: Any, name: str) -> Any:
    """Read ``name`` from an object or a mapping (LiteLLM returns both shapes)."""
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if value is not None:
        return value
    if isinstance(obj, Mapping):
        return obj.get(name)
    try:
        return obj[name]  # type: ignore[index]
    except Exception:
        return None


def extract_reply(response: Any) -> str:
    choices = _get(response, "choices") or []
    if not choices:
        return ""
    message = _get(choices[0], "message")
    content = _get(message, "content")
    if content is None:
        content = _get(choices[0], "text")
    if isinstance(content, list):  # some providers return content blocks
        parts = [str(_get(part, "text") or "") for part in content]
        content = "".join(parts)
    return str(content or "")


def extract_usage(response: Any) -> tuple[int | None, int | None]:
    usage = _get(response, "usage")
    if usage is None:
        return None, None

    def as_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return as_int(_get(usage, "prompt_tokens")), as_int(
        _get(usage, "completion_tokens")
    )


class LLMGateway:
    def __init__(
        self,
        settings: Settings,
        completion_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self.providers: list[Provider] = settings.providers
        self._completion_fn = completion_fn or litellm.completion
        self.cache, self.cache_enabled, self.cache_backend = self._build_cache()

    # ------------------------------------------------------------------- cache
    def _build_cache(self) -> tuple[Any, bool, str]:
        """Pick a cache backend, degrading instead of failing.

        ``disk`` -> LiteLLM's built-in disk cache, else a ``diskcache`` backend
        with the same interface, else in-memory. ``memory`` -> LiteLLM's local
        (in-memory) cache.
        """
        wanted = (self.settings.cache_type or "disk").lower()
        if wanted in {"off", "none", "disabled"}:
            logger.info("LLM cache disabled by configuration")
            return None, False, "disabled"

        if wanted == "disk":
            try:
                cache = Cache(
                    type="disk",
                    disk_cache_dir=self.settings.cache_dir,
                    ttl=self.settings.cache_ttl_seconds,
                )
                if getattr(cache, "cache", None) is None:
                    raise RuntimeError("cache backend not initialised")
                logger.info("LLM cache enabled (litellm disk cache)")
                return cache, True, "disk"
            except Exception as exc:
                logger.warning("litellm disk cache unavailable: %s", exc)

            try:
                handle = _CacheHandle(
                    _DiskBackend(
                        self.settings.cache_dir, self.settings.cache_ttl_seconds
                    )
                )
                logger.info(
                    "LLM cache enabled (diskcache at %s)", self.settings.cache_dir
                )
                return handle, True, "disk"
            except Exception as exc:
                logger.warning("diskcache unavailable: %s", exc)

        try:
            cache = Cache(type="local", ttl=self.settings.cache_ttl_seconds)
            if getattr(cache, "cache", None) is None:
                raise RuntimeError("cache backend not initialised")
            logger.info("LLM cache enabled (litellm in-memory cache)")
            return cache, True, "memory"
        except Exception as exc:
            logger.warning("litellm in-memory cache unavailable: %s", exc)

        logger.info("LLM cache enabled (built-in in-memory fallback)")
        return _CacheHandle(_MemoryBackend()), True, "memory"

    def cache_key(self, model: str, messages: Sequence[Mapping[str, Any]]) -> str:
        payload = json.dumps(
            {"model": model, "messages": list(messages)},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"{CACHE_KEY_PREFIX}:{digest}"

    def candidate_models(self) -> list[str]:
        seen: list[str] = []
        for provider in self.providers:
            if provider.model not in seen:
                seen.append(provider.model)
        return seen

    def cache_lookup(
        self, messages: Sequence[Mapping[str, Any]]
    ) -> tuple[str, dict[str, Any]] | None:
        if not self.cache_enabled or self.cache is None:
            return None
        for model in self.candidate_models():
            key = self.cache_key(model, messages)
            try:
                value = self.cache.cache.get_cache(key)
            except Exception as exc:  # pragma: no cover
                logger.warning("cache read failed: %s", exc)
                return None
            if not value:
                continue
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    continue
            if isinstance(value, dict) and value.get("reply"):
                return model, value
        return None

    def cache_store(
        self, model: str, messages: Sequence[Mapping[str, Any]], payload: dict[str, Any]
    ) -> None:
        if not self.cache_enabled or self.cache is None:
            return
        key = self.cache_key(model, messages)
        try:
            self.cache.cache.set_cache(key, payload, ttl=self.settings.cache_ttl_seconds)
        except TypeError:  # backends whose set_cache has no ttl kwarg
            try:
                self.cache.cache.set_cache(key, payload)
            except Exception as exc:  # pragma: no cover
                logger.warning("cache write failed: %s", exc)
        except Exception as exc:  # pragma: no cover
            logger.warning("cache write failed: %s", exc)

    # ------------------------------------------------------------------ tokens
    def count_tokens(
        self,
        model: str,
        messages: Sequence[Mapping[str, Any]] | None = None,
        text: str | None = None,
    ) -> int:
        try:
            if messages is not None:
                return int(litellm.token_counter(model=model, messages=list(messages)))
            return int(litellm.token_counter(model=model, text=text or ""))
        except Exception:
            # Rough fallback (~4 characters per token) so the field is never wrong-typed
            blob = text or json.dumps(list(messages or []))
            return max(1, len(blob) // 4)

    # ----------------------------------------------------------------- resolve
    def resolve(
        self,
        messages: Sequence[Mapping[str, Any]],
        simulate: Mapping[str, int] | None = None,
        bypass_cache: bool = False,
    ) -> Resolution:
        simulate = {k.lower(): v for k, v in (simulate or {}).items()}

        if not bypass_cache:
            hit = self.cache_lookup(messages)
            if hit is not None:
                model, payload = hit
                return Resolution(
                    ok=True,
                    reply=str(payload.get("reply", "")),
                    model=str(payload.get("model") or model),
                    resolution_source=LLM_CACHE,
                    input_tokens=int(payload.get("input_tokens") or 0),
                    output_tokens=int(payload.get("output_tokens") or 0),
                    key_id=None,
                    fallback_attempts=0,
                    provider_model=payload.get("provider_model"),
                    cached_at=payload.get("created_at"),
                )

        failures: list[Attempt] = []
        for provider in self.providers:
            simulated_status = simulate.get(provider.key_id) or simulate.get("*")
            try:
                if simulated_status:
                    raise SimulatedProviderFailure(provider.key_id, simulated_status)

                response = self._completion_fn(
                    model=provider.model,
                    messages=list(messages),
                    api_key=provider.api_key,
                    timeout=self.settings.request_timeout_seconds,
                    max_tokens=self.settings.max_output_tokens,
                    num_retries=0,
                )
                reply = extract_reply(response)
                if not reply.strip():
                    raise EmptyCompletion("provider returned an empty completion")

                input_tokens, output_tokens = extract_usage(response)
                if input_tokens is None:
                    input_tokens = self.count_tokens(provider.model, messages=messages)
                if output_tokens is None:
                    output_tokens = self.count_tokens(provider.model, text=reply)
                provider_model = _get(response, "model")

                payload = {
                    "reply": reply,
                    "model": provider.model,
                    "provider_model": str(provider_model) if provider_model else None,
                    "input_tokens": int(input_tokens),
                    "output_tokens": int(output_tokens),
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                self.cache_store(provider.model, messages, payload)

                return Resolution(
                    ok=True,
                    reply=reply,
                    model=provider.model,
                    resolution_source=LIVE_INFERENCE,
                    input_tokens=int(input_tokens),
                    output_tokens=int(output_tokens),
                    key_id=provider.key_id,
                    fallback_attempts=len(failures),
                    provider_model=payload["provider_model"],
                    failed_attempts=list(failures),
                )
            except Exception as exc:
                status = _status_code(exc)
                attempt = Attempt(
                    key_id=provider.key_id,
                    model=provider.model,
                    error_type=type(exc).__name__,
                    error=str(exc)[:300],
                    status_code=status,
                    simulated=isinstance(exc, SimulatedProviderFailure),
                )
                failures.append(attempt)
                logger.warning(
                    "provider %s (%s) failed: %s %s - falling over to the next key",
                    provider.key_id,
                    provider.model,
                    attempt.error_type,
                    attempt.error,
                )
                continue

        if not self.providers:
            logger.error("no API keys configured - set LLM_API_KEYS")

        return Resolution(
            ok=False,
            resolution_source=LIVE_INFERENCE,
            fallback_attempts=len(failures),
            failed_attempts=failures,
            error=failures[-1].error if failures else "no API keys configured",
        )