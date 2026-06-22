"""add_a2a_resilience_config_and_call_logs

Revision ID: 388fe6eefa8a
Revises: c2d3e4f5a6b7
Create Date: 2026-06-22 09:37:23.298253

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "388fe6eefa8a"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create a2a_call_logs table for structured A2A call observability
    op.create_table(
        "a2a_call_logs",
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column("tenant_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("a2a_server_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("a2a_server_name", sa.String(length=255), nullable=True),
        sa.Column("skill_id", sa.String(length=255), nullable=True),
        sa.Column("session_id", mysql.CHAR(length=36), nullable=True),
        sa.Column(
            "trace_id",
            sa.String(length=36),
            nullable=False,
            comment="Correlation ID for the call chain",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            comment="One of: success, timeout, error, circuit_open",
        ),
        sa.Column(
            "latency_ms",
            sa.Integer(),
            nullable=True,
            comment="Call duration in milliseconds",
        ),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            comment="Number of retry attempts made",
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment="Error detail if status is not success",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Add resilience config columns to a2a_servers
    op.add_column(
        "a2a_servers",
        sa.Column(
            "retry_max_attempts",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=True,
            comment="Max retry attempts for transient errors",
        ),
    )
    op.add_column(
        "a2a_servers",
        sa.Column(
            "retry_backoff_base_seconds",
            sa.Float(),
            server_default=sa.text("1.0"),
            nullable=True,
            comment="Base seconds for exponential backoff",
        ),
    )
    op.add_column(
        "a2a_servers",
        sa.Column(
            "retry_backoff_max_seconds",
            sa.Float(),
            server_default=sa.text("60.0"),
            nullable=True,
            comment="Max seconds for exponential backoff",
        ),
    )
    op.add_column(
        "a2a_servers",
        sa.Column(
            "timeout_connect_seconds",
            sa.Float(),
            server_default=sa.text("30.0"),
            nullable=True,
            comment="HTTP connect timeout in seconds",
        ),
    )
    op.add_column(
        "a2a_servers",
        sa.Column(
            "timeout_read_seconds",
            sa.Float(),
            server_default=sa.text("300.0"),
            nullable=True,
            comment="HTTP read timeout for non-streaming calls",
        ),
    )
    op.add_column(
        "a2a_servers",
        sa.Column(
            "timeout_stream_seconds",
            sa.Float(),
            server_default=sa.text("600.0"),
            nullable=True,
            comment="HTTP read timeout for streaming calls",
        ),
    )
    op.add_column(
        "a2a_servers",
        sa.Column(
            "circuit_breaker_threshold",
            sa.Integer(),
            server_default=sa.text("5"),
            nullable=True,
            comment="Consecutive failures to trip circuit breaker",
        ),
    )
    op.add_column(
        "a2a_servers",
        sa.Column(
            "circuit_breaker_window_seconds",
            sa.Integer(),
            server_default=sa.text("60"),
            nullable=True,
            comment="Time window in seconds to reset failure count",
        ),
    )
    op.add_column(
        "a2a_servers",
        sa.Column(
            "circuit_breaker_cooldown_seconds",
            sa.Integer(),
            server_default=sa.text("300"),
            nullable=True,
            comment="Cooldown in seconds before probe attempt",
        ),
    )


def downgrade() -> None:
    op.drop_column("a2a_servers", "circuit_breaker_cooldown_seconds")
    op.drop_column("a2a_servers", "circuit_breaker_window_seconds")
    op.drop_column("a2a_servers", "circuit_breaker_threshold")
    op.drop_column("a2a_servers", "timeout_stream_seconds")
    op.drop_column("a2a_servers", "timeout_read_seconds")
    op.drop_column("a2a_servers", "timeout_connect_seconds")
    op.drop_column("a2a_servers", "retry_backoff_max_seconds")
    op.drop_column("a2a_servers", "retry_backoff_base_seconds")
    op.drop_column("a2a_servers", "retry_max_attempts")
    op.drop_table("a2a_call_logs")
