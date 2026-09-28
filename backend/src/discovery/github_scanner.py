"""
GitHubRepoScanner: reads MCP config files directly from a GitHub repo
via the GitHub REST API (Contents API), with NO local clone required.
This is what makes "connect your GitHub repo" work without needing the
repo to exist anywhere on disk - the backend just reads specific files
over HTTPS.

Requires a GitHub access token (obtained via OAuth - see
src/auth/github_oauth.py). Only reads file contents, never executes
anything and never downloads the full repo - this stays true to the
same "scanning never executes code" principle as the local scanner.
"""

from __future__ import annotations

import base64

import httpx

from src.discovery.mcp_config_parser import KNOWN_CONFIG_PATHS, DiscoveredServerConfig, parse_mcp_config

GITHUB_API_BASE = "https://api.github.com"


class GitHubAPIError(Exception):
    pass


class GitHubRepoScanner:
    def __init__(self, access_token: str, timeout_seconds: float = 10.0) -> None:
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._timeout = timeout_seconds

    def fetch_file(self, owner: str, repo: str, path: str, ref: str | None = None) -> str | None:
        """Returns the file's text content, or None if it doesn't exist
        at this path. Raises GitHubAPIError for anything else (auth
        failure, rate limit, network error) - those are real problems
        the caller needs to know about, distinct from "file not found."
        """
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}"
        params = {"ref": ref} if ref else {}
        try:
            response = httpx.get(url, headers=self._headers, params=params, timeout=self._timeout)
        except httpx.RequestError as exc:
            raise GitHubAPIError(f"Network error contacting GitHub: {exc}") from exc

        if response.status_code == 404:
            return None
        if response.status_code == 403:
            raise GitHubAPIError(
                "GitHub API returned 403 - likely rate-limited or the token "
                "lacks access to this repo."
            )
        if response.status_code != 200:
            raise GitHubAPIError(f"GitHub API error {response.status_code}: {response.text[:200]}")

        data = response.json()
        if data.get("encoding") != "base64":
            raise GitHubAPIError(f"Unexpected content encoding from GitHub API: {data.get('encoding')}")

        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    def scan(self, owner: str, repo: str, ref: str | None = None) -> list[DiscoveredServerConfig]:
        """Checks every known MCP config path via the API. Does NOT do a
        full recursive tree walk (unlike the local scanner) - that would
        cost one API call per file in the repo against GitHub's rate
        limits. Known-path checking covers the standard locations every
        real MCP client actually uses."""
        found: list[DiscoveredServerConfig] = []
        for candidate_path in KNOWN_CONFIG_PATHS:
            content = self.fetch_file(owner, repo, candidate_path, ref)
            if content is not None:
                found.extend(parse_mcp_config(content, source_file=candidate_path))
        return found
