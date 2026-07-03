"""add_github_to_credential_provider_enum

Add 'github' to the credential_provider_enum so that per-user
GitHub credentials can be stored in user_tool_credentials.

Revision ID: 7f8e9d0c1b2a
Revises: z1a2b3c4d5e6
Create Date: 2026-07-03

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f8e9d0c1b2a"
down_revision: Union[str, None] = "z1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_tool_credentials "
        "MODIFY COLUMN provider "
        "ENUM('gmail','outlook','imap','google','microsoft','erpnext','github') "
        "NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE user_tool_credentials "
        "MODIFY COLUMN provider "
        "ENUM('gmail','outlook','imap','google','microsoft','erpnext') "
        "NOT NULL"
    )
