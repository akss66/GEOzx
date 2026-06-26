"""模型基础设施：主键 / JSON 类型变体、时间戳混入、枚举构造助手。

- `BigIntPK`：PostgreSQL 用 bigint，SQLite（单测）退化为 INTEGER 以支持自增。
- `JSONVariant`：PostgreSQL 用 JSONB，其它方言用通用 JSON（便于 SQLite 单测）。
- `pg_enum`：以枚举的小写 value 入库（而非 SQLAlchemy 默认的成员名）。
"""

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import JSON, BigInteger, DateTime, Integer, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# 跨方言主键类型（PG: bigint / SQLite: integer 自增）
BigIntPK = BigInteger().with_variant(Integer, "sqlite")

# 跨方言 JSON 类型（PG: JSONB / 其它: JSON）
JSONVariant = JSON().with_variant(JSONB, "postgresql")


def pg_enum(enum_cls: type[PyEnum], name: str) -> SAEnum:
    """构造以小写 value 存储的 Enum 列类型（而非默认成员名）。"""
    return SAEnum(
        enum_cls,
        name=name,
        values_callable=lambda obj: [e.value for e in obj],
    )


class TimestampMixin:
    """统一的创建/更新时间戳。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
