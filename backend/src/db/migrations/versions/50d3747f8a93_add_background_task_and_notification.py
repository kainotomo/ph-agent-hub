"""add_background_task_and_notification

Revision ID: 50d3747f8a93
Revises: d1e2f3a4b5c6
Create Date: 2026-07-23 10:17:42.658625

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '50d3747f8a93'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- Create notifications table (Issue #449) -------------------------
    op.create_table('notifications',
        sa.Column('id', mysql.CHAR(length=36), nullable=False),
        sa.Column('user_id', mysql.CHAR(length=36), nullable=False),
        sa.Column('tenant_id', mysql.CHAR(length=36), nullable=False),
        sa.Column('type', sa.String(length=32), nullable=False,
                  comment='Notification type: TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED'),
        sa.Column('title', sa.String(length=255), nullable=False,
                  comment="Short human-readable title"),
        sa.Column('body', sa.Text(), nullable=True,
                  comment='Optional longer description or result summary'),
        sa.Column('reference_id', mysql.CHAR(length=36), nullable=True,
                  comment='ID of the related entity (e.g. AutopilotRun.id)'),
        sa.Column('reference_type', sa.String(length=32), nullable=True,
                  comment="Entity type: 'autopilot_run', 'session'"),
        sa.Column('is_read', sa.Boolean(), nullable=False,
                  comment='Whether the user has seen this notification'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notifications_created_at'), 'notifications', ['created_at'], unique=False)
    op.create_index(op.f('ix_notifications_is_read'), 'notifications', ['is_read'], unique=False)
    op.create_index(op.f('ix_notifications_tenant_id'), 'notifications', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_notifications_type'), 'notifications', ['type'], unique=False)
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)

    # ---- Add background task columns to autopilot_runs (Issue #449) ------
    op.add_column('autopilot_runs',
        sa.Column('progress_message', sa.Text(), nullable=True,
                  comment="Latest human-readable progress message"))
    op.add_column('autopilot_runs',
        sa.Column('notification_sent', sa.Boolean(), nullable=False,
                  server_default=sa.text('0'),
                  comment='Whether the completion/failure notification has been sent'))
    op.add_column('autopilot_runs',
        sa.Column('result_summary', sa.Text(), nullable=True,
                  comment='Final result summary from task_complete() or the last response'))
    op.add_column('autopilot_runs',
        sa.Column('background_task', sa.Boolean(), nullable=False,
                  server_default=sa.text('0'),
                  comment='True if started as a user-facing background task'))


def downgrade() -> None:
    # ---- Revert autopilot_runs columns (Issue #449) ----------------------
    op.drop_column('autopilot_runs', 'background_task')
    op.drop_column('autopilot_runs', 'result_summary')
    op.drop_column('autopilot_runs', 'notification_sent')
    op.drop_column('autopilot_runs', 'progress_message')

    # ---- Drop notifications table (Issue #449) ---------------------------
    op.drop_index(op.f('ix_notifications_user_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_type'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_tenant_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_is_read'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_created_at'), table_name='notifications')
    op.drop_table('notifications')
