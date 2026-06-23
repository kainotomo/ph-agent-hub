"""merge_a2a_heads

Revision ID: b5c6d7e8f9a0
Revises: 4a5b6c7d8e9f, a4b5c6d7e8f9, add_tool_description
Create Date: 2026-06-22 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b5c6d7e8f9a0"
down_revision: Union[tuple[str, str, str], None] = (
    "4a5b6c7d8e9f",
    "a4b5c6d7e8f9",
    "add_tool_description",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
