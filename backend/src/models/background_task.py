# =============================================================================
# PH Agent Hub — Pydantic Schemas: Background Tasks
# =============================================================================

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class BackgroundTaskResponse(BaseModel):
    """A background task, backed by an AutopilotRun with background_task=True."""

    id: str
    session_id: str
    goal: str
    state: str
    current_turn: int
    max_turns: int
    progress_message: str | None = None
    result_summary: str | None = None
    cumulative_tokens_in: int = 0
    cumulative_tokens_out: int = 0
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BackgroundTaskListResponse(BaseModel):
    items: list[BackgroundTaskResponse]
    total: int


class BackgroundTaskCancelResponse(BaseModel):
    message: str = "Task cancelled"
    task_id: str


class StartBackgroundTaskRequest(BaseModel):
    content: str
    file_ids: list[str] | None = None
    max_turns: int | None = None
