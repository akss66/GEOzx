"""工作区域：运营项目、账号矩阵（AccountGroup / Account 一等模型）。"""

from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin, pg_enum
from app.models.enums import AccountStatus, GroupDimension, Platform, ProjectStatus


class Project(Base, TimestampMixin):
    """运营项目（绑定定位/账号，内容流水线的容器）。"""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    monthly_cost_budget_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    status: Mapped[ProjectStatus] = mapped_column(
        pg_enum(ProjectStatus, "project_status"),
        default=ProjectStatus.ACTIVE,
        nullable=False,
    )

    org: Mapped["Org"] = relationship()  # noqa: F821
    client: Mapped["Client | None"] = relationship(back_populates="projects")  # noqa: F821
    content_items: Mapped[list["ContentItem"]] = relationship(  # noqa: F821
        back_populates="project", cascade="all, delete-orphan"
    )
    accounts: Mapped[list["Account"]] = relationship(  # noqa: F821
        secondary="project_accounts",
        back_populates="projects",
        overlaps="account,project",
    )
    memberships: Mapped[list["ProjectMembership"]] = relationship(  # noqa: F821
        back_populates="project", cascade="all, delete-orphan"
    )
    legacy_accounts: Mapped[list["Account"]] = relationship(  # noqa: F821
        back_populates="project", foreign_keys="Account.project_id"
    )


class AccountGroup(Base, TimestampMixin):
    """账号分组：按赛道 / 人设 / 平台组织矩阵账号。"""

    __tablename__ = "account_groups"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    dimension: Mapped[GroupDimension] = mapped_column(
        pg_enum(GroupDimension, "group_dimension"),
        default=GroupDimension.TRACK,
        nullable=False,
    )

    org: Mapped["Org"] = relationship()  # noqa: F821
    accounts: Mapped[list["Account"]] = relationship(back_populates="group")


class Account(Base, TimestampMixin):
    """矩阵账号（一等模型）。授权 Token 等存于 auth(JSONB)，后续加密。"""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_groups.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # Transitional legacy pointer. New product behavior uses project_accounts.
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True, nullable=True
    )
    platform: Mapped[Platform] = mapped_column(pg_enum(Platform, "platform"), nullable=False)
    external_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    nickname: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[AccountStatus] = mapped_column(
        pg_enum(AccountStatus, "account_status"),
        default=AccountStatus.ACTIVE,
        nullable=False,
    )
    auth: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)

    org: Mapped["Org"] = relationship()  # noqa: F821
    client: Mapped["Client | None"] = relationship(back_populates="accounts")  # noqa: F821
    project: Mapped["Project | None"] = relationship(  # noqa: F821
        back_populates="legacy_accounts", foreign_keys=[project_id]
    )
    projects: Mapped[list["Project"]] = relationship(  # noqa: F821
        secondary="project_accounts",
        back_populates="accounts",
        overlaps="account,project",
    )
    group: Mapped["AccountGroup | None"] = relationship(back_populates="accounts")

    @property
    def integration_status(self) -> str:
        meta = self.auth or {}
        if meta.get("integration_status"):
            return str(meta["integration_status"])
        if self.platform == Platform.DOUYIN:
            return "oauth_ready"
        return "manual"

    @property
    def auth_status(self) -> str:
        meta = self.auth or {}
        if meta.get("auth_status"):
            return str(meta["auth_status"])
        return "authorized" if meta.get("access_token") else "unauthorized"

    @property
    def data_sync_status(self) -> str:
        meta = self.auth or {}
        return str(meta.get("data_sync_status") or "not_configured")
