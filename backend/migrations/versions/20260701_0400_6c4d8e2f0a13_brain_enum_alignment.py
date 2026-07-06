"""brain enum alignment

Revision ID: 6c4d8e2f0a13
Revises: 2f8d4b6c9a11
Create Date: 2026-07-01 04:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "6c4d8e2f0a13"
down_revision: str | None = "2f8d4b6c9a11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


agent_code_enum = sa.Enum(
    "00-decision",
    "01-positioning",
    "02-content-director",
    "03-art-director",
    "04-video-creator",
    "05-editor",
    "06-operator",
    "07-advertiser",
    "08-customer-service",
    name="agent_code",
)

deliverable_type_enum = sa.Enum(
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
    name="deliverable_type",
)

platform_enum = sa.Enum("douyin", "xiaohongshu", "shipinhao", name="platform")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    agent_code_enum.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "agent_invocations",
        "agent_code",
        existing_type=sa.String(length=64),
        type_=agent_code_enum,
        postgresql_using="agent_code::agent_code",
        existing_nullable=False,
    )
    op.alter_column(
        "deliverable_acceptances",
        "agent_code",
        existing_type=sa.String(length=64),
        type_=agent_code_enum,
        postgresql_using="agent_code::agent_code",
        existing_nullable=False,
    )
    op.alter_column(
        "deliverable_acceptances",
        "deliverable_type",
        existing_type=sa.String(length=64),
        type_=deliverable_type_enum,
        postgresql_using="deliverable_type::deliverable_type",
        existing_nullable=False,
    )
    op.alter_column(
        "automation_policies",
        "platform",
        existing_type=sa.String(length=64),
        type_=platform_enum,
        postgresql_using="platform::platform",
        existing_nullable=True,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.alter_column(
        "automation_policies",
        "platform",
        existing_type=platform_enum,
        type_=sa.String(length=64),
        postgresql_using="platform::text",
        existing_nullable=True,
    )
    op.alter_column(
        "deliverable_acceptances",
        "deliverable_type",
        existing_type=deliverable_type_enum,
        type_=sa.String(length=64),
        postgresql_using="deliverable_type::text",
        existing_nullable=False,
    )
    op.alter_column(
        "deliverable_acceptances",
        "agent_code",
        existing_type=agent_code_enum,
        type_=sa.String(length=64),
        postgresql_using="agent_code::text",
        existing_nullable=False,
    )
    op.alter_column(
        "agent_invocations",
        "agent_code",
        existing_type=agent_code_enum,
        type_=sa.String(length=64),
        postgresql_using="agent_code::text",
        existing_nullable=False,
    )
    agent_code_enum.drop(op.get_bind(), checkfirst=True)
