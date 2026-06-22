"""Add ``description`` column to ``tools`` table.

Revision ID: add_tool_description
Revises: a1b2c3d4e5f6_add_datetime_to_tool_type_enum
Create Date: 2026-06-22

This migration adds a nullable ``description`` text column to the
``tools`` table so that tool descriptions can be stored in the DB
and displayed in the admin UI, rather than only existing in code
docstrings.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "add_tool_description"
down_revision: Union[str, None] = "a1b2c3d4e5f6_add_datetime_to_tool_type_enum"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tools",
        sa.Column("description", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tools", "description")
