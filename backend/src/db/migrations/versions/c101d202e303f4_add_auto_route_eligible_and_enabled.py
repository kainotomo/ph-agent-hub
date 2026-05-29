"""add_auto_route_eligible_and_enabled

Add auto_route_eligible to models and auto_route_enabled to sessions
for the Intelligent Model Routing feature (Issue #283).

Revision ID: c101d202e303f4
Revises: 1a6a9deff1b2
Create Date: 2026-05-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c101d202e303f4"
down_revision: Union[str, Sequence[str], None] = "1a6a9deff1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column(
            "auto_route_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
            comment="Whether this model can be automatically selected by the intelligent router",
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "auto_route_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
            comment="When True, model is auto-selected on the first user message",
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "auto_route_enabled")
    op.drop_column("models", "auto_route_eligible")
