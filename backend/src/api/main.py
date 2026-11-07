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
import os
import secrets
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
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
    SupabaseUser,
)
from src.discovery.github_scanner import GitHubAPIError, GitHubRepoScanner
from src.discovery.registry_store import ApprovedPairing, RegistryStore
from src.discovery.tool_discovery import normalize_discovery_args
from src.llm.ollama_client import OllamaClient
from src.logging_utils.audit_log import AuditLog
from src.orchestration.mcp_client import MultiServerToolInvoker, ServerConfig
from src.saga.dead_letter import DeadLetterQueue
from src.saga.executor import SagaExecutor
from src.api.task_store import TaskResultStore
from src.auth.github_oauth import github_authorization_url


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    loop = asyncio.get_running_loop()
    viewers.bind_loop(loop)
    agents.bind_loop(loop)
    _task_results.fail_running()
    _task_results.prune_completed()
    yield


app = FastAPI(title="Saga-MCP API", lifespan=lifespan)

_cors_origins = [
    origin.strip()
    for origin in os.environ.get(
        "SAGA_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "X-Client-Info"],
)

_store = RegistryStore()
_audit_log = AuditLog()
_dlq = DeadLetterQueue()
_github_tokens = GitHubTokenStore()
_task_results = TaskResultStore()


class OneTimeUserTokens:
    def __init__(self) -> None:
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, user_id: str, lifetime_seconds: int = 300) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._tokens = {
                existing: value
                for existing, value in self._tokens.items()
                if value[1] >= now
            }
            self._tokens[token] = (user_id, time.time() + lifetime_seconds)
        return token

    def consume(self, token: str, user_id: str | None = None) -> str | None:
        with self._lock:
            stored = self._tokens.pop(token, None)
        if stored is None:
            return None
        token_user_id, expires_at = stored
        if expires_at < time.time() or (user_id is not None and token_user_id != user_id):
            return None
        return token_user_id


_github_oauth_states = OneTimeUserTokens()
_websocket_tickets = OneTimeUserTokens()


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

    async def connect(
        self, user_id: str, websocket: WebSocket, *, replace_existing: bool = False
    ) -> None:
        await websocket.accept()
        existing = self._connections.get(user_id, [])
        if replace_existing:
            self._connections[user_id] = [websocket]
            for previous in existing:
                await previous.close(code=4001, reason="Replaced by a newer agent connection")
        else:
            existing.append(websocket)
            self._connections[user_id] = existing

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        if user_id in self._connections and websocket in self._connections[user_id]:
            self._connections[user_id].remove(websocket)
            if not self._connections[user_id]:
                del self._connections[user_id]

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


def _dispatch_agent_task(user_id: str, message: dict) -> str:
    request_id = str(uuid.uuid4())
    _task_results.create(request_id, user_id)
    agents.send(user_id, {**message, "request_id": request_id})
    return request_id


def _record_agent_task_result(user_id: str, message: dict) -> None:
    request_id = message.get("request_id")
    if not isinstance(request_id, str) or message.get("type") not in {"task_result", "discovery_result"}:
        return
    _task_results.complete(request_id, user_id, message)


@app.post("/ws-ticket")
def create_websocket_ticket(user: SupabaseUser = Depends(get_current_user)) -> dict:
    return {"ticket": _websocket_tickets.issue(user.user_id)}


@app.websocket("/ws/saga-events")
async def saga_events(websocket: WebSocket, ticket: str = Query(...)) -> None:
    user_id = _websocket_tickets.consume(ticket)
    if user_id is None:
        await websocket.close(code=4401)
        return
    await viewers.connect(user_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        viewers.disconnect(user_id, websocket)


@app.websocket("/ws/agent")
async def agent_channel(websocket: WebSocket, ticket: str = Query(...)) -> None:
    user_id = _websocket_tickets.consume(ticket)
    if user_id is None:
        await websocket.close(code=4401)
        return
    await agents.connect(user_id, websocket, replace_existing=True)
    try:
        while True:
            message = await websocket.receive_json()
            _record_agent_task_result(user_id, message)
            # Relay every message the agent sends straight to that same
            # user's viewer connection(s) - the agent reports discovery
            # results and execution events, the frontend renders them.
            viewers.send(user_id, message)
    except WebSocketDisconnect:
        agents.disconnect(user_id, websocket)
        if not agents.is_connected(user_id):
            _task_results.fail_running(user_id)


# ---------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------

class GitHubConnectRequest(BaseModel):
    code: str
    state: str


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
    compensation_result_arg_mapping: dict[str, str] | None = None
    compensation_idempotent: bool = False
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
    if _github_oauth_states.consume(req.state, user.user_id) is None:
        raise HTTPException(status_code=400, detail="Invalid or expired GitHub OAuth state.")
    try:
        redirect_uri = os.environ.get("GITHUB_OAUTH_REDIRECT_URI")
        token = exchange_code_for_token(req.code, redirect_uri=redirect_uri)
        _github_tokens.store_token(user.user_id, token)
    except GitHubOAuthConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except GitHubOAuthExchangeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TokenEncryptionConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "connected"}


@app.post("/auth/github/authorize")
def github_authorize(
    user: SupabaseUser = Depends(get_current_user),
) -> dict:
    state = _github_oauth_states.issue(user.user_id)
    try:
        authorization_url = github_authorization_url(state)
    except GitHubOAuthConfigError as exc:
        _github_oauth_states.consume(state, user.user_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"authorization_url": authorization_url}


@app.get("/auth/github/status")
def github_status(user: SupabaseUser = Depends(get_current_user)) -> dict:
    try:
        connected = _github_tokens.get_token(user.user_id) is not None
    except TokenEncryptionConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"connected": connected}


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
    return {"approved": [asdict(p) for p in _store.list_approvals(user.user_id)]}


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
                compensation_result_arg_mapping=req.compensation_result_arg_mapping,
                compensation_idempotent=req.compensation_idempotent,
                tool_description=req.tool_description,
                input_schema=req.input_schema,
            ),
            user_id=user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "approved", "tool_name": req.tool_name}


@app.delete("/registry/{tool_name}")
def reject(tool_name: str, user: SupabaseUser = Depends(get_current_user)) -> dict:
    _store.reject(tool_name, user_id=user.user_id)
    return {"status": "removed", "tool_name": tool_name}


# ---------------------------------------------------------------------
# Task execution
# ---------------------------------------------------------------------

@app.get("/agent/status")
def get_agent_status(user: SupabaseUser = Depends(get_current_user)) -> dict:
    return {"connected": agents.is_connected(user.user_id)}


@app.get("/tasks/{request_id}")
def get_task_result(request_id: str, user: SupabaseUser = Depends(get_current_user)) -> dict:
    record = _task_results.get(request_id, user.user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task request not found.")
    return record


@app.post("/run-rollback-demo")
def run_rollback_demo_endpoint(
    req: RollbackDemoRequest,
    user: SupabaseUser = Depends(get_current_user),
) -> dict:
    if not agents.is_connected(user.user_id):
        raise HTTPException(status_code=409, detail="Start the local agent before running the rollback demo.")
    if not req.repo_path.strip():
        raise HTTPException(status_code=400, detail="A local Git repository path is required.")
    request_id = _dispatch_agent_task(
        user.user_id,
        {"type": "run_rollback_demo", "repo_path": req.repo_path},
    )
    return {"status": "dispatched", "request_id": request_id}


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
    request_id = _dispatch_agent_task(
        user.user_id,
        {
            "type": "run_task",
            "task_description": req.task_description,
            "mcp_command": req.mcp_command,
            "mcp_args": req.mcp_args,
            "mcp_servers": [server.model_dump() for server in req.mcp_servers],
        },
    )
    return {"status": "dispatched", "request_id": request_id}


@app.post("/run-task-local")
def run_task_local(req: RunTaskRequest, user: SupabaseUser = Depends(get_current_user)) -> dict:
    """Executes directly on THIS backend process. Only makes sense for
    local development/testing where the target MCP servers are
    reachable from wherever the backend itself runs - NOT the real
    hosted multi-user flow (use /run-task for that)."""
    if os.environ.get("SAGA_ENABLE_BACKEND_MCP_EXECUTION", "").lower() != "true":
        raise HTTPException(
            status_code=404,
            detail="Backend-side MCP execution is disabled. Run a local agent instead.",
        )
    approvals = _store.list_approvals(user.user_id)
    registry = _store.to_compensation_registry(user.user_id)
    invoker = MultiServerToolInvoker()
    if req.mcp_servers:
        for server in req.mcp_servers:
            invoker.register_tools(
                ServerConfig(command=server.command, args=server.args),
                tool_names=server.tool_names,
            )
        configured_tools = {name for server in req.mcp_servers for name in server.tool_names}
    else:
        configured_tools = {pairing.tool_name for pairing in approvals}
        invoker.register_tools(
            ServerConfig(command=req.mcp_command, args=req.mcp_args),
            tool_names=list(configured_tools),
        )

    llm_client = OllamaClient()
    tool_descriptions = {
        pairing.tool_name: pairing.tool_name
        for pairing in approvals
        if pairing.tool_name in configured_tools
    }
    tool_schemas = {
        pairing.tool_name: pairing.input_schema
        for pairing in approvals
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
    if not agents.is_connected(user.user_id):
        raise HTTPException(
            status_code=409,
            detail="Start the local agent before discovering tools.",
        )
    command = (req.command or "").strip()
    if not command:
        raise HTTPException(status_code=400, detail="Command is required for tool discovery.")
    request_id = _dispatch_agent_task(
        user.user_id,
        {
            "type": "scan_local_configs",
            "servers": [
                {
                    "server_name": "manual-discovery",
                    "command": command,
                    "args": normalize_discovery_args(req.args),
                }
            ],
        },
    )
    return {"status": "dispatched", "request_id": request_id}