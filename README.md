# LiteLLM Chat Gateway

A multi-turn chat app with a **LiteLLM key pool**, **automatic fallback**, **response caching**,
**per-request token tracking** and **SQLite session storage**.

```
React (Vite, nginx)  ->  FastAPI  ->  LiteLLM key pool  ->  provider
                            |              |
                            |              +-- LiteLLM cache (disk/memory)
                            +-- SQLite on a Docker volume (/data/app.db)
```

---

## 1. Live URLs

| Tier | URL |
| --- | --- |
| Frontend | `https://litellm-chat-gateway.vercel.app` |
| Backend | `https://litellm-chat-gateway.onrender.com` |
| Health | `https://litellm-chat-gateway.onrender.com/health` |
| Swagger UI | https://litellm-chat-gateway.onrender.com/docs |

>Both tiers are public and need no login. The backend runs on Render's free plan, so it
sleeps after 15 minutes idle - the first request can take up to a minute while the
instance wakes up, and every request after that is fast.

`DEPLOY.md` has the step-by-step guide for redeploying either tier.

---

## 2. Run locally

### With Docker (recommended)

```bash
cp .env.example .env          # then put at least 2 real API keys in LLM_API_KEYS
make up                       # builds and starts both services
```

| Service | URL |
| --- | --- |
| Backend | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| Frontend | http://localhost:3000 |

```bash
make logs      # follow both services
make down      # stop (keeps the SQLite volume)
make clean     # stop and delete the volume
make test      # run the backend test suite
```

`make up` creates `.env` from `.env.example` on the first run, so it never fails on a
fresh clone. The SQLite file lives on the named volume `litellm-chat-data` mounted at
`/data`, so **history survives `make down && make up`**.

### Without Docker

Backend:

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

export LLM_API_KEYS="key-one,key-two"
export LLM_MODELS="groq/openai/gpt-oss-20b"
export DATABASE_URL="sqlite:///./app.db"
export ALLOWED_ORIGINS="http://localhost:5173,http://localhost:3000"
export ENABLE_FAILURE_SIMULATION=true
export CACHE_TYPE=disk CACHE_DIR=./.cache

uvicorn app.main:app --reload --port 8000
pytest                         # 50+ tests, no API key needed
```

Frontend:

```bash
cd frontend
npm install
echo "VITE_API_BASE_URL=http://localhost:8000" > .env
npm run dev                    # http://localhost:3000
```

---

## 3. Design decisions

### Key fallback

* `LLM_API_KEYS` is an ordered pool. Ids come from the position in the list:
  `key_1`, `key_2`, ... The reply never contains the key itself, only its id.
* `LLM_MODELS` can hold one model (every key uses it) or one model per key
  (`key_1 -> model_1`, `key_2 -> model_2`), so the pool can span several providers.
* A request walks the pool in order. A `429`, a quota/billing error, any `5xx`, a
  timeout, a transport error, a dead key (`401/403`) or an empty completion moves the
  request to the next key. `fallback_attempts` counts the keys that failed first and
  `key_id` names the one that answered.
* The walk is written explicitly instead of handing it to `litellm.Router`, because the
  API contract requires the exact key that answered and the exact number of failures,
  and because `X-Simulate-Failure` has to behave deterministically. Router's internal
  cooldowns and shuffling hide both.
* When every key fails the API answers `503 {"detail": "all providers unavailable"}`.
  The user never sees a stack trace: FastAPI handlers turn `HTTPException`, validation
  errors and anything unexpected into JSON.

### Caching

* LiteLLM's own cache (`litellm.Cache`) is used, `disk` by default (it lives on the
  same volume as the database, so hits survive restarts) and `memory` when
    `CACHE_TYPE=memory`. Newer LiteLLM builds dropped the built-in `disk` type, so
  `CACHE_TYPE=disk` falls back to a `diskcache` backend behind the same interface, and
  then to in-memory - the app degrades, it never fails to start. `/health` reports which
  backend is live (`cache_backend`).
* The cache key is a SHA-256 of **model + the full message list sent to the LLM**
  (system prompt + history + new message). Two different messages can therefore never
  collide, and the same first message in a brand-new session hits the cache the second
  time. A different system prompt is a different key.
* A hit returns the identical reply text with the token usage stored next to it,
  `resolution_source = LLM_CACHE`, `key_id = null`, `fallback_attempts = 0`, and no
  provider call at all - which is why it comes back in single-digit milliseconds.
* A request that carries `X-Simulate-Failure` **bypasses the cache on purpose**, so the
  key pool is really exercised during a demo instead of being short-circuited.

### SQLite session storage

* One file, no external services. `DATABASE_URL` points at it
  (`sqlite:////data/app.db` inside Docker) and the schema is created on startup.
* `messages` is the required minimal model (`id`, `session_id`, `role`, `content`,
  `metadata` JSON, `created_at` ISO-8601), plus an index on `(session_id, id)` so
  history reads stay cheap. A small `sessions` table remembers the per-session system
  prompt.
* The client sends only the new message; the backend reloads history from SQLite,
  prepends the system prompt and sends the whole list to LiteLLM, which is what makes
  the bot remember earlier turns. `MAX_HISTORY_MESSAGES` caps how far back it looks so
  a long chat cannot blow up the prompt.
* A user message and its reply are written in one transaction, **after** a successful
  resolution - a `422` or a `503` leaves the history untouched.
* WAL mode is on and every call uses its own short-lived connection, so FastAPI's
  thread pool cannot hit `SQLite objects created in a thread...` errors.

---

## 4. Verification

### Health

```bash
curl -s localhost:8000/health
# {"status":"ok","keys_configured":2,"cache_enabled":true,...}
```

### Cache hit (`LLM_CACHE`)

Send the same first message in two different sessions:

```bash
curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-cache-1","message":"What is the capital of France?"}' | jq .metadata
# resolution_source: LIVE_INFERENCE, key_id: key_1, latency_ms: ~800

curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-cache-2","message":"What is the capital of France?"}' | jq .metadata
# resolution_source: LLM_CACHE, key_id: null, latency_ms: ~3
```

The reply text is identical and the second call is two orders of magnitude faster.

### Failover (`X-Simulate-Failure`)

Needs `ENABLE_FAILURE_SIMULATION=true` (set on the deployed backend).

```bash
# key_1 answers with 429 -> key_2 takes over
curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"s-f1","message":"ping"}' | jq .metadata
# key_id: "key_2", fallback_attempts: 1

# a 5xx behaves the same way
curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=500' \
  -d '{"session_id":"s-f2","message":"ping"}' | jq '.metadata.key_id'

# every key down -> clean 503, nothing stored
curl -i -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"s-f3","message":"ping"}'
# HTTP/1.1 503   {"detail":"all providers unavailable"}

curl -i -s localhost:8000/sessions/s-f3/messages   # 404 session not found
```

In the UI the same thing is one click: the **Failover demo** panel sets the header, and
the reply shows `key_2` and a `1 fallback` chip.

### Per-request token usage

Every reply in the UI carries its own metadata row - source, model, latency,
`in <input_tokens>`, `out <output_tokens>` - and these are **per request**, never a
conversation total. The same numbers come back from the API and are stored with the
assistant message:

```bash
curl -s localhost:8000/sessions/s-cache-1/messages | jq '.messages[1].metadata'
```

Two equal-length first messages in two new sessions report nearly identical
`input_tokens`; a second turn is larger because history is replayed, while
`output_tokens` always describes that one reply.

### Multi-turn memory

```bash
curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-mem","message":"My name is Harshit."}' > /dev/null
curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-mem","message":"What is my name?"}' | jq -r .reply
```

### Validation

```bash
curl -i -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-1","message":""}'          # 422
curl -i -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{not json'                                   # 422
```

### Test suite

```bash
make test            # or: cd backend && pytest
```

Covers the metadata contract, cache labelling and speed, token accounting, multi-turn
replay, session isolation, the history endpoint and its 404, every `422` case,
simulated and real 429/quota/5xx failover, the `*=429` 503 path, and that keys are
never leaked.

---

## 5. Setup Used

* **Editor:** VS Code.
* **AI tools:** Claude (Anthropic) for scaffolding the FastAPI + LiteLLM gateway, the
  React UI and the test suite; GitHub Copilot for small in-editor completions.
* **Stack:** FastAPI, LiteLLM, SQLite (stdlib `sqlite3`), React 18 + Vite, nginx,
  Docker Compose, pytest.
* Every AI-generated piece was reviewed by hand, and the behaviour in section 4 was
  checked against the running stack.

---

## 6. Bonus features

| Feature | Why |
| --- | --- |
| **Failover demo panel** in the UI | One click sets `X-Simulate-Failure`, so failover can be shown without burning real quota. |
| **`key_id` and fallback chips** on each reply | The pool's behaviour is visible, not just logged. |
| **Live `/health` panel** | Shows `keys_configured`, cache backend and models, so a misconfigured deployment is obvious at a glance. |
| **Disk cache on the volume** | Cache hits survive a restart, like the history does. |
| **Runtime `API_BASE_URL` injection** | The same frontend image can point at a local or a deployed backend without rebuilding. |
| **Multi-provider pool** | `LLM_MODELS` can pair a different model with each key, so fallback can cross providers (Gemini -> Groq). |
| **Per-session system prompt** | Stored in SQLite, so it keeps applying to later turns of that session. |
| **Empty-completion guard** | A 200 with no text is treated as a failure and the next key is tried. |
| **Swagger UI at `/docs`** | The contract is explorable without curl. |
| **Health checks, non-root runtime user, masked keys in logs** | Operational hygiene. |

---

## API contract

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | `{"status":"ok","keys_configured":2,"cache_enabled":true}` |
| `POST /chat` | `{session_id, message, system_prompt?}` -> `{session_id, reply, metadata}` |
| `GET /sessions/{session_id}/messages` | stored history, oldest first, no system prompt |

`metadata`: `resolution_source` (`LIVE_INFERENCE` / `LLM_CACHE`), `model`, `latency_ms`,
`input_tokens`, `output_tokens`, `key_id` (null on a cache hit), `fallback_attempts`.

Errors: `422` on invalid input, `503 {"detail":"all providers unavailable"}` when every
key fails, `404 {"detail":"session not found"}` for an unknown session. Always JSON.

### Failover test hook

`X-Simulate-Failure: key_1=429` | `key_1=429,key_2=500` | `*=429`
(statuses `429`, `500`, `502`, `503`; honoured only when `ENABLE_FAILURE_SIMULATION=true`).

## Repository layout

```
backend/           FastAPI app (config, db, llm gateway, schemas) + tests
frontend/          React + Vite UI, nginx runtime image
docker-compose.yml both services + the named /data volume
Makefile           up, down, logs, build, test, clean
.env.example       every variable with placeholder values
DEPLOY.md          Render + Vercel walkthrough and the demo-video script
```

## Environment variables

| Variable | Meaning |
| --- | --- |
| `LLM_API_KEYS` | Comma-separated key pool, at least 2 keys. Ids are `key_1`, `key_2`, ... in this order. |
| `LLM_MODELS` | One LiteLLM model for the whole pool, or one per key. |
| `DATABASE_URL` | SQLite location, e.g. `sqlite:////data/app.db`. |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins. |
| `ENABLE_FAILURE_SIMULATION` | `true` enables the `X-Simulate-Failure` hook. |
| `CACHE_TYPE` / `CACHE_DIR` / `CACHE_TTL_SECONDS` | LiteLLM cache backend, location and TTL. |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_OUTPUT_TOKENS` | Per-call budget. |
| `MAX_HISTORY_MESSAGES` | How many stored messages are replayed to the model. |
| `DEFAULT_SYSTEM_PROMPT` / `LOG_LEVEL` | Defaults and logging. |
| `VITE_API_BASE_URL` | Frontend: base URL of the backend. |
