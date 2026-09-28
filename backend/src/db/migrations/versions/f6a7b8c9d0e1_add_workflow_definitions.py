"""add_workflow_definitions

Workflow definitions — tenant-scoped reusable workflow templates.

Each row stores a tenant's definition of a reusable workflow: a keyed name,
a JSON description of the workflow graph, and metadata for ownership,
visibility, and integrity.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_definitions",
        sa.Column("id", mysql.CHAR(36), nullable=False),
        sa.Column("tenant_id", mysql.CHAR(36), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column(
            "visibility",
            sa.Enum("tenant", "user", name="workflow_definition_visibility_enum"),
            nullable=False,
            server_default=sa.text("'tenant'"),
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("signature_hash", sa.String(64), nullable=True),
        sa.Column("created_by", mysql.CHAR(36), nullable=True),
        sa.Column("updated_by", mysql.CHAR(36), nullable=True),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "key", name="uq_workflow_definitions_tenant_key"
        ),
    )
    op.create_index(
        "ix_wf_definitions_tenant_enabled",
        "workflow_definitions",
        ["tenant_id", "enabled"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wf_definitions_tenant_enabled", table_name="workflow_definitions"
    )
    op.drop_table("workflow_definitions")
