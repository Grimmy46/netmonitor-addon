"""An account is the top-level tenant (multi-tenant-ready from day one)."""
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class Account(Base, UUIDPk, Timestamps):
    __tablename__ = "accounts"

    name: Mapped[str] = mapped_column(default="RCS Tech")

    users: Mapped[list["User"]] = relationship(back_populates="account")  # noqa: F821
    sites: Mapped[list["Site"]] = relationship(back_populates="account")  # noqa: F821
