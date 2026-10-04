"""add_subagent_to_tool_type_enum

Add subagent to the tool_type_enum.

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-10-04

Safety note
-----------
``ALTER TABLE ... MODIFY COLUMN`` **replaces** the column definition.  If the
live enum contains any value this migration's list omits, that value is dropped
and existing rows using it are rewritten to ``''``.  ``upgrade()`` therefore
verifies that every pre-existing value is still present and aborts with a clear
error instead of corrupting data on a drifted database.
"""
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op


# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Enum values that must already exist before this migration runs.
PRIOR_VALUES: tuple[str, ...] = (
    "erpnext", "membrane", "custom", "datetime", "web_search", "fetch_url",
    "weather", "calculator", "wikipedia", "rss_feed", "currency_exchange",
    "market_overview", "etf_data", "stock_data", "stock_screener", "portfolio",
    "sec_filings", "pdf_extractor", "code_interpreter", "sql_query",
    "document_generation", "browser", "rag_search", "github", "calendar",
    "image_generation", "slack", "email", "mcp", "tasks", "a2a",
)

#: Enum values after this migration runs.
NEW_VALUES: tuple[str, ...] = PRIOR_VALUES + ("subagent",)


def enum_sql(values: Sequence[str]) -> str:
    """Render *values* as the body of a MySQL ``ENUM(...)`` definition."""
    return ",".join(f"'{v}'" for v in values)


def parse_enum_values(column_type: str) -> list[str]:
    """Extract permitted values from a MySQL ``enum(...)`` COLUMN_TYPE string."""
    return re.findall(r"'((?:[^']|'')*)'", column_type or "")


def missing_values(current: Sequence[str], required: Sequence[str]) -> list[str]:
    """Return the entries of *required* absent from *current*, in order."""
    have = set(current)
    return [value for value in required if value not in have]


def assert_enum_is_superset(current_def: str) -> None:
    """Raise unless *current_def* already permits every prior enum value.

    Pure helper so the safety rule can be unit-tested without a database.
    """
    missing = missing_values(parse_enum_values(current_def), PRIOR_VALUES)
    if missing:
        raise RuntimeError(
            "Migration c9d0e1f2a3b4 REFUSES to run: the live `tools`.`type` "
            f"enum is missing expected value(s) {missing}. Running the MODIFY "
            "now would DROP those values and corrupt existing rows. Reconcile "
            "the database schema first. "
            f"Current definition: {current_def}"
        )


def _verify_enum_is_superset() -> None:
    """Read the live column definition and apply the safety rule.

    Skipped in offline (``--sql``) mode, where no connection is available.
    """
    if context.is_offline_mode():
        return

    row = op.get_bind().execute(
        sa.text("SHOW COLUMNS FROM tools LIKE 'type'")
    ).fetchone()
    if row is None:
        raise RuntimeError(
            "Migration c9d0e1f2a3b4: could not read `tools`.`type` — is the "
            "table present?"
        )

    assert_enum_is_superset(str(row[1]))


def upgrade() -> None:
    _verify_enum_is_superset()
    op.execute(
        "ALTER TABLE tools MODIFY COLUMN type "
        f"ENUM({enum_sql(NEW_VALUES)}) NOT NULL"
    )


def downgrade() -> None:
    # Drops 'subagent'. Only safe while no `tools` row uses that value, since
    # MySQL cannot represent a removed enum member.
    op.execute(
        "ALTER TABLE tools MODIFY COLUMN type "
        f"ENUM({enum_sql(PRIOR_VALUES)}) NOT NULL"
    )
