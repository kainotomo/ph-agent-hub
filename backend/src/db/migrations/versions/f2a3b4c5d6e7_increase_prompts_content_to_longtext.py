"""increase_prompts_content_to_longtext

Revision ID: f2a3b4c5d6e7
Revises: c9d0e1f2a3b4
Create Date: 2026-10-09

Increases ``prompts.content`` from TEXT (~64 KB) to LONGTEXT (~4 GB).

The saved-prompt editor failed silently when a prompt exceeded the MariaDB
``TEXT`` limit of 65,535 bytes: the INSERT/UPDATE raised error 1406
("Data too long for column 'content'") and the API returned a 500. Widening
the column removes the artificial ceiling, matching the convention already
used by other large-text columns in this project
(``file_uploads.extracted_text``, ``a2a_tasks``, ``autopilot_runs``, ...).

Widening is non-destructive: existing rows are preserved verbatim.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Increase prompts.content from TEXT (~64 KB) to LONGTEXT (~4 GB)."""
    op.alter_column(
        "prompts",
        "content",
        existing_type=mysql.TEXT(),
        type_=mysql.LONGTEXT(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "prompts",
        "content",
        existing_type=mysql.LONGTEXT(),
        type_=mysql.TEXT(),
        existing_nullable=False,
    )
