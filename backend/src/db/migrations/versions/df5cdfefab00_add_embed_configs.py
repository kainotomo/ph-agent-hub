"""add_embed_configs

Revision ID: df5cdfefab00
Revises: 4ffaa9dfdcb5
Create Date: 2026-05-25 16:55:40.935294

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'df5cdfefab00'
down_revision: Union[str, None] = '4ffaa9dfdcb5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade() -> None:
    op.create_table('embed_configs',
        sa.Column('id', mysql.CHAR(length=36), nullable=False),
        sa.Column('tenant_id', mysql.CHAR(length=36), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('guest_token_hash', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('theme', sa.JSON(), nullable=True),
        sa.Column('feature_flags', sa.JSON(), nullable=True),
        sa.Column('default_model_id', mysql.CHAR(length=36), nullable=True),
        sa.Column('default_skill_id', mysql.CHAR(length=36), nullable=True),
        sa.Column('default_template_id', mysql.CHAR(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['default_model_id'], ['models.id'], ),
        sa.ForeignKeyConstraint(['default_skill_id'], ['skills.id'], ),
        sa.ForeignKeyConstraint(['default_template_id'], ['templates.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('embed_configs')
