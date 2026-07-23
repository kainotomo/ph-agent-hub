"""add_goal_based_skill_type

Add ``goal_based`` to ``skill_execution_enum`` and add 4 new nullable
columns to the ``skills`` table for Goal-Based Skills (Issue #448):

- ``goal`` (Text) — the objective the agent should achieve
- ``constraints`` (JSON) — behavioral constraints (e.g. read_only, max_cost)
- ``success_criteria`` (Text) — how to determine the task is complete
- ``agent_config`` (JSON) — per-skill agent overrides (max_turns, model)

Revision ID: a5b6c7d8e9f0
Revises: a1b2c3d4e5f8
Create Date: 2026-07-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, None] = "a1b2c3d4e5f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Expand execution_type enum to include goal_based
    op.execute(
        "ALTER TABLE skills MODIFY COLUMN execution_type "
        "ENUM('agent','workflow','prompt_based','workflow_based','goal_based') NOT NULL"
    )

    # 2. Add new nullable columns
    op.add_column(
        "skills",
        sa.Column(
            "goal",
            sa.Text(),
            nullable=True,
            comment="The objective the agent should achieve (Goal-Based Skills)",
        ),
    )
    op.add_column(
        "skills",
        sa.Column(
            "constraints",
            sa.JSON(),
            nullable=True,
            comment="Behavioral constraints as JSON array, e.g. [\"read_only\"]",
        ),
    )
    op.add_column(
        "skills",
        sa.Column(
            "success_criteria",
            sa.Text(),
            nullable=True,
            comment="How to determine the task is complete",
        ),
    )
    op.add_column(
        "skills",
        sa.Column(
            "agent_config",
            sa.JSON(),
            nullable=True,
            comment="Agent overrides: {max_turns, model}",
        ),
    )


def downgrade() -> None:
    # 1. Drop new columns
    op.drop_column("skills", "agent_config")
    op.drop_column("skills", "success_criteria")
    op.drop_column("skills", "constraints")
    op.drop_column("skills", "goal")

    # 2. Revert execution_type enum to previous set
    op.execute(
        "ALTER TABLE skills MODIFY COLUMN execution_type "
        "ENUM('agent','workflow','prompt_based','workflow_based') NOT NULL"
    )
