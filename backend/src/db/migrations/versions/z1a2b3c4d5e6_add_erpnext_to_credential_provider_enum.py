"""add_erpnext_to_credential_provider_enum

Add 'erpnext' to the credential_provider_enum so that per-user
ERPNext credentials can be stored in user_tool_credentials.

Revision ID: z1a2b3c4d5e6
Revises: y1z2a3b4c5d6
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "z1a2b3c4d5e6"
down_revision: Union[str, None] = "y1z2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_tool_credentials "
        "MODIFY COLUMN provider "
        "ENUM('gmail','outlook','imap','google','microsoft','erpnext') "
        "NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE user_tool_credentials "
        "MODIFY COLUMN provider "
        "ENUM('gmail','outlook','imap','google','microsoft') "
        "NOT NULL"
    )
