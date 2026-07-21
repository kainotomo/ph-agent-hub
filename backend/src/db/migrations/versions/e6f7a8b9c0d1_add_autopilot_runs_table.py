"""add_autopilot_runs_table

Create the autopilot_runs table to persist autopilot execution
state across turns and server restarts (Issue #446, Phase 3).

Revision ID: p1q2r3s4t5u6
Revises: a1b2c3d4e5f7
Create Date: 2026-07-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "autopilot_runs",
        sa.Column("id", mysql.CHAR(length=36), primary_key=True, nullable=False),
        sa.Column("session_id", mysql.CHAR(length=36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, index=True, server_default="EXECUTING"),
        sa.Column("current_turn", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_turns", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("cumulative_tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cumulative_tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plan", mysql.LONGTEXT().with_variant(sa.Text, "sqlite"), nullable=True),
        sa.Column("findings", mysql.LONGTEXT().with_variant(sa.Text, "sqlite"), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("steering_instruction", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("autopilot_runs")
