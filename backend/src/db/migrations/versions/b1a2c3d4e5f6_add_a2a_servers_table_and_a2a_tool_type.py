"""add_a2a_servers_table_and_a2a_tool_type

Add the a2a_servers table for A2A (Agent-to-Agent) remote agent connection
configurations, and add 'a2a' to the tool_type_enum on the tools table.

Revision ID: b1a2c3d4e5f6
Revises: e9f8d7c6b5a4
Create Date: 2026-06-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "b1a2c3d4e5f6"
down_revision: Union[str, None] = "e9f8d7c6b5a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- Create a2a_servers table -----------------------------------------
    op.create_table(
        "a2a_servers",
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column("tenant_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column(
            "agent_card_path", sa.String(length=512), nullable=False,
            server_default="'/.well-known/agent-card.json'",
            comment="Configurable Agent Card path (default: IANA-registered well-known URI)",
        ),
        sa.Column(
            "protocol_binding",
            sa.Enum("jsonrpc", "rest", "grpc", name="a2a_protocol_binding_enum"),
            nullable=False,
        ),
        sa.Column(
            "auth_scheme",
            sa.Enum("none", "api_key", "bearer", "oauth2", name="a2a_auth_scheme_enum"),
            nullable=True,
        ),
        sa.Column(
            "auth_token", sa.Text(), nullable=True,
            comment="Fernet-encrypted auth token",
        ),
        sa.Column(
            "headers", sa.Text(), nullable=True,
            comment="Fernet-encrypted JSON dict of HTTP headers",
        ),
        sa.Column(
            "allowed_skills", sa.JSON(), nullable=True,
            comment="Null = all skills allowed; list = subset of skill IDs",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "agent_card_cache", sa.JSON(), nullable=True,
            comment="Cached AgentCard JSON from last discovery",
        ),
        sa.Column(
            "agent_card_cached_at", sa.DateTime(timezone=True), nullable=True,
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
            ["tenant_id"], ["tenants.id"],
            name=op.f("fk_a2a_servers_tenant_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_a2a_servers")),
    )

    # ---- Add 'a2a' to tool_type_enum on the tools table --------------------
    op.execute(
        "ALTER TABLE tools MODIFY COLUMN type "
        "ENUM('erpnext','membrane','custom','datetime','web_search',"
        "'fetch_url','weather','calculator','wikipedia','rss_feed',"
        "'currency_exchange','market_overview','etf_data',"
        "'stock_data','portfolio','sec_filings','pdf_extractor',"
        "'code_interpreter','sql_query','document_generation','browser',"
        "'rag_search','github','calendar','image_generation',"
        "'slack','email','mcp','tasks','a2a') NOT NULL"
    )


def downgrade() -> None:
    # ---- Remove 'a2a' from tool_type_enum ---------------------------------
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

    # ---- Drop a2a_servers table -------------------------------------------
    op.drop_table("a2a_servers")
    op.execute("DROP TYPE IF EXISTS a2a_protocol_binding_enum")
    op.execute("DROP TYPE IF EXISTS a2a_auth_scheme_enum")
