"""
The hosted control-plane backend. Auth via Supabase JWT on every route
(see src/auth/supabase_verifier.py). GitHub scanning reads mcp.json via
the API only - no cloning, no execution. Actual tool discovery and task
execution both happen on the user's own connected local agent (see
agent/saga_agent.py) - this backend never runs a stranger's code.

Endpoints:
  POST   /auth/github/connect     -> exchange GitHub OAuth code, store encrypted token
  POST   /scan-github-repo        -> read mcp.json from a GitHub repo via the API (safe, no execution)
  GET    /registry                -> currently approved pairings
  POST   /registry/approve        -> human approves/edits a suggestion, persisted
  DELETE /registry/{tool_name}    -> reject/remove an approval
  POST   /run-task                -> dispatches a task to the user's connected local agent
  POST   /run-task-local          -> executes directly on THIS backend (local dev/testing only -
                                      only makes sense when the target MCP servers are reachable
                                      from wherever this backend process runs)
  WS     /ws/agent?token=...      -> local agent connects here, receives dispatched tasks
  WS     /ws/saga-events?token=... -> frontend connects here, receives live execution telemetry

Run with:
    uvicorn src.api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import asdict
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.agents.planning_agent import PlanningAgent, PlanValidationError
from src.auth.dependencies import get_current_user
from src.auth.github_oauth import (
    GitHubOAuthConfigError,
    GitHubOAuthExchangeError,
    exchange_code_for_token,
)
from src.auth.github_token_store import GitHubTokenStore, TokenEncryptionConfigError
from src.auth.supabase_verifier import (
    InvalidSupabaseTokenError,
    SupabaseAuthConfigError,
    SupabaseUser,
    verify_supabase_token,
)
from src.discovery.auto_pair import AutoPairSuggester
from src.discovery.github_scanner import GitHubAPIError, GitHubRepoScanner
from src.discovery.registry_store import ApprovedPairing, RegistryStore
from src.discovery.tool_discovery import discover_tools, format_discovery_error, normalize_discovery_args
from src.llm.ollama_client import OllamaClient
from src.logging_utils.audit_log import AuditLog
from src.orchestration.mcp_client import MultiServerToolInvoker, ServerConfig
from src.saga.dead_letter import DeadLetterQueue
from src.saga.executor import SagaExecutor

app = FastAPI(title="Saga-MCP API")

# Local-only for now (frontend dev server on a different port needs
# this). Tighten this to your real frontend's origin before deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_store = RegistryStore()
_audit_log = AuditLog()
_dlq = DeadLetterQueue()
_github_tokens = GitHubTokenStore()


# ---------------------------------------------------------------------
# Per-user connection managers. Both agent connections (execution
# workers) and viewer connections (frontend dashboards) are scoped by
# user_id - the previous version broadcast to every connected viewer
# regardless of whose data it was, which would leak one user's saga
# events to every other logged-in user. Fixed here.
# ---------------------------------------------------------------------

class UserScopedConnections:
    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(user_id, []).append(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        if user_id in self._connections and websocket in self._connections[user_id]:
            self._connections[user_id].remove(websocket)

    def is_connected(self, user_id: str) -> bool:
        return bool(self._connections.get(user_id))

    async def _send_async(self, user_id: str, message: dict) -> bool:
        sockets = self._connections.get(user_id, [])
        if not sockets:
            return False
        dead = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)
        return True

    def send(self, user_id: str, message: dict) -> None:
        """Thread-safe entry point for sync code (e.g. SagaExecutor
        running in a FastAPI threadpool worker) to schedule a send onto
        the main event loop."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._send_async(user_id, message), self._loop)


viewers = UserScopedConnections()  # frontend dashboards
agents = UserScopedConnections()   # local execution agents


@app.on_event("startup")
async def on_startup() -> None:
    loop = asyncio.get_running_loop()
    viewers.bind_loop(loop)
    agents.bind_loop(loop)


def _authenticate_ws_token(token: str) -> SupabaseUser:
    try:
        return verify_supabase_token(token)
    except (SupabaseAuthConfigError, InvalidSupabaseTokenError) as exc:
        raise ValueError(str(exc)) from exc


@app.websocket("/ws/saga-events")
async def saga_events(websocket: WebSocket, token: str = Query(...)) -> None:
    try:
        user = _authenticate_ws_token(token)
    except ValueError:
        await websocket.close(code=4401)
        return
    await viewers.connect(user.user_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        viewers.disconnect(user.user_id, websocket)


@app.websocket("/ws/agent")
async def agent_channel(websocket: WebSocket, token: str = Query(...)) -> None:
    try:
        user = _authenticate_ws_token(token)
    except ValueError:
        await websocket.close(code=4401)
        return
    await agents.connect(user.user_id, websocket)
    try:
        while True:
            message = await websocket.receive_json()
            # Relay every message the agent sends straight to that same
            # user's viewer connection(s) - the agent reports discovery
            # results and execution events, the frontend renders them.
            viewers.send(user.user_id, message)
    except WebSocketDisconnect:
        agents.disconnect(user.user_id, websocket)


# ---------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------

class GitHubConnectRequest(BaseModel):
    code: str


class ScanGitHubRepoRequest(BaseModel):
    owner: str
    repo: str
    ref: str | None = None


class ApproveRequest(BaseModel):
    tool_name: str
    category: Literal["compensable", "pivot"]
    compensating_tool: str | None = None
    server_command: str | None = None
    server_args: list[str] = []
    compensation_arg_mapping: dict[str, str] | None = None
    tool_description: str = ""
    input_schema: dict = {}


class ServerToolConfig(BaseModel):
    command: str
    args: list[str]
    tool_names: list[str]


class RunTaskRequest(BaseModel):
    task_description: str
    mcp_command: str
    mcp_args: list[str] = []
    mcp_servers: list[ServerToolConfig] = []


class RollbackDemoRequest(BaseModel):
    repo_path: str


class DiscoverRequest(BaseModel):
    command: str
    args: list[str] = []


# ---------------------------------------------------------------------
# GitHub OAuth + repo scanning (safe: metadata only, no execution)
# ---------------------------------------------------------------------

@app.post("/auth/github/connect")
def github_connect(req: GitHubConnectRequest, user: SupabaseUser = Depends(get_current_user)) -> dict:
    try:
        token = exchange_code_for_token(req.code)
        _github_tokens.store_token(user.user_id, token)
    except GitHubOAuthConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except GitHubOAuthExchangeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TokenEncryptionConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "connected"}


@app.post("/scan-github-repo")
def scan_github_repo(req: ScanGitHubRepoRequest, user: SupabaseUser = Depends(get_current_user)) -> dict:
    token = _github_tokens.get_token(user.user_id)
    if not token:
        raise HTTPException(status_code=400, detail="No GitHub account connected. Call /auth/github/connect first.")

    scanner = GitHubRepoScanner(access_token=token)
    try:
        servers = scanner.scan(req.owner, req.repo, req.ref)
    except GitHubAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not servers:
        return {"servers_found": [], "message": "No mcp.json configuration found in this repo."}

    # NOTE: this only returns the DECLARED servers (name/command/args
    # parsed from mcp.json). Actually calling tools/list on them means
    # running that server's code, which happens on the user's connected
    # local agent, not here. If an agent is connected, dispatch the scan
    # to it now so the frontend gets real tool discovery results over
    # the WebSocket without a second manual step.
    server_payload = [
        {"server_name": s.server_name, "command": s.command, "args": s.args, "source_file": s.source_file}
        for s in servers
    ]
    if agents.is_connected(user.user_id):
        agents.send(user.user_id, {"type": "scan_local_configs", "servers": server_payload})
        return {"servers_found": server_payload, "discovery": "dispatched_to_agent"}

    return {
        "servers_found": server_payload,
        "discovery": "agent_not_connected",
        "message": "Servers found in mcp.json, but no local agent is connected to actually run "
        "discovery against them. Start your local agent and try again.",
    }


# ---------------------------------------------------------------------
# Approval registry
# ---------------------------------------------------------------------

@app.get("/registry")
def get_registry(user: SupabaseUser = Depends(get_current_user)) -> dict:
    return {"approved": [asdict(p) for p in _store.list_approvals()]}


@app.post("/registry/approve")
def approve(req: ApproveRequest, user: SupabaseUser = Depends(get_current_user)) -> dict:
    try:
        _store.approve(
            ApprovedPairing(
                tool_name=req.tool_name,
                category=req.category,
                compensating_tool=req.compensating_tool,
                server_command=req.server_command,
                server_args=req.server_args,
                compensation_arg_mapping=req.compensation_arg_mapping,
                tool_description=req.tool_description,
                input_schema=req.input_schema,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "approved", "tool_name": req.tool_name}


@app.delete("/registry/{tool_name}")
def reject(tool_name: str, user: SupabaseUser = Depends(get_current_user)) -> dict:
    _store.reject(tool_name)
    return {"status": "removed", "tool_name": tool_name}


# ---------------------------------------------------------------------
# Task execution
# ---------------------------------------------------------------------

@app.get("/agent/status")
def get_agent_status(user: SupabaseUser = Depends(get_current_user)) -> dict:
    return {"connected": agents.is_connected(user.user_id)}


@app.post("/run-rollback-demo")
def run_rollback_demo_endpoint(
    req: RollbackDemoRequest,
    user: SupabaseUser = Depends(get_current_user),
) -> dict:
    if not agents.is_connected(user.user_id):
        raise HTTPException(status_code=409, detail="Start the local agent before running the rollback demo.")
    if not req.repo_path.strip():
        raise HTTPException(status_code=400, detail="A local Git repository path is required.")
    agents.send(
        user.user_id,
        {"type": "run_rollback_demo", "repo_path": req.repo_path},
    )
    return {"status": "dispatched"}


@app.post("/run-task")
def run_task_via_agent(req: RunTaskRequest, user: SupabaseUser = Depends(get_current_user)) -> dict:
    """The real hosted flow: dispatches the task to the user's connected
    local agent, which executes it using the exact same SagaExecutor
    code, on their own machine. Returns immediately - actual progress
    streams over /ws/saga-events."""
    if not agents.is_connected(user.user_id):
        raise HTTPException(
            status_code=409,
            detail="No local agent connected. Start your local agent (see agent/saga_agent.py) first.",
        )
    agents.send(
        user.user_id,
        {
            "type": "run_task",
            "task_description": req.task_description,
            "mcp_command": req.mcp_command,
            "mcp_args": req.mcp_args,
            "mcp_servers": [server.model_dump() for server in req.mcp_servers],
        },
    )
    return {"status": "dispatched"}


@app.post("/run-task-local")
def run_task_local(req: RunTaskRequest, user: SupabaseUser = Depends(get_current_user)) -> dict:
    """Executes directly on THIS backend process. Only makes sense for
    local development/testing where the target MCP servers are
    reachable from wherever the backend itself runs - NOT the real
    hosted multi-user flow (use /run-task for that)."""
    registry = _store.to_compensation_registry()
    invoker = MultiServerToolInvoker()
    if req.mcp_servers:
        for server in req.mcp_servers:
            invoker.register_tools(
                ServerConfig(command=server.command, args=server.args),
                tool_names=server.tool_names,
            )
        configured_tools = {name for server in req.mcp_servers for name in server.tool_names}
    else:
        configured_tools = {pairing.tool_name for pairing in _store.list_approvals()}
        invoker.register_tools(
            ServerConfig(command=req.mcp_command, args=req.mcp_args),
            tool_names=list(configured_tools),
        )

    llm_client = OllamaClient()
    tool_descriptions = {
        pairing.tool_name: pairing.tool_name
        for pairing in _store.list_approvals()
        if pairing.tool_name in configured_tools
    }
    tool_schemas = {
        pairing.tool_name: pairing.input_schema
        for pairing in _store.list_approvals()
        if pairing.tool_name in configured_tools
    }
    planner = PlanningAgent(
        agent_id=user.user_id,
        registry=registry,
        llm_call=llm_client.generate,
        tool_descriptions=tool_descriptions,
        tool_schemas=tool_schemas,
    )
    try:
        plan = planner.plan(req.task_description)
    except PlanValidationError as exc:
        raise HTTPException(status_code=422, detail=f"LLM produced an invalid plan: {exc}") from exc

    executor = SagaExecutor(
        registry, invoker, audit_log=_audit_log, dead_letter_queue=_dlq,
        event_emitter=lambda e: viewers.send(user.user_id, e),
    )
    tracker = executor.run(plan, agent_id=user.user_id)

    return {
        "saga_id": tracker.record.saga_id,
        "status": tracker.record.status.value,
        "steps": [
            {"tool_name": s.tool_name, "status": s.status.value, "failure_reason": s.failure_reason.value}
            for s in tracker.record.steps
        ],
    }

@app.post("/discover")
def discover_tools_endpoint(req: DiscoverRequest, user=Depends(get_current_user)) -> dict:
    command = (req.command or "").strip()
    if not command:
        raise HTTPException(status_code=400, detail="Command is required for tool discovery.")
    if command.lower() == "python":
        command = sys.executable

    try:
        raw_tools = discover_tools(command, normalize_discovery_args(req.args))
    except Exception as exc:  # noqa: BLE001
        details = format_discovery_error(exc)
        raise HTTPException(
            status_code=502,
            detail=f"Tool discovery failed: {details}. Check the backend log for MCP server startup errors.",
        ) from exc

    suggestions_by_tool = {
        suggestion.tool_name: suggestion
        for suggestion in AutoPairSuggester().suggest(raw_tools)
    }
    approved_map = {p.tool_name: p for p in _store.list_approvals()}

    results = []
    for t in raw_tools:
        name = t.name
        desc = t.description
        suggestion = suggestions_by_tool[name]

        results.append({
            "name": name,
            "description": desc,
            "input_schema": t.input_schema,
            "suggested_category": suggestion.suggested_category,
            "suggested_compensating_tool": suggestion.suggested_compensating_tool,
            "confidence": suggestion.confidence,
            "already_approved": name in approved_map
        })
    return {"tools": results}