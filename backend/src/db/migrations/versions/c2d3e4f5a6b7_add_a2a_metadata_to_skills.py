"""add_a2a_metadata_to_skills

Add a nullable JSON column ``a2a_metadata`` to the ``skills`` table for
storing A2A Agent Card skill metadata (inputModes, outputModes, examples,
tags).  This allows the A2A server's Agent Card endpoint to declare
accurate per-skill I/O modes instead of hardcoding ``["text/plain"]``.

Revision ID: c2d3e4f5a6b7
Revises: b1a2c3d4e5f6
Create Date: 2026-06-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b1a2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column(
            "a2a_metadata",
            sa.JSON(),
            nullable=True,
            comment="A2A Agent Card per-skill metadata: {inputModes, outputModes, examples, tags}",
        ),
    )


def downgrade() -> None:
    op.drop_column("skills", "a2a_metadata")
