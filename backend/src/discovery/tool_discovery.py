"""
ToolDiscovery: connects to a real upstream MCP server (any server, ours
or a third party's) and calls the real tools/list protocol method to
enumerate what it exposes.

This is the "auto-identify their MCP connections" piece - it works
against ANY MCP server, not just the two we built for the saga demo,
because it speaks the actual protocol rather than reading source code.
"""

from __future__ import annotations

import asyncio
import csv
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client


@dataclass
class DiscoveredTool:
    name: str
    description: str
    input_schema: dict


def normalize_discovery_args(args: list[str]) -> list[str]:
    """Accept the UI's legacy comma-delimited argv as well as JSON arrays."""
    if len(args) == 1 and args[0].lstrip().startswith("-m,"):
        return [arg.strip() for arg in next(csv.reader([args[0]], skipinitialspace=True)) if arg.strip()]
    return args


def format_discovery_error(error: BaseException) -> str:
    nested_errors = getattr(error, "exceptions", None)
    if nested_errors:
        return "; ".join(format_discovery_error(nested) for nested in nested_errors)
    return str(error)


async def _discover_async(command: str, args: list[str]) -> list[DiscoveredTool]:
    # Child MCP servers often run as `python -m src.mcp_servers.*`.
    # They must be launched from the backend package root so imports work
    # reliably regardless of whatever directory the caller is running from.
    backend_root = Path(__file__).resolve().parents[2]
    if command.strip().lower() in {"python", "python.exe"}:
        command = sys.executable
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(backend_root) + (os.pathsep + existing_pythonpath if existing_pythonpath else "")

    params = StdioServerParameters(command=command, args=list(args), cwd=backend_root, env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [
                DiscoveredTool(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.input_schema or {},
                )
                for tool in result.tools
            ]

def discover_tools(command: str, args: list[str], timeout_seconds: float = 15.0):
    return asyncio.run(asyncio.wait_for(_discover_async(command, args), timeout=timeout_seconds))
