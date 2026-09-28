# Customer Journey - Frontend to Backend Mapping

This is the complete, accurate flow. Every backend endpoint listed here
exists, is tested, and was verified live in this session. Frontend
pieces are what you prompt Lovable to build/wire against these real
endpoints.

## Stage 1: Login

**Frontend (Lovable + Supabase):** User clicks "Sign in with Google" or
enters email/password. Supabase handles the entire flow and returns a
JWT to the frontend. Lovable stores this token and attaches it as
`Authorization: Bearer <token>` on every API call from here on.

**Backend:** Does nothing at login time - no endpoint needed. The
backend only verifies the token Supabase already issued, on every
subsequent request (`src/auth/supabase_verifier.py`).

**You need to do:** In your Supabase project dashboard -> Settings ->
API -> JWT Settings, copy the JWT Secret, set it as an environment
variable `SUPABASE_JWT_SECRET` before starting the backend.

---

## Stage 2: Connect a Repo

**Frontend:** A form - "Paste your repo's local path" (or, if you've
built repo cloning, a path to where it was cloned). Button: "Scan for
MCP Tools."

**Backend:** `POST /scan-repo` `{"repo_path": "/path/to/repo"}`
- Scans for `mcp.json`, `.cursor/mcp.json`, `.mcp/config.json`,
  `.vscode/mcp.json`, and any `mcp.json` found within 4 directory levels
- For every server definition found, connects live and calls the real
  `tools/list` protocol method
- Returns every tool found, grouped by server, each with an
  auto-suggested compensating pair and confidence level

**Real, tested. Not built:** connecting a GitHub repo via OAuth and
cloning it server-side. For now, "repo" means a local path the backend
process can read - if you want GitHub OAuth + cloning, that's new scope
(flag if you want it next).

---

## Stage 3: Review & Approve

**Frontend:** Table of discovered tools per server. Each row: name,
description, suggested category (editable dropdown: Compensable /
Pivot), suggested compensating tool (editable dropdown), confidence
badge (flag "needs review" visually when confidence is `"low"`).
Approve button per row.

**Backend:**
- `POST /registry/approve` `{"tool_name": ..., "category": "compensable"|"pivot", "compensating_tool": ...}`
  - Real validation: a `"compensable"` category with no
    `compensating_tool` is rejected with a 400, not silently accepted
  - Persisted to SQLite (`logs/saga.db`) - survives restarts, safe
    under concurrent requests
- `GET /registry` - list everything currently approved
- `DELETE /registry/{tool_name}` - revoke an approval

---

## Stage 4: Run a Task

**Frontend:** A text box - "What do you want the agent to do?" - plus
the MCP server command to run it against (this can be pre-filled from
Stage 2's scan result). Button: "Run."

**Backend:** `POST /run-task`
`{"task_description": "...", "mcp_command": "python", "mcp_args": [...]}`
1. Builds a `CompensationRegistry` from everything approved in Stage 3
2. Sends the task description to your local Ollama model
   (`PlanningAgent`), which returns a validated step-by-step plan -
   any tool the LLM hallucinates that isn't in the approved registry
   is rejected before anything executes
3. `SagaExecutor` runs the real plan against the real MCP server
4. On genuine failure, compensating actions run automatically
5. Every step transition is broadcast live to `/ws/saga-events`

**Status: code complete, genuinely never run against a real Ollama
model.** This is the single most important thing to test on your
machine before you claim this works.

---

## Stage 5: Watch It Happen Live

**Frontend:** Connect to `ws://localhost:8000/ws/saga-events`, render
each incoming event as a step in your DAG/timeline view.

**Backend:** Every event is a JSON object:
```json
{
  "event_type": "FORWARD_EXEC" | "FAULT_DETECTED" | "COMPENSATED" | "DEAD_LETTER",
  "saga_id": "...",
  "tool_name": "...",
  "status": "...",
  "timestamp": ...
}
```
Map these to your node colors: `FORWARD_EXEC` = green, `FAULT_DETECTED`
= red, `COMPENSATED` = gray/struck-through, `DEAD_LETTER` = bright red
alert requiring human review.

**Known simplification:** there is no separate "in-progress/amber"
event fired while a compensation is actively being attempted - only the
terminal outcome (`COMPENSATED` or `DEAD_LETTER`) is emitted. If you
want a live "attempting..." state in the UI, that's a small addition to
`_compensate_with_retries` in `src/saga/executor.py` - ask if you want
it built.

---

## What genuinely doesn't exist anywhere in this codebase

- GitHub OAuth / repo cloning
- Transparent stdio/SSE proxy daemon sitting between third-party
  clients (Claude Desktop, Cursor) and their servers
- Multi-tenant credential encryption / sandboxed execution per user
- Any deployment/hosting configuration
