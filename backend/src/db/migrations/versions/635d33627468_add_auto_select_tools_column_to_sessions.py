"""Add auto_select_tools column to sessions

Revision ID: 635d33627468
Revises: c101d202e303f4
Create Date: 2026-06-01 16:40:20.738085

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '635d33627468'
down_revision: Union[str, None] = 'c101d202e303f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sessions',
        sa.Column(
            'auto_select_tools',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('0'),
        ),
    )


def downgrade() -> None:
    op.drop_column('sessions', 'auto_select_tools')
