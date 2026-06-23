"""add_a2a_oauth2_columns

Revision ID: a4b5c6d7e8f9
Revises: 388fe6eefa8a
Create Date: 2026-06-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "388fe6eefa8a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "a2a_servers",
        sa.Column(
            "oauth2_client_id",
            sa.Text(),
            nullable=True,
            comment="OAuth2 client identifier for Authorization Code flow",
        ),
    )
    op.add_column(
        "a2a_servers",
        sa.Column(
            "oauth2_client_secret",
            sa.Text(),
            nullable=True,
            comment="Fernet-encrypted OAuth2 client secret",
        ),
    )
    op.add_column(
        "a2a_servers",
        sa.Column(
            "oauth2_authorize_url",
            sa.Text(),
            nullable=True,
            comment="OAuth2 authorization endpoint URL",
        ),
    )
    op.add_column(
        "a2a_servers",
        sa.Column(
            "oauth2_token_url",
            sa.Text(),
            nullable=True,
            comment="OAuth2 token endpoint URL",
        ),
    )
    op.add_column(
        "a2a_servers",
        sa.Column(
            "oauth2_scopes",
            sa.Text(),
            nullable=True,
            comment="Space-separated OAuth2 scope string",
        ),
    )
    op.add_column(
        "a2a_servers",
        sa.Column(
            "oauth2_tokens",
            sa.Text(),
            nullable=True,
            comment="Fernet-encrypted JSON blob of OAuth2 tokens",
        ),
    )


def downgrade() -> None:
    op.drop_column("a2a_servers", "oauth2_tokens")
    op.drop_column("a2a_servers", "oauth2_scopes")
    op.drop_column("a2a_servers", "oauth2_token_url")
    op.drop_column("a2a_servers", "oauth2_authorize_url")
    op.drop_column("a2a_servers", "oauth2_client_secret")
    op.drop_column("a2a_servers", "oauth2_client_id")
