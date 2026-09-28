# Deployment Guide

## Architecture recap

- **Frontend (Lovable):** deploy to **Vercel**. This is the right tool
  for it.
- **Backend (this FastAPI app):** deploy to **Render, Railway, or
  Fly.io**. NOT Vercel - it spawns real subprocesses and holds
  WebSocket connections open, which Vercel's serverless model does not
  support.
- **Local agent:** runs on each user's own machine. Not deployed
  anywhere - it's a script they run.

---

## 1. Supabase (Auth)

1. Create a project at supabase.com
2. In Lovable, connect Supabase (there's a native integration) and set
   up your login providers (Google, GitHub, email) in the Supabase
   dashboard under Authentication -> Providers
3. Get your JWT secret: Supabase dashboard -> Settings -> API -> JWT
   Settings -> copy "JWT Secret"
4. Set it as an environment variable on your BACKEND host (not
   frontend): `SUPABASE_JWT_SECRET=<value>`

## 2. GitHub OAuth App (for repo scanning)

1. Go to github.com/settings/developers -> "New OAuth App"
2. Application name: whatever you want
3. Homepage URL: your deployed frontend URL
4. Authorization callback URL: your frontend URL + a route like
   `/github/callback` (Lovable will redirect here after GitHub consent,
   then your frontend should POST the `code` query param it receives to
   your backend's `/auth/github/connect`)
5. After creating it, copy the Client ID and generate a Client Secret
6. Set on your backend host:
   ```
   GITHUB_OAUTH_CLIENT_ID=<client id>
   GITHUB_OAUTH_CLIENT_SECRET=<client secret>
   ```

## 3. Token encryption key

Generate one locally:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Set the output as an environment variable on your backend host:
```
GITHUB_TOKEN_ENCRYPTION_KEY=<generated key>
```

## 4. Deploy the backend (Render example - Railway/Fly.io are similar)

1. Push this repo to GitHub
2. Render dashboard -> New -> Web Service -> connect your repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn src.api.main:app --host 0.0.0.0 --port $PORT`
5. Add all the environment variables from steps 1-3 above
6. Deploy. Note the resulting URL (e.g. `https://your-app.onrender.com`)

## 5. Deploy the frontend (Vercel)

1. Connect your Lovable project's repo to Vercel (or use Lovable's own
   one-click deploy, which uses Vercel under the hood)
2. Set environment variables in Vercel for:
   - Your Supabase project URL and anon key (from Supabase dashboard)
   - Your backend's URL from step 4 (`https://your-app.onrender.com`)
3. Deploy

## 6. The local agent - what your users need to do

Every user who wants to actually RUN tasks (not just browse) needs to:
1. Have Python 3.10+ installed
2. Clone/download this repo (or you package `agent/` + `src/` as a pip-
   installable package later - not done yet)
3. Install Ollama and pull a model (see below)
4. Set three environment variables:
   ```bash
   export SAGA_BACKEND_HTTP_URL="https://your-app.onrender.com"
   export SAGA_BACKEND_WS_URL="wss://your-app.onrender.com"
   export SAGA_SUPABASE_TOKEN="<their JWT, copied from browser localStorage after logging in>"
   ```
5. Run: `python -m agent.saga_agent`

This manual token-copying step is real friction - worth building a
proper `saga-mcp login` CLI command later that opens a browser and
handles this automatically, similar to how the GitHub CLI (`gh auth
login`) works. Not built yet.

## 7. Ollama (required for the LLM planning step, on the agent side only)

The hosted backend never needs Ollama - only wherever the local agent
runs (each user's own machine) needs it, since planning happens there.

1. Download from ollama.com, install
2. `ollama pull llama3.1:8b` (or a smaller model if VRAM is limited)
3. Confirm it's running: `ollama list`

## What's genuinely still not done after all of this

- No `saga-mcp login` CLI - token copying is manual
- No automated tests for the FastAPI routes themselves (only the logic
  underneath each route is tested) - add `httpx.AsyncClient` +
  `TestClient`-based route tests before calling the API layer "fully
  tested"
- Docker sandboxing was explicitly NOT chosen - the local agent model
  means arbitrary code never runs on your servers, which is the safer
  trade-off, but it does mean every user needs local Python + Ollama
  set up, which is real onboarding friction for non-technical users
