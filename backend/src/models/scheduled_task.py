# =============================================================================
# PH Agent Hub — Pydantic Schemas: Scheduled Tasks
# =============================================================================

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator


class ScheduledTaskCreate(BaseModel):
    """Input schema for creating a new scheduled task."""

    goal: str
    schedule_description: str
    cron_expression: str
    timezone: str = "UTC"
    template_session_id: str | None = None


class ScheduledTaskUpdate(BaseModel):
    """Input schema for updating an existing scheduled task."""

    goal: str | None = None
    schedule_description: str | None = None
    cron_expression: str | None = None
    timezone: str | None = None
    state: str | None = None


class ScheduledTaskResponse(BaseModel):
    """Output schema for a single scheduled task."""

    id: str
    tenant_id: str
    user_id: str
    goal: str
    schedule_description: str
    cron_expression: str
    timezone: str
    state: str
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    last_run_session_id: str | None = None
    last_run_error: str | None = None
    template_session_id: str | None = None
    run_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScheduledTaskListResponse(BaseModel):
    """Paginated list response."""

    items: list[ScheduledTaskResponse]
    total: int
