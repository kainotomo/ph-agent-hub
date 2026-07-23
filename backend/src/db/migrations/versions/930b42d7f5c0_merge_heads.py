"""merge_heads

Revision ID: 930b42d7f5c0
Revises: 7f8e9d0c1b2a, n1o2p3q4r5s7, e6f7a8b9c0d1
Create Date: 2026-07-21 07:44:23.904227

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '930b42d7f5c0'
down_revision: Union[str, None] = ('7f8e9d0c1b2a', 'n1o2p3q4r5s7', 'e6f7a8b9c0d1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
