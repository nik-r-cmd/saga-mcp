"""
BaseAgent defines the interface every agent in this project implements.

Concrete agents (SupportAgent, ReleaseAgent) are built in Stage 1 and 3.
This file only defines the contract now, in Stage 0, so that the
orchestration layer, enforcement layer, and provenance layer can all be
written against a stable interface before any single agent is finished.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class PrivilegeLevel(str, Enum):
    """Coarse privilege classification used by both the baseline
    enforcement layer (Stage 2) and the provenance verifier (Stage 4)."""

    LOW = "low"  # read/search only, e.g. SupportAgent
    HIGH = "high"  # write/merge capable, e.g. ReleaseAgent


@dataclass
class AgentRequest:
    """A single unit of work an agent is asked to perform. When this
    request is forwarded from one agent to another (Stage 3 onward), the
    origin fields are what the provenance layer inspects."""

    task_description: str
    requested_by: str  # agent_id of whoever is asking, set at creation
    origin_agent_id: str | None = None  # set once, never overwritten,
    # even as the request is relayed through further agents


class BaseAgent(ABC):
    """Every agent has an id, a fixed privilege level, and a way to
    execute a request against its available MCP tools."""

    agent_id: str
    privilege_level: PrivilegeLevel

    def __init__(self, agent_id: str, privilege_level: PrivilegeLevel) -> None:
        self.agent_id = agent_id
        self.privilege_level = privilege_level

    @abstractmethod
    def handle_request(self, request: AgentRequest) -> dict:
        """Process a request and return a result dict. Concrete
        implementation arrives in Stage 1 (single agent) and is extended
        in Stage 3 (inter-agent messaging)."""
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - debug convenience only
        return f"<{self.__class__.__name__} id={self.agent_id!r} priv={self.privilege_level.value}>"
