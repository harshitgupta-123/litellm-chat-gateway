# Deploy guide (backend on Render, frontend on Vercel)

Total time: about 20 minutes. Both tiers are free.

---

## Step 0 - get at least 2 API keys

The key pool needs 2+ keys. Easiest free options:

* **Google AI Studio** (`https://aistudio.google.com/apikey`) - create **two** keys in two
  different Google projects. Model name: `gemini/gemini-2.0-flash`.
* **Groq** (`https://console.groq.com/keys`) - free and fast. Model name:
  `groq/llama-3.1-8b-instant`.

Mixing providers is supported, and it makes the fallback story stronger:

```
LLM_API_KEYS=<gemini-key>,<groq-key>
LLM_MODELS=gemini/gemini-2.0-flash,groq/llama-3.1-8b-instant
```

Check it locally first:

```bash
cp .env.example .env     # paste the keys into LLM_API_KEYS
make up
curl -s localhost:8000/health
open http://localhost:3000
```

---

## Step 1 - push to GitHub

```bash
cd litellm-chat-gateway
git init
git add .
git commit -m "LiteLLM chat gateway: FastAPI + React + SQLite"
git branch -M main
git remote add origin https://github.com/<you>/litellm-chat-gateway.git
git push -u origin main
```

`.env` is git-ignored - confirm with `git status` that it is not staged.

---

## Step 2 - backend on Render

1. `https://dashboard.render.com` -> **New** -> **Web Service** -> connect the repo.
2. Settings:
   * **Language / Runtime:** `Docker`
   * **Root Directory:** `backend`
   * **Dockerfile Path:** `./Dockerfile`
   * **Instance Type:** `Free`
   * **Health Check Path:** `/health`
3. **Environment variables:**

   | Key | Value |
   | --- | --- |
   | `LLM_API_KEYS` | `<key-one>,<key-two>` |
   | `LLM_MODELS` | `gemini/gemini-2.0-flash` |
   | `DATABASE_URL` | `sqlite:////data/app.db` |
   | `ALLOWED_ORIGINS` | `http://localhost:3000` (updated in step 4) |
   | `ENABLE_FAILURE_SIMULATION` | `true` |
   | `CACHE_TYPE` | `disk` |
   | `CACHE_DIR` | `/data/cache` |

4. **Create Web Service** and wait for the build.
5. Verify:

   ```bash
   curl -s https://<backend>.onrender.com/health
   # {"status":"ok","keys_configured":2,"cache_enabled":true,...}
   ```

> The repo also has `render.yaml`, so **New -> Blueprint** does steps 2-4 in one go;
> you only paste `LLM_API_KEYS` and `ALLOWED_ORIGINS`.

Two notes about the free plan:

* It sleeps after 15 minutes idle, so the first request can take ~50 s. Warm it up
  before recording the demo video.
* Persistent disks need a paid instance, so on free the SQLite file resets on redeploy.
  History still survives restarts locally (the compose volume) and in production if you
  attach a 1 GB disk at `/data` (uncomment the `disk:` block in `render.yaml`) or deploy
  with `fly.toml`, which mounts a real volume.

---

## Step 3 - frontend on Vercel

1. `https://vercel.com/new` -> import the same repo.
2. Settings:
   * **Framework Preset:** `Vite`
   * **Root Directory:** `frontend`
   * **Build Command:** `npm run build` (default)
   * **Output Directory:** `dist` (default)
3. **Environment variable:**

   | Key | Value |
   | --- | --- |
   | `VITE_API_BASE_URL` | `https://<backend>.onrender.com` |

4. **Deploy**, then open the URL. The side panel should show `status ok` and
   `keys configured 2` - that means both tiers are talking to each other.

> Changing `VITE_API_BASE_URL` later needs a **redeploy**, because Vite inlines it at
> build time.

---

## Step 4 - connect the two tiers (CORS)

Back on Render, set:

```
ALLOWED_ORIGINS=https://<frontend>.vercel.app,http://localhost:3000
```

Save - Render redeploys. Without this the browser blocks the calls and the UI shows
"Cannot reach the backend".

---

## Step 5 - final check on the live URLs

```bash
BACKEND=https://<backend>.onrender.com

curl -s $BACKEND/health

# live inference
curl -s -X POST $BACKEND/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-demo","message":"What is the capital of France?"}'

# same message in a new session -> LLM_CACHE
curl -s -X POST $BACKEND/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-demo-2","message":"What is the capital of France?"}'

# failover
curl -s -X POST $BACKEND/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"s-demo-3","message":"ping"}'

# everything down
curl -i -s -X POST $BACKEND/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: *=429' -d '{"session_id":"s-demo-4","message":"ping"}'

# history
curl -s $BACKEND/sessions/s-demo/messages
```

---

## Step 6 - demo video script (2-3 minutes)

Record the screen with your voice. Suggested run:

**0:00-0:20 - what it is.** Show the live frontend. "React frontend on Vercel, FastAPI
backend on Render, LiteLLM gateway with a two-key pool, SQLite for history."

**0:20-0:50 - multi-turn + metadata.** Send "My name is Harshit." then "What is my
name?". Point at the metadata row: `LIVE_INFERENCE`, model, latency, input/output
tokens, `key_1`. Say that the tokens are per request, not a running total.

**0:50-1:20 - cache hit.** Click **Start a new session**, send the exact same first
question. Point at `LLM_CACHE`, the identical reply, and the latency dropping from
hundreds of milliseconds to single digits.

**1:20-1:50 - failover.** In the **Failover demo** panel click `key_1=429`, send a
message: the reply now shows `key_2` and `1 fallback`. Then click `*=429`: a readable
error bubble with `HTTP 503`, no broken screen.

**1:50-2:20 - the endpoints.** In a terminal: `/health`, the cache-hit pair, the
`X-Simulate-Failure` curl with `key_id: key_2`, the `*=429` 503, and
`/sessions/{id}/messages` showing stored history with per-message metadata.

**2:20-2:45 - storage and local run.** Mention SQLite on the `/data` volume, then show
`make up` bringing both tiers up on `localhost:8000` / `localhost:3000`, and
`cd backend && pytest` passing.

Tip: warm the Render service up right before recording, and keep `jq` in the curl
pipeline so the JSON is readable on camera.

---

## Submission checklist

- [ ] Frontend URL public, no login
- [ ] Backend URL public, `/health` returns 200 with `keys_configured >= 2`
- [ ] `ENABLE_FAILURE_SIMULATION=true` on the deployed backend
- [ ] `ALLOWED_ORIGINS` contains the Vercel URL
- [ ] Repo public: `backend/`, `frontend/`, `docker-compose.yml`, `Makefile`,
      `.env.example`, `README.md`, tests
- [ ] No real keys anywhere in the repo history
- [ ] Live URLs filled into README section 1
- [ ] Demo video (2-3 min, with voice) showing a cache hit and failover
