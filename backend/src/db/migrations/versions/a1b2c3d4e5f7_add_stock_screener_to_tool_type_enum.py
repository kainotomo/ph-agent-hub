"""add_stock_screener_to_tool_type_enum

Add stock_screener to the tool_type_enum so it can be
selected when creating new Tool records.

Revision ID: a1b2c3d4e5f7
Revises: b5c6d7e8f9a0
Create Date: 2026-06-29

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tools MODIFY COLUMN type "
        "ENUM('erpnext','membrane','custom','datetime','web_search',"
        "'fetch_url','weather','calculator','wikipedia','rss_feed',"
        "'currency_exchange','market_overview','etf_data','stock_data',"
        "'stock_screener','portfolio','sec_filings','pdf_extractor',"
        "'code_interpreter','sql_query','document_generation','browser',"
        "'rag_search','github','calendar','image_generation','slack','email',"
        "'mcp','tasks','a2a') NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE tools MODIFY COLUMN type "
        "ENUM('erpnext','membrane','custom','datetime','web_search',"
        "'fetch_url','weather','calculator','wikipedia','rss_feed',"
        "'currency_exchange','market_overview','etf_data','stock_data',"
        "'portfolio','sec_filings','pdf_extractor',"
        "'code_interpreter','sql_query','document_generation','browser',"
        "'rag_search','github','calendar','image_generation','slack','email',"
        "'mcp','tasks','a2a') NOT NULL"
    )
