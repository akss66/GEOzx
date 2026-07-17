"""Add client-scoped workspaces without removing legacy account bindings.

Revision ID: 20260716_0200
Revises: 20260716_0100
Create Date: 2026-07-16 16:50:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK

revision: str = "20260716_0200"
down_revision: str | None = "20260716_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


client_status = sa.Enum("active", "archived", name="client_status")
workspace_role = sa.Enum("lead", "operator", "editor", "reviewer", name="workspace_role")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", client_status, nullable=False, server_default="active"),
        *_timestamps(),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_clients_org_id"), "clients", ["org_id"])

    op.add_column("projects", sa.Column("client_id", BigIntPK, nullable=True))
    op.create_index(op.f("ix_projects_client_id"), "projects", ["client_id"])
    op.create_foreign_key(
        "fk_projects_client_id_clients", "projects", "clients", ["client_id"], ["id"],
        ondelete="CASCADE",
    )
    op.add_column("accounts", sa.Column("client_id", BigIntPK, nullable=True))
    op.create_index(op.f("ix_accounts_client_id"), "accounts", ["client_id"])
    op.create_foreign_key(
        "fk_accounts_client_id_clients", "accounts", "clients", ["client_id"], ["id"],
        ondelete="CASCADE",
    )

    op.create_table(
        "client_memberships",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("client_id", BigIntPK, nullable=False),
        sa.Column("user_id", BigIntPK, nullable=False),
        sa.Column("role", workspace_role, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "user_id", name="uq_client_memberships_client_user"),
    )
    op.create_index(op.f("ix_client_memberships_client_id"), "client_memberships", ["client_id"])
    op.create_index(op.f("ix_client_memberships_user_id"), "client_memberships", ["user_id"])

    op.create_table(
        "project_memberships",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("project_id", BigIntPK, nullable=False),
        sa.Column("user_id", BigIntPK, nullable=False),
        sa.Column("role", workspace_role, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_memberships_project_user"),
    )
    op.create_index(op.f("ix_project_memberships_project_id"), "project_memberships", ["project_id"])
    op.create_index(op.f("ix_project_memberships_user_id"), "project_memberships", ["user_id"])

    op.create_table(
        "project_accounts",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("project_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "account_id", name="uq_project_accounts_project_account"),
    )
    op.create_index(op.f("ix_project_accounts_project_id"), "project_accounts", ["project_id"])
    op.create_index(op.f("ix_project_accounts_account_id"), "project_accounts", ["account_id"])

    op.create_table(
        "notifications",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("user_id", BigIntPK, nullable=False),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("path", sa.String(length=500), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notifications_org_id"), "notifications", ["org_id"])
    op.create_index(op.f("ix_notifications_user_id"), "notifications", ["user_id"])
    op.create_index(op.f("ix_notifications_type"), "notifications", ["type"])

    bind = op.get_bind()
    bind.execute(sa.text(
        "INSERT INTO clients (org_id, name, status, created_at, updated_at) "
        "SELECT id, '默认客户', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM orgs"
    ))
    bind.execute(sa.text(
        "UPDATE projects SET client_id = ("
        "SELECT clients.id FROM clients WHERE clients.org_id = projects.org_id "
        "ORDER BY clients.id LIMIT 1)"
    ))
    bind.execute(sa.text(
        "UPDATE accounts SET client_id = ("
        "SELECT clients.id FROM clients WHERE clients.org_id = accounts.org_id "
        "ORDER BY clients.id LIMIT 1)"
    ))
    bind.execute(sa.text(
        "INSERT INTO project_accounts (project_id, account_id, created_at, updated_at) "
        "SELECT project_id, id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM accounts "
        "WHERE project_id IS NOT NULL"
    ))
    bind.execute(sa.text(
        "INSERT INTO client_memberships (client_id, user_id, role, created_at, updated_at) "
        "SELECT clients.id, users.id, "
        "(CASE WHEN users.role = 'admin' THEN 'lead' ELSE 'operator' END)::workspace_role, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM users "
        "JOIN clients ON clients.org_id = users.org_id"
    ))

    missing = bind.execute(sa.text(
        "SELECT (SELECT COUNT(*) FROM projects WHERE client_id IS NULL) + "
        "(SELECT COUNT(*) FROM accounts WHERE client_id IS NULL)"
    )).scalar_one()
    if missing:
        raise RuntimeError("client workspace backfill left unscoped rows")


def downgrade() -> None:
    op.drop_index(op.f("ix_notifications_type"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_org_id"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_project_accounts_account_id"), table_name="project_accounts")
    op.drop_index(op.f("ix_project_accounts_project_id"), table_name="project_accounts")
    op.drop_table("project_accounts")
    op.drop_index(op.f("ix_project_memberships_user_id"), table_name="project_memberships")
    op.drop_index(op.f("ix_project_memberships_project_id"), table_name="project_memberships")
    op.drop_table("project_memberships")
    op.drop_index(op.f("ix_client_memberships_user_id"), table_name="client_memberships")
    op.drop_index(op.f("ix_client_memberships_client_id"), table_name="client_memberships")
    op.drop_table("client_memberships")
    op.drop_constraint("fk_accounts_client_id_clients", "accounts", type_="foreignkey")
    op.drop_index(op.f("ix_accounts_client_id"), table_name="accounts")
    op.drop_column("accounts", "client_id")
    op.drop_constraint("fk_projects_client_id_clients", "projects", type_="foreignkey")
    op.drop_index(op.f("ix_projects_client_id"), table_name="projects")
    op.drop_column("projects", "client_id")
    op.drop_index(op.f("ix_clients_org_id"), table_name="clients")
    op.drop_table("clients")
    workspace_role.drop(op.get_bind(), checkfirst=True)
    client_status.drop(op.get_bind(), checkfirst=True)
