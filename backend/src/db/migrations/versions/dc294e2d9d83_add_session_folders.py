"""add_session_folders

Revision ID: dc294e2d9d83
Revises: aa55bb66cc77
Create Date: 2026-09-19 00:00:00.000000

Issue #526 — introduce folders in the chat left sidebar.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'dc294e2d9d83'
down_revision: Union[str, None] = 'aa55bb66cc77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # User-scoped, single-level session folders (Issue #526)
    op.create_table(
        'folders',
        sa.Column('id', mysql.CHAR(36), nullable=False),
        sa.Column('tenant_id', mysql.CHAR(36), nullable=False),
        sa.Column('user_id', mysql.CHAR(36), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('color', sa.String(20), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'name', name='uq_folders_user_id_name'),
    )
    op.create_index('ix_folders_user_id', 'folders', ['user_id'])

    # A session belongs to at most one folder; NULL means "Unfiled".
    op.add_column('sessions', sa.Column('folder_id', mysql.CHAR(36), nullable=True))
    op.create_index('ix_sessions_folder_id', 'sessions', ['folder_id'])
    op.create_foreign_key(
        'fk_sessions_folder_id_folders',
        'sessions',
        'folders',
        ['folder_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_sessions_folder_id_folders', 'sessions', type_='foreignkey')
    op.drop_index('ix_sessions_folder_id', table_name='sessions')
    op.drop_column('sessions', 'folder_id')
    op.drop_index('ix_folders_user_id', table_name='folders')
    op.drop_table('folders')
