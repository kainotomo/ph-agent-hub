"""add_agent_config_to_templates

Add a nullable ``agent_config`` JSON column to the ``templates`` table so a
template can carry a run budget (Issue #595):

- ``agent_config`` (JSON) — agent overrides: {max_steps, max_input_tokens}

Revision ID: t7u8v9w0x1y2
Revises: f2a3b4c5d6e7
Create Date: 2026-11-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "t7u8v9w0x1y2"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "templates",
        sa.Column(
            "agent_config",
            sa.JSON(),
            nullable=True,
            comment="Agent overrides: {max_steps, max_input_tokens}",
        ),
    )


def downgrade() -> None:
    op.drop_column("templates", "agent_config")
