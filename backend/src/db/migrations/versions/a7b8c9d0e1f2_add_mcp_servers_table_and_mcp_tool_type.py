"""add_mcp_servers_table_and_mcp_tool_type

Add the mcp_servers table for MCP server connection configs,
and add 'mcp' to the tool_type_enum on the tools table.

Revision ID: a7b8c9d0e1f2
Revises: u1v2w3x4y5z6
Create Date: 2026-05-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "u1v2w3x4y5z6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- Create mcp_servers table -----------------------------------------
    op.create_table(
        "mcp_servers",
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column("tenant_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "transport",
            sa.Enum("stdio", "streamable_http", "websocket", name="mcp_transport_enum"),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("command", sa.String(length=1024), nullable=True),
        sa.Column("args", sa.JSON(), nullable=True),
        sa.Column(
            "env_vars", sa.Text(), nullable=True,
            comment="Fernet-encrypted JSON dict of env vars",
        ),
        sa.Column(
            "headers", sa.Text(), nullable=True,
            comment="Fernet-encrypted JSON dict of HTTP headers",
        ),
        sa.Column(
            "allowed_tools", sa.JSON(), nullable=True,
            comment="Null = all tools allowed; list = subset of tool names",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name=op.f("fk_mcp_servers_tenant_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_servers")),
    )

    # ---- Add 'mcp' to tool_type_enum on the tools table -------------------
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


def downgrade() -> None:
    # ---- Remove 'mcp' from tool_type_enum ---------------------------------
    op.execute(
        "ALTER TABLE tools MODIFY COLUMN type "
        "ENUM('erpnext','membrane','custom','datetime','web_search',"
        "'fetch_url','weather','calculator','wikipedia','rss_feed',"
        "'currency_exchange','market_overview','etf_data',"
        "'stock_data','portfolio','sec_filings','pdf_extractor',"
        "'code_interpreter','sql_query','document_generation','browser',"
        "'rag_search','github','calendar','image_generation',"
        "'slack','email') NOT NULL"
    )

    # ---- Drop mcp_servers table -------------------------------------------
    op.drop_table("mcp_servers")
    op.execute("DROP TYPE IF EXISTS mcp_transport_enum")
