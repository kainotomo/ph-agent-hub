"""fix_user_tool_preferences_pk

Drop the orphaned `id` primary key column from user_tool_preferences
and replace it with a composite primary key (user_id, tool_id).

The original migration (c3d4e5f6a7b8) was modified after being applied
to the database, leaving the `id` column with no default value in the
actual schema.  SQLAlchemy's ORM model does not define an `id` column,
so inserts fail with:
    OperationalError: (1364, "Field 'id' doesn't have a default value")

This migration aligns the real table with the ORM model.

Revision ID: d1e2f3a4b5c6
Revises: 930b42d7f5c0
Create Date: 2026-07-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "930b42d7f5c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(conn, column: str) -> bool:
    """Check whether `user_tool_preferences` still has an orphaned column."""
    row = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'user_tool_preferences' "
            "AND COLUMN_NAME = :col"
        ),
        {"col": column},
    ).scalar()
    return row > 0


def _has_index(conn, index: str) -> bool:
    """Check whether an index exists on user_tool_preferences."""
    row = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'user_tool_preferences' "
            "AND INDEX_NAME = :idx"
        ),
        {"idx": index},
    ).scalar()
    return row > 0


def upgrade() -> None:
    """Align user_tool_preferences with the ORM model (composite PK).

    The original migration (c3d4e5f6a7b8) was modified after being applied
    to some databases, leaving an orphaned ``id`` column.  Fresh databases
    already have the correct composite PK, so this is a no-op there.
    """
    conn = op.get_bind()

    if _has_column(conn, "id"):
        conn.execute(
            sa.text(
                "ALTER TABLE user_tool_preferences "
                "DROP PRIMARY KEY, "
                "DROP COLUMN id, "
                "ADD PRIMARY KEY (user_id, tool_id)"
            )
        )

    if _has_index(conn, "idx_user_tool"):
        op.drop_index("idx_user_tool", table_name="user_tool_preferences")


def downgrade() -> None:
    """Revert to the original schema with an ``id`` PK column."""
    conn = op.get_bind()

    if not _has_column(conn, "id"):
        conn.execute(
            sa.text(
                "ALTER TABLE user_tool_preferences "
                "DROP PRIMARY KEY, "
                "ADD COLUMN id CHAR(36) NOT NULL PRIMARY KEY, "
                "ADD INDEX idx_user_tool (user_id, tool_id)"
            )
        )
