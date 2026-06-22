"""add_a2a_tasks_table

Revision ID: 4a5b6c7d8e9f
Revises: 388fe6eefa8a
Create Date: 2026-06-22 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "4a5b6c7d8e9f"
down_revision: Union[str, None] = "388fe6eefa8a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "a2a_tasks",
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column(
            "context_id",
            mysql.CHAR(length=36),
            nullable=False,
            comment="A2A context ID — groups related tasks in a multi-turn exchange",
        ),
        sa.Column(
            "session_id",
            mysql.CHAR(length=36),
            nullable=True,
            comment="The ph-agent-hub Session backing this task's conversation",
        ),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            index=True,
            comment=(
                "One of: TASK_STATE_SUBMITTED, TASK_STATE_WORKING, "
                "TASK_STATE_INPUT_REQUIRED, TASK_STATE_AUTH_REQUIRED, "
                "TASK_STATE_COMPLETED, TASK_STATE_FAILED, "
                "TASK_STATE_CANCELED, TASK_STATE_REJECTED"
            ),
        ),
        sa.Column(
            "artifacts",
            mysql.LONGTEXT().with_variant(sa.Text, "sqlite"),
            nullable=True,
            comment="JSON array of artifact dicts (artifactId, name, parts)",
        ),
        sa.Column(
            "history",
            mysql.LONGTEXT().with_variant(sa.Text, "sqlite"),
            nullable=True,
            comment="JSON array of A2A message history entries",
        ),
        sa.Column(
            "status_message",
            mysql.LONGTEXT().with_variant(sa.Text, "sqlite"),
            nullable=True,
            comment=(
                "JSON object — error detail when FAILED, "
                "agent question when INPUT_REQUIRED, "
                "credential description when AUTH_REQUIRED"
            ),
        ),
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
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_a2a_tasks_context_id", "a2a_tasks", ["context_id"],
    )
    op.create_index(
        "ix_a2a_tasks_session_id", "a2a_tasks", ["session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_a2a_tasks_session_id", table_name="a2a_tasks")
    op.drop_index("ix_a2a_tasks_context_id", table_name="a2a_tasks")
    op.drop_table("a2a_tasks")
