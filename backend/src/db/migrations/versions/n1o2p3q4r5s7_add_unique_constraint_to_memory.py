"""add_unique_constraint_to_memory

Revision ID: n1o2p3q4r5s7
Revises: g1h2i3j4k5l6
Create Date: 2026-07-20 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'n1o2p3q4r5s7'
down_revision: Union[str, None] = 'g1h2i3j4k5l6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove duplicates before adding unique constraint (keep the first entry
    # for each (user_id, tenant_id, key, session_id) combination).
    op.execute(
        """
        DELETE t1 FROM memory t1
        INNER JOIN memory t2
        WHERE
            t1.id > t2.id
            AND t1.user_id = t2.user_id
            AND t1.tenant_id = t2.tenant_id
            AND t1.key = t2.key
            AND ((t1.session_id = t2.session_id) OR (t1.session_id IS NULL AND t2.session_id IS NULL))
        """
    )
    op.create_unique_constraint(
        'uq_memory_user_tenant_key_session',
        'memory',
        ['user_id', 'tenant_id', 'key', 'session_id'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_memory_user_tenant_key_session',
        'memory',
        type_='unique',
    )
