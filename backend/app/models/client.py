"""Client and project access boundaries for agency workspaces."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, TimestampMixin, pg_enum
from app.models.enums import ClientStatus, WorkspaceRole


class Client(Base, TimestampMixin):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[ClientStatus] = mapped_column(
        pg_enum(ClientStatus, "client_status"),
        default=ClientStatus.ACTIVE,
        nullable=False,
    )

    org: Mapped["Org"] = relationship(back_populates="clients")  # noqa: F821
    projects: Mapped[list["Project"]] = relationship(back_populates="client")  # noqa: F821
    accounts: Mapped[list["Account"]] = relationship(back_populates="client")  # noqa: F821
    memberships: Mapped[list["ClientMembership"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )


class ClientMembership(Base, TimestampMixin):
    __tablename__ = "client_memberships"
    __table_args__ = (UniqueConstraint("client_id", "user_id"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        pg_enum(WorkspaceRole, "workspace_role"), nullable=False
    )

    client: Mapped["Client"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(back_populates="client_memberships")  # noqa: F821


class ProjectMembership(Base, TimestampMixin):
    __tablename__ = "project_memberships"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        pg_enum(WorkspaceRole, "workspace_role"), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="memberships")  # noqa: F821
    user: Mapped["User"] = relationship(back_populates="project_memberships")  # noqa: F821


class ProjectAccount(Base, TimestampMixin):
    __tablename__ = "project_accounts"
    __table_args__ = (UniqueConstraint("project_id", "account_id"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )

    project: Mapped["Project"] = relationship(overlaps="accounts,projects")  # noqa: F821
    account: Mapped["Account"] = relationship(overlaps="accounts,projects")  # noqa: F821


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship()  # noqa: F821
