"""add_workflow_checkpoints

Issue #551 — durable MAF workflow checkpoints for resume.

One row is one MAF ``WorkflowCheckpoint``.  ``id`` is MAF's ``checkpoint_id``
(supplied by the engine, never generated here); ``tenant_id`` is carried
explicitly so every query can be tenant-scoped with a single filter.

Revision ID: d3e4f5a6b7c8
Revises: b1c2d3e4f5a6
Create Date: 2026-06-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_checkpoints",
        sa.Column("id", mysql.CHAR(36), nullable=False),
        sa.Column("tenant_id", mysql.CHAR(36), nullable=False),
        sa.Column("workflow_name", sa.String(320), nullable=False),
        sa.Column("graph_signature_hash", sa.String(64), nullable=False),
        sa.Column("session_id", mysql.CHAR(36), nullable=True),
        sa.Column("message_id", mysql.CHAR(36), nullable=True),
        sa.Column("previous_checkpoint_id", mysql.CHAR(36), nullable=True),
        sa.Column(
            "iteration_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("run_state", sa.String(32), nullable=True),
        sa.Column(
            "payload",
            mysql.LONGTEXT().with_variant(sa.Text, "sqlite"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_wf_checkpoints_tenant_name_created",
        "workflow_checkpoints",
        ["tenant_id", "workflow_name", "created_at"],
    )
    op.create_index(
        "ix_wf_checkpoints_tenant_session",
        "workflow_checkpoints",
        ["tenant_id", "session_id"],
    )
    op.create_index(
        "ix_wf_checkpoints_expires_at",
        "workflow_checkpoints",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wf_checkpoints_expires_at", table_name="workflow_checkpoints"
    )
    op.drop_index(
        "ix_wf_checkpoints_tenant_session", table_name="workflow_checkpoints"
    )
    op.drop_index(
        "ix_wf_checkpoints_tenant_name_created", table_name="workflow_checkpoints"
    )
    op.drop_table("workflow_checkpoints")
