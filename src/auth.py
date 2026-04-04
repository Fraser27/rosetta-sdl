"""Cognito JWT authentication middleware for FastAPI.

Verifies access tokens from the Authorization header against a Cognito User Pool.
Disabled when COGNITO_USER_POOL_ID is not set (local dev mode).

Environment variables:
  COGNITO_USER_POOL_ID  — Cognito User Pool ID (e.g., us-east-1_xxxxx)
  COGNITO_REGION        — AWS region (default: us-east-1)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any
from urllib.request import urlopen

import jwt
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("semantic-layer.auth")

COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_REGION = os.environ.get("COGNITO_REGION", "us-east-1")

# Paths that skip auth
PUBLIC_PATHS = {"/health", "/", "/docs", "/openapi.json", "/redoc"}

# JWKS cache
_jwks: dict[str, Any] | None = None
_jwks_fetched_at: float = 0


def _get_jwks_url() -> str:
    return f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"


def _fetch_jwks() -> dict[str, Any]:
    global _jwks, _jwks_fetched_at
    now = time.time()
    # Cache JWKS for 1 hour
    if _jwks and (now - _jwks_fetched_at) < 3600:
        return _jwks
    url = _get_jwks_url()
    logger.info("Fetching JWKS from %s", url)
    with urlopen(url) as resp:
        _jwks = json.loads(resp.read())
    _jwks_fetched_at = now
    return _jwks


def _get_public_key(token: str) -> Any:
    """Get the RSA public key for a given JWT token from JWKS."""
    jwks = _fetch_jwks()
    headers = jwt.get_unverified_header(token)
    kid = headers.get("kid")
    for key in jwks.get("keys", []):
        if key["kid"] == kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key)
    raise ValueError(f"Public key not found for kid: {kid}")


def verify_cognito_token(token: str) -> dict[str, Any]:
    """Verify and decode a Cognito JWT token."""
    public_key = _get_public_key(token)
    issuer = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
    payload = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer=issuer,
        options={"verify_aud": False},  # access tokens don't have aud
    )
    # Verify token_use is access or id
    token_use = payload.get("token_use")
    if token_use not in ("access", "id"):
        raise ValueError(f"Invalid token_use: {token_use}")
    return payload


class CognitoAuthMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that validates Cognito JWT tokens.

    Skips auth for:
    - Public paths (/health, /, /docs, etc.)
    - OPTIONS requests (CORS preflight)
    - When COGNITO_USER_POOL_ID is not configured (local dev mode)
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Skip auth if Cognito is not configured (local dev)
        if not COGNITO_USER_POOL_ID:
            return await call_next(request)

        # Skip OPTIONS (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Skip public paths
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"},
            )

        token = auth_header[7:]  # Strip "Bearer "
        try:
            claims = verify_cognito_token(token)
            # Attach user info to request state
            request.state.user = claims
            request.state.user_email = claims.get("email", claims.get("username", "unknown"))
        except Exception as e:
            logger.warning("Auth failed: %s", e)
            return JSONResponse(
                status_code=401,
                content={"detail": f"Invalid token: {e}"},
            )

        return await call_next(request)
