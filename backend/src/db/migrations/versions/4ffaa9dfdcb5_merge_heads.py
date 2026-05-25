"""merge_heads

Revision ID: 4ffaa9dfdcb5
Revises: 359185b6bb95, 420d7dfd247c
Create Date: 2026-05-25 16:55:25.856591

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4ffaa9dfdcb5'
down_revision: Union[str, None] = ('359185b6bb95', '420d7dfd247c')
branch_labels: Union[str, Sequence[str], None] = ('merge_heads',)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
