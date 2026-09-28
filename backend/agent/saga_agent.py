"""
Saga-MCP local agent. Run this on your own machine - it connects out to
your hosted backend and does the actual work (discovery, task
execution) locally, using your own credentials and environment. Your
hosted backend never runs your repo's code directly.

Setup:
    export SAGA_BACKEND_HTTP_URL="https://your-backend.onrender.com"
    export SAGA_BACKEND_WS_URL="wss://your-backend.onrender.com"
    export SAGA_SUPABASE_TOKEN="<paste the JWT from your logged-in session>"

Run:
    python -m agent.saga_agent

Getting SAGA_SUPABASE_TOKEN: log into the web app, open browser dev
tools -> Application -> Local Storage -> find the Supabase auth entry
and copy the access_token value. (A proper `saga-mcp login` CLI command
that does this automatically is a good next addition - not built yet.)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets

from agent.agent_logic import fetch_registry, run_discovery_for_servers
from src.agents.planning_agent import PlanningAgent, PlanValidationError
from src.llm.ollama_client import OllamaClient
from src.orchestration.mcp_client import MultiServerToolInvoker, ServerConfig
from src.saga.executor import SagaExecutor


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"[agent] ERROR: environment variable {name} is not set. See this file's docstring for setup.")
        sys.exit(1)
    return value


async def main() -> None:
    http_url = _require_env("SAGA_BACKEND_HTTP_URL")
    ws_url = _require_env("SAGA_BACKEND_WS_URL")
    token = _require_env("SAGA_SUPABASE_TOKEN")

    uri = f"{ws_url}/ws/agent?token={token}"
    print(f"[agent] connecting to {ws_url}/ws/agent ...")

    async with websockets.connect(uri) as ws:
        print("[agent] connected. Waiting for tasks from the backend.")
        loop = asyncio.get_running_loop()

        async for raw_message in ws:
            message = json.loads(raw_message)
            msg_type = message.get("type")

            if msg_type == "scan_local_configs":
                print(f"[agent] running discovery for {len(message['servers'])} server(s)...")
                results = run_discovery_for_servers(message["servers"])
                await ws.send(json.dumps({"type": "discovery_result", "tools_by_server": results}))
                print("[agent] discovery results sent back.")

            elif msg_type == "run_task":
                print(f"[agent] received task: {message['task_description']!r}")
                # SagaExecutor.run() is synchronous and internally uses
                # asyncio.run() per MCP call - it CANNOT run directly
                # inside this already-running event loop (that would
                # raise "asyncio.run() cannot be called from a running
                # event loop"). Run it in a worker thread instead, same
                # pattern FastAPI itself uses for sync endpoint functions.
                await loop.run_in_executor(None, _run_task_sync, loop, ws, http_url, token, message)

            else:
                print(f"[agent] unknown message type: {msg_type}")


def _run_task_sync(loop: asyncio.AbstractEventLoop, ws, http_url: str, token: str, message: dict) -> None:
    def emit(event: dict) -> None:
        # Called from THIS worker thread - schedule the actual send back
        # onto the agent's main event loop thread-safely.
        asyncio.run_coroutine_threadsafe(ws.send(json.dumps({"type": "event", "payload": event})), loop)

    try:
        registry = fetch_registry(http_url, token)
        invoker = MultiServerToolInvoker()
        invoker.register_tools(
            ServerConfig(command=message["mcp_command"], args=message["mcp_args"]),
            tool_names=registry.tool_names(),
        )

        llm_client = OllamaClient()
        tool_descriptions = {name: name for name in registry.tool_names()}
        planner = PlanningAgent(
            agent_id="local-agent", registry=registry, llm_call=llm_client.generate,
            tool_descriptions=tool_descriptions,
        )
        plan = planner.plan(message["task_description"])

        executor = SagaExecutor(registry, invoker, event_emitter=emit)
        tracker = executor.run(plan, agent_id="local-agent")

        result = {
            "type": "task_result",
            "saga_id": tracker.record.saga_id,
            "status": tracker.record.status.value,
            "steps": [
                {"tool_name": s.tool_name, "status": s.status.value, "failure_reason": s.failure_reason.value}
                for s in tracker.record.steps
            ],
        }
    except PlanValidationError as exc:
        result = {"type": "task_result", "status": "plan_rejected", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - report failure back rather
        # than letting the agent crash silently
        result = {"type": "task_result", "status": "agent_error", "error": str(exc)}

    asyncio.run_coroutine_threadsafe(ws.send(json.dumps(result)), loop)
    print(f"[agent] task finished: {result.get('status')}")


if __name__ == "__main__":
    asyncio.run(main())
