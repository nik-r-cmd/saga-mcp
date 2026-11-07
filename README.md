# Saga-MCP

Saga-MCP is a transactional recovery layer for multi-step MCP tool
workflows driven by LLM agents. It pairs approved mutating tools with
compensating actions, detects protocol and semantic failures, and
executes compensation in reverse order when a workflow fails.

## What is included

- **Saga engine:** validated plans, compensation mappings, pivot boundaries,
  bounded retries, dead-letter handling, and an audit trail.
- **MCP integrations:** real Git and SQLite MCP servers, with a
  multi-server invoker.
- **Control plane:** authenticated FastAPI API and a user-local WebSocket
  agent so MCP servers run on the user's own machine.
- **Dashboard:** the separate `transactional-nexus` React application for
  tool discovery, approval, task execution, and live saga events.
- **Evaluation:** reproducible benchmark schedules, Git/SQLite state
  oracles, and saved trial-level results.

## Run locally on Windows

From the repository root in PowerShell, install backend dependencies and
create a local environment file:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
if (!(Test-Path backend\.env)) { Copy-Item backend\.env.example backend\.env }
```

Set `SUPABASE_PROJECT_ID` and `SUPABASE_URL` in `backend\.env`. Start the
API in one terminal:

```powershell
Set-Location "<path-to-repository>\backend"
..\.venv\Scripts\python.exe -m uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal, start the `transactional-nexus` frontend. Set its
Supabase project values as well as the backend URL:

```powershell
Set-Location "<path-to-transactional-nexus>"
$env:VITE_BACKEND_URL = "http://localhost:8000"
$env:VITE_SUPABASE_URL = "https://<project-ref>.supabase.co"
$env:VITE_SUPABASE_PUBLISHABLE_KEY = "<publishable-key>"
npm run dev
```

Sign in through Supabase. For LLM-planned execution, install Ollama and
pull the configured model (`ollama pull llama3.2:3b`). In a third
terminal, set `SAGA_BACKEND_HTTP_URL`, `SAGA_BACKEND_WS_URL`, and the
Supabase session values for the signed-in user, then run:

```powershell
$env:SAGA_BACKEND_HTTP_URL = "http://localhost:8000"
$env:SAGA_BACKEND_WS_URL = "ws://localhost:8000"
$env:SAGA_SUPABASE_TOKEN = "<access-token-from-your-Supabase-session>"
$env:SAGA_SUPABASE_REFRESH_TOKEN = "<refresh-token-from-the-same-session>"
$env:SAGA_SUPABASE_URL = "https://<project-ref>.supabase.co"
$env:SAGA_SUPABASE_PUBLISHABLE_KEY = "<publishable-key>"
```

```powershell
Set-Location "<path-to-repository>\backend"
..\.venv\Scripts\python.exe -m agent.saga_agent
```

The local agent refreshes expired access tokens and keeps rotated tokens
in memory while running. Do not commit any session token.

The agent and task-result tracking currently rely on a single backend
process; horizontal scaling requires shared WebSocket routing and task
storage.

## Tests and benchmark

Run the backend tests from `backend`:

```powershell
..\.venv\Scripts\python.exe -m pytest -q
```

The recorded paper benchmark contains 100 trials per condition and
confidence intervals derived from its saved trial data. Its methodology,
results, deployment guidance, and backend details are documented in
[`backend/docs/ROADMAP.md`](backend/docs/ROADMAP.md),
[`backend/docs/DEPLOYMENT.md`](backend/docs/DEPLOYMENT.md), and
[`backend/README.md`](backend/README.md).
