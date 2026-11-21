"""FastAPI application: /health, /chat and /sessions/{id}/messages."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import load_settings
from .db import Database
from .llm import LLMGateway, parse_simulation_header
from .schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    SessionHistoryResponse,
)

settings = load_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app")

db = Database(settings.sqlite_path)
gateway = LLMGateway(settings)

ALL_PROVIDERS_UNAVAILABLE = "all providers unavailable"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()  # schema is created automatically on startup
    logger.info(
        "startup: db=%s keys=%d models=%s cache=%s simulation=%s",
        settings.sqlite_path,
        len(gateway.providers),
        ",".join(gateway.candidate_models()),
        gateway.cache_backend,
        settings.enable_failure_simulation,
    )
    yield


app = FastAPI(
    title="LiteLLM Chat Gateway",
    version="1.0.0",
    description=(
        "Multi-turn chat backend with a LiteLLM key pool, automatic fallback, "
        "response caching, per-request token tracking and SQLite session storage."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------- errors
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: StarletteHTTPException):
    detail = exc.detail if exc.detail else "request failed"
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    # FastAPI's default 422 shape, guaranteed to be JSON serialisable
    return JSONResponse(
        status_code=422,
        content={"detail": [_serialise_error(error) for error in exc.errors()]},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception):
    logger.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


def _serialise_error(error: dict) -> dict:
    cleaned = {
        "type": error.get("type"),
        "loc": list(error.get("loc", [])),
        "msg": error.get("msg"),
    }
    return cleaned


# --------------------------------------------------------------------- routes
@app.get("/health", response_model=HealthResponse)
def health() -> dict:
    if not db.ping():
        raise HTTPException(status_code=503, detail="database unavailable")
    if not gateway.providers:
        raise HTTPException(status_code=503, detail="no API keys configured")
    return {
        "status": "ok",
        "keys_configured": len(gateway.providers),
        "cache_enabled": gateway.cache_enabled,
        "database": "ok",
        "cache_backend": gateway.cache_backend,
        "models": gateway.candidate_models(),
        "failure_simulation_enabled": settings.enable_failure_simulation,
    }


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "service": "litellm-chat-gateway",
        "endpoints": ["/health", "/chat", "/sessions/{session_id}/messages", "/docs"],
    }


@app.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    x_simulate_failure: str | None = Header(default=None, alias="X-Simulate-Failure"),
) -> dict:
    started = time.perf_counter()
    request_id = uuid.uuid4().hex[:8]

    simulate: dict[str, int] = {}
    if settings.enable_failure_simulation:
        simulate = parse_simulation_header(x_simulate_failure)

    session_id = payload.session_id
    stored_prompt = db.get_system_prompt(session_id)
    system_prompt = (
        payload.system_prompt or stored_prompt or settings.default_system_prompt
    )

    history = db.get_history(session_id, limit=settings.max_history_messages)
    messages = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": payload.message}]
    )

    # When failure simulation is requested the cache is bypassed on purpose, so
    # the key pool is actually exercised instead of being short-circuited.
    resolution = gateway.resolve(
        messages, simulate=simulate, bypass_cache=bool(simulate)
    )

    if not resolution.ok:
        logger.error(
            "[%s] session=%s every key failed after %d attempt(s): %s",
            request_id,
            session_id,
            resolution.fallback_attempts,
            resolution.error,
        )
        # A failed request is never written to the session history.
        raise HTTPException(status_code=503, detail=ALL_PROVIDERS_UNAVAILABLE)

    latency_ms = int((time.perf_counter() - started) * 1000)
    metadata = {
        "resolution_source": resolution.resolution_source,
        "model": resolution.model,
        "latency_ms": latency_ms,
        "input_tokens": resolution.input_tokens,
        "output_tokens": resolution.output_tokens,
        "key_id": resolution.key_id,
        "fallback_attempts": resolution.fallback_attempts,
        "cache_backend": gateway.cache_backend,
        "provider_model": resolution.provider_model,
        "simulated_failures": simulate or None,
    }

    try:
        db.add_exchange(
            session_id=session_id,
            user_message=payload.message,
            assistant_reply=resolution.reply,
            metadata=metadata,
            system_prompt=payload.system_prompt,
        )
    except Exception as exc:  # storage must never break a good answer
        logger.exception("[%s] failed to persist session %s: %s", request_id, session_id, exc)

    logger.info(
        "[%s] session=%s source=%s key=%s fallbacks=%d latency=%dms tokens=%d/%d",
        request_id,
        session_id,
        resolution.resolution_source,
        resolution.key_id,
        resolution.fallback_attempts,
        latency_ms,
        resolution.input_tokens,
        resolution.output_tokens,
    )

    return {
        "session_id": session_id,
        "reply": resolution.reply,
        "metadata": metadata,
    }


@app.get("/sessions/{session_id}/messages", response_model=SessionHistoryResponse)
def session_messages(session_id: str) -> dict:
    session_id = (session_id or "").strip()
    if not session_id or not db.session_exists(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {"session_id": session_id, "messages": db.get_messages(session_id)}
