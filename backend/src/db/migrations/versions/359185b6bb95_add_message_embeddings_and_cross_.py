"""add_message_embeddings_and_cross_session_memory_config

Revision ID: 359185b6bb95
Revises: 852db3dd6183
Create Date: 2026-05-22 07:03:02.644429

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '359185b6bb95'
down_revision: Union[str, None] = '852db3dd6183'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- message_embeddings table (cross-session memory, Issue #229) ------
    op.create_table('message_embeddings',
        sa.Column('id', mysql.CHAR(length=36), nullable=False),
        sa.Column('message_id', mysql.CHAR(length=36), nullable=False),
        sa.Column('user_id', mysql.CHAR(length=36), nullable=False),
        sa.Column('tenant_id', mysql.CHAR(length=36), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('embedding_json', sa.JSON(), nullable=True),
        sa.Column('model', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # ---- Cross-session memory config columns on skills --------------------
    op.add_column('skills', sa.Column('cross_session_retrieval_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')))
    op.add_column('skills', sa.Column('cross_session_max_snippets', sa.Integer(), nullable=False, server_default=sa.text('3')))
    op.add_column('skills', sa.Column('cross_session_min_score', sa.Float(), nullable=False, server_default=sa.text('0.70')))

    # ---- Cross-session memory toggle on sessions (tri-state: NULL=inherit) -
    op.add_column('sessions', sa.Column('cross_session_retrieval_enabled', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_table('message_embeddings')
    op.drop_column('skills', 'cross_session_min_score')
    op.drop_column('skills', 'cross_session_max_snippets')
    op.drop_column('skills', 'cross_session_retrieval_enabled')
    op.drop_column('sessions', 'cross_session_retrieval_enabled')