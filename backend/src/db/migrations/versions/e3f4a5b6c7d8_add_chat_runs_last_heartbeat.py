"""add_chat_runs_last_heartbeat

Durable proof of life for a chat run (Issue #632).  A ``running`` row whose
Redis lease key blipped must not be declared ``lost`` when the process that
owns it is still alive; the heartbeat task refreshes this column so the
read-path repair and the orphan sweep can tell a live run from a dead one.

Revision ID: e3f4a5b6c7d8
Revises: d8e9f0a1b2c3
Create Date: 2026-11-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_runs",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_runs", "last_heartbeat_at")
