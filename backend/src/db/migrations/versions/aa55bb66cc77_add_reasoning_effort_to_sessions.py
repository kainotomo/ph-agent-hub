"""add_reasoning_effort_to_sessions

Revision ID: aa55bb66cc77
Revises: e1f2a3b4c5d6
Create Date: 2026-08-24 12:00:00.000000

Add reasoning_effort column to the sessions table so users can select
a per-session reasoning-effort level (e.g. for DeepSeek thinking mode)
directly in the chat window. Null means "use the model default".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'aa55bb66cc77'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sessions', sa.Column('reasoning_effort', sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column('sessions', 'reasoning_effort')
