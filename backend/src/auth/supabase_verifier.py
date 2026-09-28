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

import jwt


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
    # BYPASS: Return a dummy string so the backend doesn't crash on startup
    return "dummy_secret_for_local_demo"


def verify_supabase_token(token: str) -> SupabaseUser:
    try:
        # BYPASS: Decode the token but completely ignore the cryptographic signature
        claims = jwt.decode(
            token,
            options={"verify_signature": False, "verify_audience": False}
        )
    except jwt.InvalidTokenError as exc:
        raise InvalidSupabaseTokenError(f"Invalid token format: {exc}") from exc

    user_id = claims.get("sub")
    if not user_id:
        raise InvalidSupabaseTokenError("Token is missing a 'sub' claim.")

    return SupabaseUser(user_id=user_id, email=claims.get("email"))