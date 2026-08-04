"""Add the dedicated weekly publish-package deliverable type.

Revision ID: 20260805_0300
Revises: 20260805_0200
Create Date: 2026-08-05 16:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260805_0300"
down_revision: str | None = "20260805_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VALUES = (
    "positioning_strategy",
    "topic_plan",
    "publish_calendar",
    "video_script",
    "art_prompt",
    "video_asset",
    "edited_video",
    "review_report",
    "ad_plan",
    "cs_record",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE deliverable_type ADD VALUE IF NOT EXISTS 'publish_package'")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # PostgreSQL cannot drop one enum value. Preserve rollback compatibility by
    # mapping package rows to their former calendar representation, then rebuild.
    op.execute(
        "UPDATE deliverables SET type = 'publish_calendar' "
        "WHERE type::text = 'publish_package'"
    )
    op.execute(
        "UPDATE deliverable_acceptances SET deliverable_type = 'publish_calendar' "
        "WHERE deliverable_type::text = 'publish_package'"
    )
    op.execute(
        "ALTER TABLE deliverables ALTER COLUMN type TYPE text USING type::text"
    )
    op.execute(
        "ALTER TABLE deliverable_acceptances ALTER COLUMN deliverable_type "
        "TYPE text USING deliverable_type::text"
    )
    op.execute("DROP TYPE deliverable_type")
    values = ", ".join(f"'{value}'" for value in _OLD_VALUES)
    op.execute(f"CREATE TYPE deliverable_type AS ENUM ({values})")
    op.execute(
        "ALTER TABLE deliverables ALTER COLUMN type TYPE deliverable_type "
        "USING type::deliverable_type"
    )
    op.execute(
        "ALTER TABLE deliverable_acceptances ALTER COLUMN deliverable_type "
        "TYPE deliverable_type USING deliverable_type::deliverable_type"
    )
