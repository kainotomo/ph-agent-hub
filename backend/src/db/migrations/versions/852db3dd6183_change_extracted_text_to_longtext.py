"""change_extracted_text_to_longtext

Revision ID: 852db3dd6183
Revises: u1v2w3x4y5z6
Create Date: 2026-05-18 14:08:12.068720

Increases the extracted_text column from TEXT (~64 KB) to LONGTEXT (~4 GB)
to accommodate PDF text extraction output for large documents.

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "852db3dd6183"
down_revision: Union[str, None] = "u1v2w3x4y5z6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Increase extracted_text from TEXT (~64 KB) to LONGTEXT (~4 GB)."""
    op.alter_column(
        "file_uploads",
        "extracted_text",
        existing_type=mysql.TEXT(),
        type_=mysql.LONGTEXT(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "file_uploads",
        "extracted_text",
        existing_type=mysql.LONGTEXT(),
        type_=mysql.TEXT(),
        existing_nullable=True,
    )
