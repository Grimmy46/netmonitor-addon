"""Accounts & sessions: first-run setup, login/logout, and user management.

Bootstrap: with ZERO users in the database, /auth/status reports
setup_required and /auth/setup creates the first ADMIN account (one-time —
locked the moment any user exists). After that, admins manage users from
Settings. No self-serve signup.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    any_users_exist,
    clear_session_cookie,
    current_user,
    hash_password,
    make_session_token,
    optional_user,
    require_admin,
    set_session_cookie,
    verify_password,
)
from app.core.db import get_db
from app.models import User
from app.services.sync import get_or_create_account

router = APIRouter(prefix="/auth", tags=["auth"])

ROLES = ("admin", "viewer")


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    is_active: bool


class StatusOut(BaseModel):
    setup_required: bool
    authenticated: bool
    user: UserOut | None = None


class SetupIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    email: str
    password: str


class UserCreateIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    role: str = "viewer"


class PasswordIn(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class RoleIn(BaseModel):
    role: str


def _out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, role=u.role, is_active=u.is_active)


def _norm_email(e: str) -> str:
    return e.strip().lower()


@router.get("/status", response_model=StatusOut)
async def auth_status(request: Request, db: AsyncSession = Depends(get_db)) -> StatusOut:
    has_users = await any_users_exist(db)
    user = await optional_user(request, db) if has_users else None
    return StatusOut(
        setup_required=not has_users,
        authenticated=user is not None,
        user=_out(user) if user else None,
    )


@router.post("/setup", response_model=UserOut)
async def first_run_setup(
    body: SetupIn, response: Response, db: AsyncSession = Depends(get_db)
) -> UserOut:
    """Create the FIRST admin account. Only available while no users exist."""
    if await any_users_exist(db):
        raise HTTPException(status_code=403, detail="Setup is already complete.")
    account = await get_or_create_account(db)
    user = User(
        account_id=account.id,
        email=_norm_email(body.email),
        hashed_password=hash_password(body.password),
        role="admin",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    set_session_cookie(response, make_session_token(user.id))
    return _out(user)


@router.post("/login", response_model=UserOut)
async def login(body: LoginIn, response: Response, db: AsyncSession = Depends(get_db)) -> UserOut:
    user = (
        await db.execute(select(User).where(User.email == _norm_email(body.email)))
    ).scalars().first()
    if user is None or not user.is_active or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Wrong email or password.")
    set_session_cookie(response, make_session_token(user.id))
    return _out(user)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    clear_session_cookie(response)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> UserOut:
    return _out(user)


# ── user management (admin only) ─────────────────────────────────────────────
@router.get("/users", response_model=list[UserOut])
async def list_users(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
) -> list[UserOut]:
    users = (await db.execute(select(User).order_by(User.email))).scalars().all()
    return [_out(u) for u in users]


@router.post("/users", response_model=UserOut)
async def create_user(
    body: UserCreateIn, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)
) -> UserOut:
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail="Role must be admin or viewer.")
    email = _norm_email(body.email)
    exists = (await db.execute(select(User).where(User.email == email))).scalars().first()
    if exists:
        raise HTTPException(status_code=409, detail="That email already has an account.")
    account = await get_or_create_account(db)
    user = User(
        account_id=account.id,
        email=email,
        hashed_password=hash_password(body.password),
        role=body.role,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _out(user)


async def _other_active_admin_exists(db: AsyncSession, user_id: uuid.UUID) -> bool:
    others = (
        await db.execute(
            select(User).where(User.role == "admin", User.is_active.is_(True), User.id != user_id)
        )
    ).scalars().first()
    return others is not None


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)
) -> None:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="No such user.")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You can't delete your own account.")
    if user.role == "admin" and not await _other_active_admin_exists(db, user.id):
        raise HTTPException(status_code=400, detail="Can't remove the last admin.")
    await db.delete(user)
    await db.commit()


@router.post("/users/{user_id}/password", status_code=204)
async def set_user_password(
    user_id: uuid.UUID,
    body: PasswordIn,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> None:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="No such user.")
    user.hashed_password = hash_password(body.password)
    await db.commit()


@router.post("/users/{user_id}/role", response_model=UserOut)
async def set_user_role(
    user_id: uuid.UUID,
    body: RoleIn,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> UserOut:
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail="Role must be admin or viewer.")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="No such user.")
    if user.role == "admin" and body.role != "admin" and not await _other_active_admin_exists(db, user.id):
        raise HTTPException(status_code=400, detail="Can't demote the last admin.")
    user.role = body.role
    await db.commit()
    await db.refresh(user)
    return _out(user)
