"""add_tenant_id_to_user_tool_credentials

Add tenant_id column to user_tool_credentials for direct tenant-isolation
filtering.  Existing rows are backfilled via users.tenant_id.

Revision ID: e9f8d7c6b5a4
Revises: abc123def456
Create Date: 2026-06-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "e9f8d7c6b5a4"
down_revision: Union[str, tuple[str, ...], None] = "abc123def456"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add column as nullable first (existing rows have no value)
    op.add_column(
        "user_tool_credentials",
        sa.Column("tenant_id", mysql.CHAR(length=36), nullable=True),
    )

    # 2. Backfill existing rows by joining through the users table
    op.execute(
        """UPDATE user_tool_credentials utc
           JOIN users u ON utc.user_id = u.id
           SET utc.tenant_id = u.tenant_id
           WHERE utc.tenant_id IS NULL"""
    )

    # 3. Make the column NOT NULL now that all rows have a value
    op.alter_column(
        "user_tool_credentials",
        "tenant_id",
        existing_type=mysql.CHAR(length=36),
        nullable=False,
    )

    # 4. Add foreign key constraint
    op.create_foreign_key(
        "fk_user_tool_credentials_tenant",
        "user_tool_credentials",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_user_tool_credentials_tenant",
        "user_tool_credentials",
        type_="foreignkey",
    )
    op.drop_column("user_tool_credentials", "tenant_id")
