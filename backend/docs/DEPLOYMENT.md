# Deployment Guide

## Architecture recap

- **Frontend (Lovable):** this repository's Nitro build targets a
  **Cloudflare Worker**. Use the generated Worker configuration and set
  frontend build-time variables before deployment.
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
3. Set these environment variables on your BACKEND host (not frontend):
   `SUPABASE_PROJECT_ID=<project ref>` and
   `SUPABASE_URL=<project auth URL>`. The backend verifies modern ES256/
   RS256 tokens using the project's published JWKS endpoint. Only legacy
   HS256 tokens require `SUPABASE_JWT_SECRET`.

## 2. GitHub OAuth App (for repo scanning)

1. Go to github.com/settings/developers -> "New OAuth App"
2. Application name: whatever you want
3. Homepage URL: your deployed frontend URL
4. Authorization callback URL: your frontend URL plus
   `/auth/github/callback`. After GitHub redirects back, this route sends
   the returned `code` to your backend's `/auth/github/connect`.
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
3. Set the service root directory to `backend`
4. Build command: `pip install -r requirements.txt`
5. Start command: `uvicorn src.api.main:app --host 0.0.0.0 --port $PORT`
6. Add the environment variables described above
7. Deploy. Note the resulting URL (e.g. `https://your-app.onrender.com`)
8. Set `SAGA_CORS_ORIGINS` to a comma-separated list of exact frontend
   origins (for example `https://your-dashboard.example.com`). The local
   development configuration also accepts localhost and 127.0.0.1 on
   any port. Do not use `*` in production.
9. Leave `SAGA_ENABLE_BACKEND_MCP_EXECUTION` unset or set to `false` in
   production. The development-only `/run-task-local` route can start
   MCP server processes on the backend host when explicitly enabled.

Set `GITHUB_OAUTH_REDIRECT_URI` on the backend to the exact frontend
callback URL, including `/auth/github/callback`.

WebSocket credentials are short-lived, one-use tickets issued by the
authenticated `/ws-ticket` endpoint; do not put Supabase access tokens in
WebSocket URLs. Task results are stored in the backend's
`logs/task_results.sqlite3` file. Use a persistent disk for that file in
single-instance deployments. Horizontal scaling still requires shared
task storage and cross-instance WebSocket delivery.

## 5. Deploy the frontend (Cloudflare Workers)

1. Connect the `transactional-nexus` repository to Cloudflare Workers.
2. Set these build-time environment variables:
   - `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` from Supabase
   - `VITE_BACKEND_URL` to the backend URL from step 4
3. Deploy using the generated Nitro/Cloudflare Worker configuration.

## 6. The local agent - what your users need to do

Every user who wants to actually RUN tasks (not just browse) needs to:
1. Have Python 3.10+ installed
2. Clone/download this repo (or you package `agent/` + `src/` as a pip-
   installable package later - not done yet)
3. Install Ollama and pull a model (see below)
4. Set the backend URLs and Supabase session values:
   ```bash
   export SAGA_BACKEND_HTTP_URL="https://your-app.onrender.com"
   export SAGA_BACKEND_WS_URL="wss://your-app.onrender.com"
   export SAGA_SUPABASE_TOKEN="<their JWT, copied from browser localStorage after logging in>"
   export SAGA_SUPABASE_REFRESH_TOKEN="<its refresh_token>"
   export SAGA_SUPABASE_URL="https://your-project.supabase.co"
   export SAGA_SUPABASE_PUBLISHABLE_KEY="<the project's publishable key>"
   ```
5. Run: `python -m agent.saga_agent`

The agent automatically refreshes expired access tokens and keeps the
rotated tokens in memory for its current run. It does not persist tokens
to disk. A browser-based `saga-mcp login` CLI would remove the remaining
manual token-copying step.

## 7. Ollama (required for the LLM planning step, on the agent side only)

The hosted backend never needs Ollama - only wherever the local agent
runs (each user's own machine) needs it, since planning happens there.

1. Download from ollama.com, install
2. `ollama pull llama3.2:3b` (or set `OLLAMA_MODEL` to another installed model)
3. Confirm it's running: `ollama list`

## What's genuinely still not done after all of this

- No `saga-mcp login` CLI - token copying is manual
- Docker sandboxing was explicitly NOT chosen - the local agent model
  means arbitrary code never runs on your servers, which is the safer
  trade-off, but it does mean every user needs local Python + Ollama
  set up, which is real onboarding friction for non-technical users
- Agent and viewer WebSocket connections and one-time tickets are held
  in backend-process memory. Run a single backend instance; horizontal
  scaling needs shared WebSocket routing and shared ticket/task storage.
- Registry rows from the pre-user-scoped schema are retained in an
  unassigned legacy scope. Re-approve those tools under the intended user
  account rather than assigning potentially shared approvals to an
  arbitrary user.
