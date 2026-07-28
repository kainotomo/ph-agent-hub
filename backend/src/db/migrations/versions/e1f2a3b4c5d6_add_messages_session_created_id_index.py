"""add_messages_session_created_id_index

Add composite index on ``messages(session_id, created_at DESC, id DESC)``
to support cursor-based pagination of long conversations (Issue #497).

Revision ID: e1f2a3b4c5d6
Revises: a5b6c7d8e9f0
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "a5b6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_messages_session_created_id",
        "messages",
        ["session_id", sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_messages_session_created_id", table_name="messages")
