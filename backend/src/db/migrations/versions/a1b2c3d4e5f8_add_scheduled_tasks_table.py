"""add_scheduled_tasks_table

Revision ID: a1b2c3d4e5f8
Revises: 50d3747f8a93
Create Date: 2026-07-23 11:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import CHAR, LONGTEXT


revision: str = "a1b2c3d4e5f8"
down_revision: Union[str, None] = "50d3747f8a93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", CHAR(36), nullable=False),
        sa.Column("tenant_id", CHAR(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", CHAR(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("schedule_description", sa.String(255), nullable=False),
        sa.Column("cron_expression", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="UTC"),
        sa.Column("state", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(32), nullable=True),
        sa.Column("last_run_session_id", CHAR(36), sa.ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_run_error", sa.Text(), nullable=True),
        sa.Column("template_session_id", CHAR(36), sa.ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduled_tasks_tenant_user", "scheduled_tasks", ["tenant_id", "user_id"])
    op.create_index("ix_scheduled_tasks_state_next_run", "scheduled_tasks", ["state", "next_run_at"])

    # Add scheduled task notification type constants to the comment on notifications.type column
    # (no schema change to notifications — types are constants in Python code)


def downgrade() -> None:
    op.drop_table("scheduled_tasks")
