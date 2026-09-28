"""
Thin wrapper around the local Ollama server. Deliberately minimal - one
method, one job: send a system+user prompt, get raw text back.

Kept separate from PlanningAgent so PlanningAgent can be unit-tested
with a fake callable and never needs a real Ollama server running. This
class is what you point at a REAL server when actually demoing.
"""

from __future__ import annotations

from src.config import OllamaConfig, OLLAMA


class OllamaClient:
    def __init__(self, config: OllamaConfig = OLLAMA) -> None:
        self._config = config
        # Imported lazily so importing this module (and anything that
        # transitively imports it) never fails on a machine without the
        # `ollama` package installed, e.g. this sandbox.
        import ollama

        self._client = ollama.Client(host=config.host)

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.chat(
            model=self._config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": self._config.temperature},
        )
        return response["message"]["content"]
