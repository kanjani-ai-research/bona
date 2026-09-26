"""Authentication Middleware — delegation token or simple Bearer secret.

Modes (AUTH_TYPE env var):
  - env (default): Bearer token matches AUTH_SECRET
  - vlt: HMAC-based delegation tokens (VOUCH_SIGNING_KEY)
  - disabled: no auth configured → open access (dev only)

Exempt paths: /health, /ping, /debug
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

EXEMPT_PATHS = {"/health", "/ping", "/debug", "/openapi.json"}


class DelegationTokenMiddleware(BaseHTTPMiddleware):
    """Auth middleware compatible with the client pipeline token model."""

    def __init__(self, app, signing_key: str = ""):
        super().__init__(app)
        self.auth_type = os.environ.get("AUTH_TYPE", "env").lower()
        self.signing_key = signing_key or os.environ.get("VOUCH_SIGNING_KEY", "")
        self.env_secret = os.environ.get("AUTH_SECRET", "")

        if self.auth_type == "env" and self.env_secret:
            logger.info("Auth: env-secret mode (Bearer token)")
        elif self.auth_type == "vlt" and self.signing_key:
            logger.info("Auth: VLT delegation token mode")
        else:
            logger.warning("Auth: DISABLED — no credentials configured")

    async def dispatch(self, request: Request, call_next):
        # Exempt paths
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        # No auth configured → open access
        if not self.env_secret and not self.signing_key:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"error": "Missing Authorization header"}, status_code=401)

        token = auth_header[7:]

        if self.auth_type == "env":
            if not self._verify_env_token(token):
                return JSONResponse({"error": "Invalid token"}, status_code=403)
        elif self.auth_type == "vlt":
            if not self._verify_vlt_token(token):
                return JSONResponse({"error": "Invalid delegation token"}, status_code=403)

        return await call_next(request)

    def _verify_env_token(self, token: str) -> bool:
        """Simple secret comparison."""
        return hmac.compare_digest(token, self.env_secret)

    def _verify_vlt_token(self, token: str) -> bool:
        """Verify HMAC-signed delegation token."""
        try:
            payload = json.loads(token)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Token is not valid JSON")
            return False

        signature = payload.pop("signature", "")
        if not signature:
            return False

        # Check expiry
        expires_at = payload.get("expires_at", "")
        if expires_at:
            try:
                exp = datetime.fromisoformat(expires_at)
                if exp < datetime.now(timezone.utc):
                    logger.warning("Token expired: %s", expires_at)
                    return False
            except (ValueError, TypeError):
                pass

        # Verify HMAC
        message = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected = hmac.new(
            self.signing_key.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(signature, expected)
