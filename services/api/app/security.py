from __future__ import annotations

import base64
import hashlib
import hmac
import time

from fastapi import HTTPException, Request, status

from app.config import Settings, get_settings

COOKIE_NAME = "latepayer_session"


def _signature(value: str, secret: str) -> str:
    raw = hmac.new(secret.encode(), value.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def create_session(settings: Settings | None = None, now: int | None = None) -> str:
    settings = settings or get_settings()
    payload = f"{settings.operator_username}:{int(now if now is not None else time.time())}"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{encoded}.{_signature(encoded, settings.session_secret)}"


def session_user(request: Request, settings: Settings | None = None, now: int | None = None) -> str | None:
    settings = settings or get_settings()
    if not settings.production_auth_configured:
        return None
    token = request.cookies.get(COOKIE_NAME, "")
    try:
        encoded, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, _signature(encoded, settings.session_secret)):
            return None
        padded = encoded + "=" * (-len(encoded) % 4)
        username, issued = base64.urlsafe_b64decode(padded).decode().rsplit(":", 1)
        age = int(now if now is not None else time.time()) - int(issued)
        if username != settings.operator_username or age < 0 or age > settings.session_max_age_seconds:
            return None
        return username
    except (ValueError, UnicodeDecodeError):
        return None


def require_operator(request: Request) -> str:
    user = session_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Operator login required.")
    return user
