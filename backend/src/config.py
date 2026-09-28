"""
Central configuration for the project. Every module pulls constants from
here rather than hardcoding values, so changing the LLM model, ports, or
storage paths never requires touching more than one file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

AUDIT_DB_PATH = LOG_DIR / "audit_trail.sqlite3"


@dataclass(frozen=True)
class OllamaConfig:
    """Connection settings for the locally hosted LLM."""

    host: str = "http://localhost:11434"
    model: str = "llama3.1:8b"  # swap for whatever fits your VRAM
    temperature: float = 0.2  # low temperature: agent behavior should be
    # reproducible enough for evaluation runs


@dataclass(frozen=True)
class EnforcementDefaults:
    """Default policy posture, mirrors the base paper's L0-L3 scheme."""

    default_level: str = "L1"  # scoped access binding by default
    read_only_by_default: bool = True


OLLAMA = OllamaConfig()
ENFORCEMENT = EnforcementDefaults()
