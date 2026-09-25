"""add_model_role_bindings

Issue #550 — tenant role bindings for workflow model resolution.

Developers reference logical roles (``@reasoning``) in workflow definitions;
admins bind each role to one or more of their tenant's ``models`` rows.  A
role may bind several models (a pool); resolution is deterministic and
cost-agnostic.

Revision ID: b1c2d3e4f5a6
Revises: a9b8c7d6e5f4
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_role_bindings",
        sa.Column("tenant_id", mysql.CHAR(36), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("model_id", mysql.CHAR(36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.PrimaryKeyConstraint("tenant_id", "role", "model_id"),
    )


def downgrade() -> None:
    op.drop_table("model_role_bindings")
