"""add_user_notification_prefs

Per-user browser notification preferences.  One JSON column on ``users``
so new toggles do not need another migration; the API normalises it
against a typed default set (see ``services/user_preferences.py``).

Revision ID: c4d5e6f7a8b9
Revises: f7a8b9c0d1e2
Create Date: 2026-11-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "notification_prefs",
            sa.JSON(),
            nullable=True,
            comment="Per-user browser notification preferences (JSON)",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "notification_prefs")
