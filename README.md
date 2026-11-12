# Saga-MCP: Compensation-Based Recovery for MCP Workflows

## Abstract

Saga-MCP is a prototype for managing partial failure in multi-step
workflows executed by AI agents through the Model Context Protocol (MCP).
It pairs approved mutating tool calls with compensating actions and
executes those actions in reverse order when a later step fails. The
system also represents irreversible actions as pivot boundaries, applies
bounded retries to compensation, records failed recovery for review, and
maintains an audit trail.

The repository includes the Saga execution engine, MCP integrations, an
authenticated API, a user-local agent, a web interface, and benchmark
artifacts. The implementation and evaluation are intended to support
reproducible academic review. The documented scope and limitations are
described below and in the linked technical documents.

## Research objective

The project studies whether compensation-based orchestration can reduce
inconsistent state when an MCP workflow fails after some mutating actions
have already completed. It focuses on transactional recovery rather than
general-purpose agent safety. In particular, it does not claim to
intercept arbitrary MCP clients or to detect prompt injection.

## System overview

The execution path consists of the following components:

1. A planning agent produces a sequence of registered tool actions.
2. The Saga executor validates and invokes each action through the
   configured MCP tool invoker.
3. A failure detector checks both tool or protocol errors and semantic
   failures represented in tool responses.
4. On failure, the executor applies registered compensations in
   last-in, first-out order. A pivot marks an irreversible boundary.
5. The audit log and evaluation harness record execution and recovery
   outcomes.

The web interface supports tool discovery, review and approval of
compensation mappings, task execution, and viewing saga events.

## Contributions and implementation

- **Compensation-based execution:** registered inverse actions, explicit
  argument mappings, LIFO recovery, bounded retries, and pivot handling.
- **Failure handling:** protocol-level and semantic failure detection,
  with dead-letter recording when compensation cannot complete.
- **MCP integration:** Git and SQLite tool servers, plus an invoker for
  routing tools to configured servers.
- **Control plane:** an authenticated FastAPI service and a user-local
  agent that runs MCP servers on the user's machine.
- **Evaluation:** benchmark schedules, Git and SQLite state oracles, and
  saved trial-level results and figures.
- **Frontend:** a React and TypeScript application in [`frontend/`](frontend/README.md).

## Scope and limitations

The Saga guarantees apply only to tool calls routed through the invoker
controlled by this project. The implementation is not a transparent proxy
for arbitrary MCP clients. Prompt injection, malicious server metadata,
and cross-server tool shadowing are outside the evaluated threat model.

The current task store and WebSocket routing assume a single backend
process. Horizontal scaling requires shared task storage and shared
WebSocket routing. Compensation is best-effort recovery and cannot
guarantee restoration when an action is irreversible or its compensating
action fails.

For the formal model, algorithm, and threat model, see
[`backend/docs/FORMALIZATION.md`](backend/docs/FORMALIZATION.md). For
implementation status, benchmark methodology, and reported results, see
[`backend/docs/ROADMAP.md`](backend/docs/ROADMAP.md). Deployment details
are in [`backend/docs/DEPLOYMENT.md`](backend/docs/DEPLOYMENT.md).

## Repository structure

```text
backend/
  agent/                 User-local execution agent
  docs/                  Formalization, deployment, and evaluation notes
  scripts/               Demo and benchmark entry points
  src/                   API, MCP integrations, Saga engine, evaluation
  tests/                 Backend test suite
frontend/
  src/                   React and TypeScript application
  public/                Static assets
```

## Requirements

- Windows with PowerShell, Python, and Node.js with npm
- Supabase project settings for authenticated API and frontend use
- Ollama and the configured model for LLM-planned task execution

The backend tests and deterministic benchmark do not require Ollama.

## Local setup

### Backend

From the repository root, create a Python environment, install the
backend requirements, and create the local environment file:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
if (!(Test-Path backend\.env)) { Copy-Item backend\.env.example backend\.env }
```

Set `SUPABASE_PROJECT_ID` and `SUPABASE_URL` in `backend\.env` for
authenticated API use. Start the API from the `backend` directory:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8000
```

### Frontend

In a second terminal, configure and start the application:

```powershell
Set-Location frontend
npm ci
Copy-Item .env.example .env
```

Set `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` in
`frontend\.env`. The backend URL defaults to `http://localhost:8000`;
set `VITE_BACKEND_URL` if the API is hosted elsewhere. Then run:

```powershell
npm run dev
```

### User-local agent

For LLM-planned task execution, install Ollama and pull the configured
model:

```powershell
ollama pull llama3.2:3b
```

With the API running and a user signed in through the frontend, configure
the agent's Supabase session values in the PowerShell process where it
will run. Do not commit access tokens or refresh tokens.

```powershell
$env:SAGA_BACKEND_HTTP_URL = "http://localhost:8000"
$env:SAGA_BACKEND_WS_URL = "ws://localhost:8000"
$env:SAGA_SUPABASE_TOKEN = "<access-token>"
$env:SAGA_SUPABASE_REFRESH_TOKEN = "<refresh-token>"
$env:SAGA_SUPABASE_URL = "https://<project-ref>.supabase.co"
$env:SAGA_SUPABASE_PUBLISHABLE_KEY = "<publishable-key>"
Set-Location backend
..\.venv\Scripts\python.exe -m agent.saga_agent
```

The agent refreshes expired access tokens and keeps rotated tokens in
memory while it is running.

## Tests and evaluation

Run the backend test suite from the `backend` directory:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m pytest -q
```

To reproduce the benchmark with 100 trials per condition, run from
`backend`:

```powershell
$env:BENCH_TRIALS = "100"
..\.venv\Scripts\python.exe -m scripts.run_benchmark
```

The checked-in benchmark results and figures are in
[`backend/benchmark_output/`](backend/benchmark_output/). The benchmark
evaluates transactional execution and recovery under defined fault
schedules. It does not evaluate LLM plan quality or defenses against
adversarial prompts. The methodology and interpretation are documented
in [`backend/docs/ROADMAP.md`](backend/docs/ROADMAP.md).
