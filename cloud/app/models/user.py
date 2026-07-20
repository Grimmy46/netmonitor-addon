"""A user belongs to an account. Login only (no self-serve signup)."""
import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class User(Base, UUIDPk, Timestamps):
    __tablename__ = "users"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    email: Mapped[str] = mapped_column(unique=True, index=True)
    hashed_password: Mapped[str]
    role: Mapped[str] = mapped_column(default="admin")  # admin | viewer
    is_active: Mapped[bool] = mapped_column(default=True)

    account: Mapped["Account"] = relationship(back_populates="users")  # noqa: F821
