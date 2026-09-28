"""add_memory_scope_unique

Replaces the ineffective ``uq_memory_user_tenant_key_session`` unique key with
``uq_memory_user_tenant_key_scope``, which also covers *global* memory entries
(``session_id IS NULL``).

MySQL/MariaDB treat NULLs as distinct inside a unique index, so the previous
key did not constrain global entries at all — several rows could share the same
``(user_id, tenant_id, key)`` while ``session_id`` was NULL.  The replacement
key uses a sentinel column ``scope_session_id`` that holds ``session_id`` or
``''`` for global entries, so uniqueness is enforced for both scopes.

``scope_session_id`` is a plain column rather than a generated one because
MariaDB rejects IFNULL / COALESCE / CONCAT_WS in the GENERATED ALWAYS AS clause
(error 1901), including on VIRTUAL columns once they are indexed.  It is kept in
sync by a SQLAlchemy mapper event in ``db/orm/memory.py``.

Revision ID: b7c8d9e0f1a2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # (a) Remove any pre-existing duplicate global entries, keeping the newest
    #     row per (user_id, tenant_id, key) where session_id IS NULL.  Without
    #     this the unique key added in (e) could not be created.
    op.execute(
        """
        DELETE t1 FROM memory t1
        INNER JOIN memory t2
        WHERE
            t1.user_id = t2.user_id
            AND t1.tenant_id = t2.tenant_id
            AND t1.key = t2.key
            AND t1.session_id IS NULL
            AND t2.session_id IS NULL
            AND (
                COALESCE(t1.updated_at, t1.created_at) < COALESCE(t2.updated_at, t2.created_at)
                OR (
                    COALESCE(t1.updated_at, t1.created_at) = COALESCE(t2.updated_at, t2.created_at)
                    AND t1.id > t2.id
                )
            )
        """
    )

    # (b) Drop the constraint that never constrained global entries.  A plain
    #     DROP INDEX is used because the key may have been created by
    #     create_all() rather than by the previous migration.
    op.execute(
        "ALTER TABLE memory DROP INDEX uq_memory_user_tenant_key_session"
    )

    # (c) Add the sentinel column.  The server default keeps raw inserts (and
    #     the backfill below) valid; the ORM event overwrites it on every
    #     ORM write with the real session scope.
    op.add_column(
        'memory',
        sa.Column(
            'scope_session_id',
            mysql.CHAR(length=36),
            nullable=False,
            server_default='',
        ),
    )

    # (d) Backfill existing rows: session_id for scoped rows, '' for globals.
    op.execute("UPDATE memory SET scope_session_id = IFNULL(session_id, '')")

    # (e) Enforce uniqueness across both scopes.
    op.create_unique_constraint(
        'uq_memory_user_tenant_key_scope',
        'memory',
        ['user_id', 'tenant_id', 'key', 'scope_session_id'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_memory_user_tenant_key_scope',
        'memory',
        type_='unique',
    )
    op.drop_column('memory', 'scope_session_id')
    # Restore the previous key.  Rows removed by (a) are intentionally not
    # restored — they were duplicates and cannot be reconstructed.
    op.create_unique_constraint(
        'uq_memory_user_tenant_key_session',
        'memory',
        ['user_id', 'tenant_id', 'key', 'session_id'],
    )
