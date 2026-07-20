"""身份域：组织、用户（RBAC 两级角色）。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, TimestampMixin, pg_enum
from app.models.enums import UserRole


class Org(Base, TimestampMixin):
    """组织（单团队单部署；预留未来多租户隔离边界）。"""

    __tablename__ = "orgs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="org", cascade="all, delete-orphan")
    clients: Mapped[list["Client"]] = relationship(  # noqa: F821
        back_populates="org", cascade="all, delete-orphan"
    )


class User(Base, TimestampMixin):
    """系统用户。role=admin/user（见 UserRole，可扩展）。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, "user_role"), default=UserRole.USER, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    account_scope_mode: Mapped[str] = mapped_column(
        String(32), default="all_accessible", server_default="all_accessible", nullable=False
    )

    org: Mapped["Org"] = relationship(back_populates="users")
    client_memberships: Mapped[list["ClientMembership"]] = relationship(  # noqa: F821
        back_populates="user", cascade="all, delete-orphan"
    )
    project_memberships: Mapped[list["ProjectMembership"]] = relationship(  # noqa: F821
        back_populates="user", cascade="all, delete-orphan"
    )
    account_memberships: Mapped[list["AccountMembership"]] = relationship(  # noqa: F821
        back_populates="user", cascade="all, delete-orphan"
    )
    admin_security_credential: Mapped["AdminSecurityCredential | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class AdminSecurityCredential(Base):
    __tablename__ = "admin_security_credentials"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    delete_available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC) + timedelta(minutes=10),
        nullable=False,
    )
    failed_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="admin_security_credential")


class UserDeletionPreviewReservation(Base):
    """Atomic, non-sensitive single-use marker for a deletion preview nonce."""

    __tablename__ = "user_deletion_preview_reservations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "operation_id",
            name="uq_user_deletion_preview_reservations_org_operation",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
