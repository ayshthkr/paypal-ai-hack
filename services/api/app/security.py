from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, status

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from app.config import Settings, get_settings

COOKIE_NAME = "latepayer_session"


def _signature(value: str, secret: str) -> str:
    raw = hmac.new(secret.encode(), value.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def create_session(settings: Settings | None = None, now: int | None = None) -> str:
    settings = settings or get_settings()
    payload = f"{settings.operator_username}:{int(now if now is not None else time.time())}:{secrets.token_urlsafe(24)}"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{encoded}.{_signature(encoded, settings.session_secret)}"


def session_claims(token: str, settings: Settings | None = None, now: int | None = None) -> tuple[str, int, str] | None:
    settings = settings or get_settings()
    if not settings.production_auth_configured:
        return None
    try:
        encoded, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, _signature(encoded, settings.session_secret)):
            return None
        padded = encoded + "=" * (-len(encoded) % 4)
        username, issued, session_id = base64.urlsafe_b64decode(padded).decode().rsplit(":", 2)
        age = int(now if now is not None else time.time()) - int(issued)
        if (username != settings.operator_username or not session_id or age < 0
                or age > settings.session_max_age_seconds):
            return None
        return username, int(issued), session_id
    except (ValueError, UnicodeDecodeError):
        return None


def session_user(
    request: Request,
    settings: Settings | None = None,
    now: int | None = None,
    db: "Session | None" = None,
) -> str | None:
    settings = settings or get_settings()
    claims = session_claims(request.cookies.get(COOKIE_NAME, ""), settings, now)
    if claims is None:
        return None
    username, issued_at, session_id = claims
    if db is not None:
        from app.models import OperatorSession

        record = db.get(OperatorSession, session_id)
        current_time = int(now if now is not None else time.time())
        if (record is None or record.username != username or record.issued_at != issued_at
                or record.revoked_at is not None or record.expires_at < current_time):
            return None
    return username


def require_operator(request: Request, db: "Session | None" = None) -> str:
    user = session_user(request, db=db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Operator login required.")
    return user
