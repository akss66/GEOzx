"""身份域：组织、用户（RBAC 两级角色）。"""

from sqlalchemy import Boolean, ForeignKey, String
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

    org: Mapped["Org"] = relationship(back_populates="users")
