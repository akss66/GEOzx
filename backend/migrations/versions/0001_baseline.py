"""baseline (empty)

建立迁移基线，使 `alembic upgrade head` 能创建 alembic_version 版本表。
不含任何业务表——核心数据模型与首个真实迁移在 T3 生成。

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-26
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
