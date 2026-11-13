"""chat_runs_message_id_fk

Bind `chat_runs.message_id` to `messages.id` with ON DELETE SET NULL.
Deleting a message (or truncating the tail after one) previously left the
durable run record pointing at a row that no longer existed.  The composed
runtime view reads that column, so the database must keep it honest.

Existing dangling references are cleared before the constraint is created.

Revision ID: f7a8b9c0d1e2
Revises: a8b9c0d1e2f3
Create Date: 2026-11-13

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A foreign key validates existing rows, so clear references to messages
    # that were already hard-deleted before adding it.
    op.execute(
        """
        UPDATE chat_runs r
        LEFT JOIN messages m ON m.id = r.message_id
        SET r.message_id = NULL
        WHERE r.message_id IS NOT NULL AND m.id IS NULL
        """
    )
    op.create_foreign_key(
        "fk_chat_runs_message_id",
        "chat_runs",
        "messages",
        ["message_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_chat_runs_message_id", "chat_runs", type_="foreignkey",
    )
