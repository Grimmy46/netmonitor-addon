"""Session auth: bcrypt passwords + a signed JWT in an HttpOnly cookie.

The dashboard is a same-origin SPA (and the Planner iframe is same-origin too),
so a cookie session flows to every API call — including fetches made inside the
embedded SitePlanner — with zero client-side token handling. Kiosk agents never
use cookies; they authenticate with X-Agent-Token on their own endpoints.

Roles: "admin" (can change things) · "viewer" (read-only). Enforced server-side
via the require_admin dependency; the UI merely hides what a viewer can't do.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.models import User

COOKIE_NAME = "nm_session"
SESSION_DAYS = 7
_ALG = "HS256"

def hash_password(plain: str) -> str:
    # bcrypt caps input at 72 bytes; truncate deterministically (standard practice).
    return bcrypt.hashpw(plain.encode("utf-8")[:72], bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("ascii"))
    except Exception:
        return False


def guest_tag(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()[:20]


def make_session_token(user_id: uuid.UUID, gt: str | None = None) -> str:
    now = datetime.now(tz=timezone.utc)
    claims = {"sub": str(user_id), "iat": now, "exp": now + timedelta(days=SESSION_DAYS)}
    if gt:
        claims["gt"] = gt
    return jwt.encode(
        claims,
        get_settings().secret_key,
        algorithm=_ALG,
    )


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


async def _user_from_request(request: Request, db: AsyncSession) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[_ALG])
        user_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    if user.role == "guest":
        # Guest sessions die the moment the link is disabled or rotated.
        from app.models import Account
        acc = await db.get(Account, user.account_id)
        if acc is None or not acc.guest_token or payload.get("gt") != guest_tag(acc.guest_token):
            return None
    return user


# What a guest-link visitor may read (everything else 403s, all writes too).
GUEST_GET_PATHS = ("/auth/me", "/live/feed", "/network/overview", "/agents", "/agents/outages",
                   "/agents/teardown")


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Require a signed-in user (any role)."""
    user = await _user_from_request(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    # Employee accounts are strictly view-only, enforced server-side for every
    # signed-in route: no edits, adds, deletes, imports, notification settings
    # or device control. Only signing out is allowed besides reads.
    path = request.url.path.rstrip("/")
    if path.startswith("/api/"):
        path = path[4:]
    if user.role in ("employee", "guest") and request.method not in ("GET", "HEAD", "OPTIONS") \
            and path != "/auth/logout":
        raise HTTPException(status_code=403, detail="View-only account.")
    if user.role == "guest" and request.method in ("GET", "HEAD") and path not in GUEST_GET_PATHS:
        raise HTTPException(status_code=403, detail="Not available on the guest view.")
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    return user


async def optional_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    return await _user_from_request(request, db)


async def any_users_exist(db: AsyncSession) -> bool:
    return (await db.execute(select(User.id).limit(1))).scalars().first() is not None
