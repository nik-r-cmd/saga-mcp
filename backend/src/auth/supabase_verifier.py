"""
Verifies JWTs issued by Supabase Auth (via your Lovable frontend's login
flow). Your FastAPI backend never handles passwords or OAuth itself -
Supabase does that entirely on the frontend/Supabase side. The backend's
only job is: given a request claiming to be from a logged-in user,
verify the JWT Supabase issued is genuine and not expired.

Setup required (see docs/DEPLOYMENT.md):
  1. In your Supabase project dashboard: Settings -> API -> JWT Settings
     -> copy the "JWT Secret".
  2. Set it as an environment variable: SUPABASE_JWT_SECRET=<that value>
  3. That's it - this module reads it from the environment, nothing else
     to configure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit

import jwt
import httpx
from dotenv import load_dotenv

from src.config import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")


class SupabaseAuthConfigError(Exception):
    """Raised when SUPABASE_JWT_SECRET is not configured - a setup
    problem, distinct from a bad/invalid token, and should fail loudly
    rather than silently accept unverified requests."""


class InvalidSupabaseTokenError(Exception):
    pass


@dataclass
class SupabaseUser:
    user_id: str  # Supabase's "sub" claim
    email: str | None


def _get_jwt_secret() -> str:
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret:
        raise SupabaseAuthConfigError(
            "This token uses legacy HS256 signing, but SUPABASE_JWT_SECRET is not configured."
        )
    return secret


def _get_jwks_url() -> str:
    project_url = os.environ.get("SUPABASE_URL")
    if not project_url:
        project_id = os.environ.get("SUPABASE_PROJECT_ID")
        if not project_id:
            raise SupabaseAuthConfigError(
                "Set SUPABASE_URL or SUPABASE_PROJECT_ID to verify Supabase signing keys."
            )
        project_url = f"https://{project_id}.supabase.co"

    parsed = urlsplit(project_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SupabaseAuthConfigError("SUPABASE_URL must be a valid http(s) URL.")
    return urlunsplit((parsed.scheme, parsed.netloc, "/auth/v1/.well-known/jwks.json", "", ""))


@lru_cache(maxsize=4)
def _get_jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    class HttpxPyJWKClient(jwt.PyJWKClient):
        def fetch_data(self) -> dict:
            try:
                response = httpx.get(self.uri, headers=self.headers, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise jwt.PyJWKClientConnectionError(
                    f"Could not fetch Supabase JWKS: {exc}"
                ) from exc

    return HttpxPyJWKClient(jwks_url)


def verify_supabase_token(token: str) -> SupabaseUser:
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm == "HS256":
            signing_key = _get_jwt_secret()
        elif algorithm in {"ES256", "RS256"}:
            jwks_url = _get_jwks_url()
            try:
                signing_key = _get_jwks_client(jwks_url).get_signing_key_from_jwt(token).key
            except jwt.PyJWKClientConnectionError as exc:
                raise SupabaseAuthConfigError(f"Could not fetch Supabase signing keys: {exc}") from exc
            except jwt.PyJWKClientError as exc:
                raise InvalidSupabaseTokenError(f"No matching Supabase signing key: {exc}") from exc
        else:
            raise InvalidSupabaseTokenError(f"Unsupported Supabase token algorithm: {algorithm!r}")

        claims = jwt.decode(
            token,
            signing_key,
            algorithms=[algorithm],
            audience="authenticated",
        )
    except SupabaseAuthConfigError:
        raise
    except InvalidSupabaseTokenError:
        raise
    except jwt.InvalidTokenError as exc:
        raise InvalidSupabaseTokenError(f"Invalid Supabase token: {exc}") from exc

    user_id = claims.get("sub")
    if not user_id:
        raise InvalidSupabaseTokenError("Token is missing a 'sub' claim.")

    return SupabaseUser(user_id=user_id, email=claims.get("email"))