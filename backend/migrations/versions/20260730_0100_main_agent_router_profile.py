"""Create lightweight router model profiles from main-agent profiles.

Revision ID: 20260730_0100
Revises: 20260728_0300
Create Date: 2026-07-30 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.services.model_infrastructure import ROUTER_AGENT_CODE

revision: str = "20260730_0100"
down_revision: str | None = "20260728_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO model_configs (
                org_id,
                agent_code,
                primary_provider_id,
                fallback_provider_id,
                primary_model,
                fallback_model,
                params
            )
            SELECT
                orgs.id,
                :router_agent_code,
                decision.primary_provider_id,
                decision.fallback_provider_id,
                'deepseek-v4-flash',
                decision.fallback_model,
                decision.params
            FROM orgs
            LEFT JOIN model_configs AS decision
                ON decision.org_id = orgs.id
                AND decision.agent_code = '00-decision'
            WHERE NOT EXISTS (
                SELECT 1
                FROM model_configs AS router
                WHERE router.org_id = orgs.id
                    AND router.agent_code = :router_agent_code
            )
            """
        ).bindparams(router_agent_code=ROUTER_AGENT_CODE)
    )


def downgrade() -> None:
    """Preserve data rows because pre-existing router profiles are indistinguishable.

    Older code ignores this internal workload config, so retaining it is safe.
    """
