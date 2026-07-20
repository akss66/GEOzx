"""Persist organization model providers and route references.

Revision ID: 20260720_0400
Revises: 20260720_0300
Create Date: 2026-07-20 20:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260720_0400"
down_revision: str | None = "20260720_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQLITE_NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _add_route_provider_references() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "model_configs",
            naming_convention=_SQLITE_NAMING_CONVENTION,
        ) as batch_op:
            batch_op.add_column(sa.Column("primary_provider_id", BigIntPK, nullable=True))
            batch_op.add_column(sa.Column("fallback_provider_id", BigIntPK, nullable=True))
            batch_op.create_foreign_key(
                "fk_model_configs_primary_provider_org",
                "model_providers",
                ["org_id", "primary_provider_id"],
                ["org_id", "id"],
                ondelete="RESTRICT",
            )
            batch_op.create_foreign_key(
                "fk_model_configs_fallback_provider_org",
                "model_providers",
                ["org_id", "fallback_provider_id"],
                ["org_id", "id"],
                ondelete="RESTRICT",
            )
        return

    op.add_column("model_configs", sa.Column("primary_provider_id", BigIntPK, nullable=True))
    op.add_column("model_configs", sa.Column("fallback_provider_id", BigIntPK, nullable=True))
    op.create_foreign_key(
        "fk_model_configs_primary_provider_org",
        "model_configs",
        "model_providers",
        ["org_id", "primary_provider_id"],
        ["org_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_model_configs_fallback_provider_org",
        "model_configs",
        "model_providers",
        ["org_id", "fallback_provider_id"],
        ["org_id", "id"],
        ondelete="RESTRICT",
    )


def _drop_route_provider_references() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "model_configs",
            naming_convention=_SQLITE_NAMING_CONVENTION,
        ) as batch_op:
            batch_op.drop_constraint(
                "fk_model_configs_fallback_provider_org", type_="foreignkey"
            )
            batch_op.drop_constraint(
                "fk_model_configs_primary_provider_org", type_="foreignkey"
            )
            batch_op.drop_column("fallback_provider_id")
            batch_op.drop_column("primary_provider_id")
        return

    op.drop_constraint(
        "fk_model_configs_fallback_provider_org", "model_configs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_model_configs_primary_provider_org", "model_configs", type_="foreignkey"
    )
    op.drop_column("model_configs", "fallback_provider_id")
    op.drop_column("model_configs", "primary_provider_id")


def upgrade() -> None:
    op.create_table(
        "model_providers",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("template_code", sa.String(length=64), nullable=True),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=1000), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("credential_source", sa.String(length=32), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("key_last_four", sa.String(length=4), nullable=True),
        sa.Column("key_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "verification_status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_error_code", sa.String(length=64), nullable=True),
        sa.Column("models", JSONVariant, nullable=True),
        sa.Column("models_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", BigIntPK, nullable=True),
        sa.Column("updated_by_id", BigIntPK, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("org_id", "code", name="uq_model_provider_org_code"),
        sa.UniqueConstraint("org_id", "id", name="uq_model_provider_org_id"),
    )
    op.create_index(op.f("ix_model_providers_org_id"), "model_providers", ["org_id"])
    _add_route_provider_references()

    op.execute(
        sa.text(
            """
            INSERT INTO model_providers (
                org_id, code, display_name, provider_type, template_code,
                protocol, base_url, enabled, sort_order, credential_source,
                verification_status, created_by_id, updated_by_id
            )
            SELECT
                orgs.id, 'deepseek', 'DeepSeek', 'preset', 'deepseek',
                'openai_compatible', 'https://api.deepseek.com', true, 0,
                'environment', 'pending',
                (SELECT MIN(users.id) FROM users WHERE users.org_id = orgs.id),
                (SELECT MIN(users.id) FROM users WHERE users.org_id = orgs.id)
            FROM orgs
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO model_providers (
                org_id, code, display_name, provider_type, template_code,
                protocol, base_url, enabled, sort_order, credential_source,
                verification_status, created_by_id, updated_by_id
            )
            SELECT
                orgs.id, 'legacy-litellm', 'Legacy LiteLLM', 'preset', NULL,
                'legacy_litellm', NULL, false, 100, 'none', 'pending',
                (SELECT MIN(users.id) FROM users WHERE users.org_id = orgs.id),
                (SELECT MIN(users.id) FROM users WHERE users.org_id = orgs.id)
            FROM orgs
            WHERE EXISTS (
                SELECT 1
                FROM model_configs
                WHERE model_configs.org_id = orgs.id
                  AND (
                    model_configs.primary_model LIKE 'litellm:%'
                    OR model_configs.fallback_model LIKE 'litellm:%'
                  )
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE model_configs
            SET primary_provider_id = (
                SELECT model_providers.id
                FROM model_providers
                WHERE model_providers.org_id = model_configs.org_id
                  AND model_providers.code = CASE
                    WHEN model_configs.primary_model LIKE 'litellm:%'
                    THEN 'legacy-litellm'
                    ELSE 'deepseek'
                  END
            ),
            fallback_provider_id = CASE
                WHEN model_configs.fallback_model IS NULL THEN NULL
                ELSE (
                    SELECT model_providers.id
                    FROM model_providers
                    WHERE model_providers.org_id = model_configs.org_id
                      AND model_providers.code = CASE
                        WHEN model_configs.fallback_model LIKE 'litellm:%'
                        THEN 'legacy-litellm'
                        ELSE 'deepseek'
                      END
                )
            END
            """
        )
    )
    op.create_index(
        op.f("ix_model_configs_primary_provider_id"),
        "model_configs",
        ["primary_provider_id"],
    )
    op.create_index(
        op.f("ix_model_configs_fallback_provider_id"),
        "model_configs",
        ["fallback_provider_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_model_configs_fallback_provider_id"), table_name="model_configs")
    op.drop_index(op.f("ix_model_configs_primary_provider_id"), table_name="model_configs")
    _drop_route_provider_references()
    op.drop_index(op.f("ix_model_providers_org_id"), table_name="model_providers")
    op.drop_table("model_providers")
