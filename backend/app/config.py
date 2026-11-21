"""Application configuration, loaded from environment variables only."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

try:  # optional convenience for local runs: load a .env file if present
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


DEFAULT_MODEL = "gemini/gemini-2.0-flash"
DEFAULT_SYSTEM_PROMPT = (
    "You are a concise, helpful assistant. Answer clearly and stay on topic."
)
# Local development defaults (docker compose frontend + `npm run dev`).
DEFAULT_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _float(value: str | None, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def sqlite_path_from_url(database_url: str) -> str:
    """Turn a DATABASE_URL into a filesystem path.

    Accepts ``sqlite:////data/app.db`` (absolute), ``sqlite:///./app.db``
    (relative) and a bare path such as ``/data/app.db``.
    """
    url = (database_url or "").strip()
    had_scheme = False
    for prefix in ("sqlite+aiosqlite://", "sqlite://"):
        if url.startswith(prefix):
            url = url[len(prefix) :]
            had_scheme = True
            break
    if had_scheme:
        # what is left is either '//data/app.db' (absolute) or '/./app.db'
        if url.startswith("//"):
            url = url[1:]
        elif url.startswith("/"):
            url = url[1:]
    if not url:
        url = "app.db"
    return os.path.abspath(os.path.expanduser(url))


@dataclass(frozen=True)
class Provider:
    """One entry of the key pool: an API key bound to a LiteLLM model."""

    key_id: str
    api_key: str
    model: str

    def masked_key(self) -> str:
        if len(self.api_key) <= 8:
            return "****"
        return f"{self.api_key[:4]}...{self.api_key[-4:]}"


@dataclass(frozen=True)
class Settings:
    api_keys: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    database_url: str = "sqlite:////data/app.db"
    sqlite_path: str = "/data/app.db"
    allowed_origins: list[str] = field(default_factory=list)
    enable_failure_simulation: bool = False
    cache_type: str = "disk"  # 'disk' or 'memory'
    cache_dir: str = "/data/cache"
    cache_ttl_seconds: int = 60 * 60 * 24 * 7
    request_timeout_seconds: float = 30.0
    max_output_tokens: int = 1024
    max_history_messages: int = 40
    default_system_prompt: str = DEFAULT_SYSTEM_PROMPT
    log_level: str = "INFO"

    @property
    def providers(self) -> list[Provider]:
        """Build the ordered key pool: key_1, key_2, ... in env order.

        Pairing rules between LLM_API_KEYS and LLM_MODELS:
          * one model  -> every key uses that model
          * N models for N keys -> paired by position
          * otherwise  -> models are cycled over the keys
        """
        models = self.models or [DEFAULT_MODEL]
        pool: list[Provider] = []
        for index, api_key in enumerate(self.api_keys):
            if len(models) == 1:
                model = models[0]
            elif len(models) == len(self.api_keys):
                model = models[index]
            else:
                model = models[index % len(models)]
            pool.append(
                Provider(key_id=f"key_{index + 1}", api_key=api_key, model=model)
            )
        return pool


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    database_url = env.get("DATABASE_URL", "sqlite:////data/app.db")
    return Settings(
        api_keys=_split(env.get("LLM_API_KEYS")),
        models=_split(env.get("LLM_MODELS")) or [DEFAULT_MODEL],
        database_url=database_url,
        sqlite_path=sqlite_path_from_url(database_url),
        allowed_origins=_split(env.get("ALLOWED_ORIGINS")) or list(DEFAULT_ORIGINS),
        enable_failure_simulation=_bool(env.get("ENABLE_FAILURE_SIMULATION"), False),
        cache_type=(env.get("CACHE_TYPE") or "disk").strip().lower(),
        cache_dir=(env.get("CACHE_DIR") or "/data/cache").strip(),
        cache_ttl_seconds=_int(env.get("CACHE_TTL_SECONDS"), 60 * 60 * 24 * 7),
        request_timeout_seconds=_float(env.get("LLM_TIMEOUT_SECONDS"), 30.0),
        max_output_tokens=_int(env.get("LLM_MAX_OUTPUT_TOKENS"), 1024),
        max_history_messages=_int(env.get("MAX_HISTORY_MESSAGES"), 40),
        default_system_prompt=(
            env.get("DEFAULT_SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT
        ).strip()
        or DEFAULT_SYSTEM_PROMPT,
        log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper(),
    )
