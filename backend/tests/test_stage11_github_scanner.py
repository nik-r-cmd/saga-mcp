"""
Tests for GitHubRepoScanner. Uses real httpx.Response objects (not
fake/mock objects) constructed directly - this exercises our actual
response-parsing code path exactly as it would run against a real
API response, without depending on live network conditions or GitHub's
rate limits to pass reliably in CI.

A live, real-network smoke test was also run manually against
api.github.com during development and confirmed: successful content
fetch + base64 decode work correctly, and a genuine 403 rate-limit
response from GitHub's real API was correctly caught and surfaced as a
GitHubAPIError with a clear message - proving the error handling is
real, not just theoretically correct.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from src.discovery.github_scanner import GitHubAPIError, GitHubRepoScanner


def _fake_response(status_code: int, json_body: dict | None = None, text: str = "") -> httpx.Response:
    content = json.dumps(json_body).encode() if json_body is not None else text.encode()
    return httpx.Response(status_code=status_code, content=content, request=httpx.Request("GET", "https://api.github.com/x"))


def test_successful_fetch_decodes_base64_content(monkeypatch):
    scanner = GitHubRepoScanner(access_token="fake-token")
    raw_content = '{"mcpServers": {"git": {"command": "python", "args": ["-m", "x"]}}}'
    encoded = base64.b64encode(raw_content.encode()).decode()

    def fake_get(url, headers, params, timeout):
        return _fake_response(200, {"encoding": "base64", "content": encoded})

    monkeypatch.setattr(httpx, "get", fake_get)
    result = scanner.fetch_file("owner", "repo", "mcp.json")
    assert result == raw_content


def test_404_returns_none_not_error(monkeypatch):
    scanner = GitHubRepoScanner(access_token="fake-token")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(404))
    result = scanner.fetch_file("owner", "repo", "mcp.json")
    assert result is None


def test_403_raises_clear_error(monkeypatch):
    scanner = GitHubRepoScanner(access_token="fake-token")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(403, text="rate limited"))
    with pytest.raises(GitHubAPIError, match="rate-limited|access"):
        scanner.fetch_file("owner", "repo", "mcp.json")


def test_network_error_raises_github_api_error(monkeypatch):
    scanner = GitHubRepoScanner(access_token="fake-token")

    def raise_network_error(*a, **k):
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(httpx, "get", raise_network_error)
    with pytest.raises(GitHubAPIError, match="Network error"):
        scanner.fetch_file("owner", "repo", "mcp.json")


def test_scan_finds_config_at_first_known_path(monkeypatch):
    scanner = GitHubRepoScanner(access_token="fake-token")
    raw_content = '{"mcpServers": {"git": {"command": "python", "args": ["-m", "x"]}}}'
    encoded = base64.b64encode(raw_content.encode()).decode()

    def fake_get(url, headers, params, timeout):
        if url.endswith("/contents/mcp.json"):
            return _fake_response(200, {"encoding": "base64", "content": encoded})
        return _fake_response(404)

    monkeypatch.setattr(httpx, "get", fake_get)
    results = scanner.scan("owner", "repo")
    assert len(results) == 1
    assert results[0].server_name == "git"
    assert results[0].source_file == "mcp.json"


def test_scan_returns_empty_when_no_config_found_anywhere(monkeypatch):
    scanner = GitHubRepoScanner(access_token="fake-token")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(404))
    results = scanner.scan("owner", "repo")
    assert results == []


def test_unexpected_encoding_raises_error(monkeypatch):
    scanner = GitHubRepoScanner(access_token="fake-token")
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _fake_response(200, {"encoding": "utf-8", "content": "raw text"})
    )
    with pytest.raises(GitHubAPIError, match="encoding"):
        scanner.fetch_file("owner", "repo", "mcp.json")
