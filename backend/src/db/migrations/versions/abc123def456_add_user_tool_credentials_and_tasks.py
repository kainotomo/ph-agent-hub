"""add_user_tool_credentials_and_tasks

Create user_tool_credentials table for per-user OAuth/password storage,
and add 'tasks' to the tool_type_enum on the tools table.

Revision ID: abc123def456
Revises: 635d33627468, a7b8c9d0e1f2
Create Date: 2026-06-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "abc123def456"
down_revision: Union[str, tuple[str, ...], None] = (
    "635d33627468",  # add_auto_select_tools_column_to_sessions
    "a7b8c9d0e1f2",  # add_mcp_servers_table_and_mcp_tool_type
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- Create user_tool_credentials table --------------------------------
    op.create_table(
        "user_tool_credentials",
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column("user_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("tool_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column(
            "provider",
            sa.Enum("gmail", "outlook", "imap", "google", "microsoft",
                    name="credential_provider_enum"),
            nullable=False,
        ),
        sa.Column(
            "email_address", sa.String(length=255), nullable=True,
            comment="Primary email address for this account",
        ),
        sa.Column(
            "credentials", sa.Text(length=4096), nullable=True,
            comment="Encrypted JSON — IMAP/SMTP passwords, client IDs, etc.",
        ),
        sa.Column(
            "oauth_tokens", sa.Text(length=4096), nullable=True,
            comment="Encrypted JSON — access_token, refresh_token, expires_at",
        ),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False,
            server_default=sa.text("0"),
            comment="When true, this account is used when no account_label is specified",
        ),
        sa.Column(
            "status",
            sa.Enum("active", "expired", "revoked", "error",
                    name="credential_status_enum"),
            nullable=False, server_default="active",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_user_tool_credentials_user_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id"], ["tools.id"],
            name=op.f("fk_user_tool_credentials_tool_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_tool_credentials")),
        sa.UniqueConstraint(
            "user_id", "tool_id", "email_address",
            name="uq_user_tool_credential_email",
        ),
    )

    # ---- Add 'tasks' to tool_type_enum on the tools table -----------------
    op.execute(
        "ALTER TABLE tools MODIFY COLUMN type "
        "ENUM('erpnext','membrane','custom','datetime','web_search',"
        "'fetch_url','weather','calculator','wikipedia','rss_feed',"
        "'currency_exchange','market_overview','etf_data',"
        "'stock_data','portfolio','sec_filings','pdf_extractor',"
        "'code_interpreter','sql_query','document_generation','browser',"
        "'rag_search','github','calendar','image_generation',"
        "'slack','email','mcp','tasks') NOT NULL"
    )


def downgrade() -> None:
    # ---- Drop user_tool_credentials table ----------------------------------
    op.drop_table("user_tool_credentials")

    # ---- Remove 'tasks' from tool_type_enum --------------------------------
    op.execute(
        "ALTER TABLE tools MODIFY COLUMN type "
        "ENUM('erpnext','membrane','custom','datetime','web_search',"
        "'fetch_url','weather','calculator','wikipedia','rss_feed',"
        "'currency_exchange','market_overview','etf_data',"
        "'stock_data','portfolio','sec_filings','pdf_extractor',"
        "'code_interpreter','sql_query','document_generation','browser',"
        "'rag_search','github','calendar','image_generation',"
        "'slack','email','mcp') NOT NULL"
    )
