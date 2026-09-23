"""drop_fulltext_index_sessions_title

Issue #541 — remove the unused FULLTEXT index on ``sessions.title``.

The session search endpoint no longer uses ``MATCH()`` / ``AGAINST()``; it
matches ``q`` as a literal, case-insensitive substring (SQL ``LIKE``).  The
FULLTEXT index cannot serve leading-wildcard ``LIKE '%…%'`` queries, so it
provides zero benefit and only adds write overhead.

Revision ID: a9b8c7d6e5f4
Revises: dc294e2d9d83
Create Date: 2026-09-20
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, None] = "dc294e2d9d83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("idx_sessions_title_ft", table_name="sessions")


def downgrade() -> None:
    op.create_index(
        "idx_sessions_title_ft",
        "sessions",
        ["title"],
        mysql_prefix="FULLTEXT",
    )
