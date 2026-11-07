"""
Exchanges a GitHub OAuth 'code' (obtained by your Lovable frontend
redirecting the user through GitHub's consent screen) for a real GitHub
access token. This token is what GitHubRepoScanner uses to read a
user's repos.

Requires a GitHub OAuth App you register yourself (see
docs/DEPLOYMENT.md) - I cannot create this for you, it's tied to your
GitHub account.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

import httpx

GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"


class GitHubOAuthConfigError(Exception):
    pass


class GitHubOAuthExchangeError(Exception):
    pass


def _client_id() -> str:
    client_id = os.environ.get("GITHUB_OAUTH_CLIENT_ID")
    if not client_id:
        raise GitHubOAuthConfigError(
            "GITHUB_OAUTH_CLIENT_ID must be set. Register a GitHub OAuth App at "
            "https://github.com/settings/developers to get these."
        )
    return client_id


def github_authorization_url(state: str) -> str:
    redirect_uri = os.environ.get("GITHUB_OAUTH_REDIRECT_URI")
    if not redirect_uri:
        raise GitHubOAuthConfigError("GITHUB_OAUTH_REDIRECT_URI must point to the frontend callback route.")
    return f"{GITHUB_OAUTH_AUTHORIZE_URL}?{urlencode({'client_id': _client_id(), 'redirect_uri': redirect_uri, 'state': state})}"


def exchange_code_for_token(code: str, redirect_uri: str | None = None) -> str:
    client_id = _client_id()
    client_secret = os.environ.get("GITHUB_OAUTH_CLIENT_SECRET")
    if not client_secret:
        raise GitHubOAuthConfigError(
            "GITHUB_OAUTH_CLIENT_SECRET must be set. Register a GitHub OAuth App at "
            "https://github.com/settings/developers to get it."
        )

    try:
        data = {"client_id": client_id, "client_secret": client_secret, "code": code}
        if redirect_uri:
            data["redirect_uri"] = redirect_uri
        response = httpx.post(
            GITHUB_OAUTH_TOKEN_URL,
            headers={"Accept": "application/json"},
            data=data,
            timeout=10.0,
        )
    except httpx.RequestError as exc:
        raise GitHubOAuthExchangeError(f"Network error contacting GitHub: {exc}") from exc

    if response.status_code != 200:
        raise GitHubOAuthExchangeError(f"GitHub OAuth exchange failed: {response.status_code} {response.text[:200]}")

    data = response.json()
    if "error" in data:
        raise GitHubOAuthExchangeError(f"GitHub OAuth error: {data.get('error_description', data['error'])}")

    token = data.get("access_token")
    if not token:
        raise GitHubOAuthExchangeError("GitHub did not return an access_token.")
    return token
