"""add_chat_runs_table

Durable terminal record for every chat run (Issue #608).  The run's outcome
used to live only in the SSE stream; this table is the authority that a run
ended, why, and whether an answer was persisted.

Revision ID: a8b9c0d1e2f3
Revises: t7u8v9w0x1y2
Create Date: 2026-11-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "t7u8v9w0x1y2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_runs",
        sa.Column("id", mysql.CHAR(length=36), primary_key=True, nullable=False),
        sa.Column(
            "session_id",
            mysql.CHAR(length=36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", mysql.CHAR(length=36), nullable=False, index=True),
        sa.Column("tenant_id", mysql.CHAR(length=36), nullable=False, index=True),
        sa.Column(
            "kind", sa.String(length=16), nullable=False, server_default="chat",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "outcome", sa.String(length=16), nullable=True, index=True,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("message_id", mysql.CHAR(length=36), nullable=True),
        sa.Column(
            "persisted", sa.Boolean(), nullable=False, server_default="0",
        ),
        sa.Column(
            "client_attached", sa.Boolean(), nullable=False, server_default="0",
        ),
        sa.Column(
            "last_event_seq", sa.Integer(), nullable=False, server_default="0",
        ),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_chat_runs_session_started",
        "chat_runs",
        ["session_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_runs_session_started", table_name="chat_runs")
    op.drop_table("chat_runs")
