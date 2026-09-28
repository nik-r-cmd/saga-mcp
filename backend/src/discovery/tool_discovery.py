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
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters, stdio_client


@dataclass
class DiscoveredTool:
    name: str
    description: str
    input_schema: dict


async def _discover_async(command: str, args: list[str]) -> list[DiscoveredTool]:
    params = StdioServerParameters(command=command, args=args)
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
