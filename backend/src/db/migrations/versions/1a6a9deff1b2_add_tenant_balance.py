"""add_tenant_balance

Revision ID: 1a6a9deff1b2
Revises: a3b4c5d6e7f8
Create Date: 2026-05-26 17:36:42.512436

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '1a6a9deff1b2'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- balance_transactions table (audit log) ---
    op.create_table('balance_transactions',
        sa.Column('id', mysql.CHAR(length=36), nullable=False),
        sa.Column('tenant_id', mysql.CHAR(length=36), nullable=False),
        sa.Column('admin_user_id', mysql.CHAR(length=36), nullable=True),
        sa.Column('amount_eur', sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column('balance_after', sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=False),
        sa.Column('reference_type', sa.String(length=50), nullable=True),
        sa.Column('reference_id', mysql.CHAR(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['admin_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        op.f('ix_balance_transactions_tenant_id'),
        'balance_transactions', ['tenant_id'], unique=False
    )

    # --- new columns on tenants ---
    op.add_column('tenants',
        sa.Column('balance_euros', sa.Numeric(precision=12, scale=6), nullable=True)
    )
    op.add_column('tenants',
        sa.Column('warning_threshold_eur', sa.Numeric(precision=12, scale=6), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('tenants', 'warning_threshold_eur')
    op.drop_column('tenants', 'balance_euros')
    op.drop_index(
        op.f('ix_balance_transactions_tenant_id'),
        table_name='balance_transactions'
    )
    op.drop_table('balance_transactions')
