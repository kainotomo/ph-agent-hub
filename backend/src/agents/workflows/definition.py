# =============================================================================
# PH Agent Hub — Workflow Definition Data Model
# =============================================================================
# Pydantic v2 models for defining workflow structure: steps, ordering, and
# per-step agent configuration.
# =============================================================================

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from ...core.exceptions import ValidationError


# ---------------------------------------------------------------------------
# Workflow step
# ---------------------------------------------------------------------------

class WorkflowStep(BaseModel):
    """A single step (agent) within a workflow graph.

    Attributes:
        id:      Unique identifier for the step (used as executor ID).
        name:    Display name for the step.
        instructions:   System-prompt / instructions for this agent.
        model_ref:      Reference to a Model DB record (used to build the client).
        reasoning_effort:  Optional CoT effort ("low" | "medium" | "high" | "max").
        temperature:     Model temperature for this step.
        tool_names:      Optional list of tool names to inject into the step's agent.
        input:           Optional description of the step's input source.
                         Default is ``""``  (empty → the agent sees the full
                         prior conversation via MAF's default ``context_mode="full"``).
        on_error:        How to handle step failure: ``"stop"`` or ``"continue"``.
                         Defaults to ``"stop"``.
    """

    id: str
    name: str = Field(description="Display name for the step.")
    instructions: str = Field(
        default="",
        description="System-prompt / instructions for this agent.",
    )
    model_ref: str = Field(
        description="Reference to a Model DB record (model ID or name).",
    )
    reasoning_effort: str | None = Field(
        default=None,
        description="CoT effort level: low, medium, high, or max.",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Model temperature for this step.",
    )
    tool_names: list[str] = Field(
        default_factory=list,
        description="Optional list of tool names to inject.",
    )
    input: str = Field(
        default="",
        description="Optional description of input source.",
    )
    on_error: str = Field(
        default="stop",
        description="How to handle step failure: 'stop' or 'continue'.",
    )

    @field_validator("on_error")
    @classmethod
    def validate_on_error(cls, v: str) -> str:
        if v not in ("stop", "continue"):
            raise ValueError("on_error must be 'stop' or 'continue'")
        return v


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------

class WorkflowDefinition(BaseModel):
    """A complete workflow definition (ordered list of steps).

    Attributes:
        key:         Unique identifier (matches MAF_KEY in registry).
        name:        Display name for the workflow.
        description: Human-readable description.
        steps:       Ordered list of workflow steps.
    """

    key: str = Field(
        description="Unique identifier (matches MAF_KEY in registry).",
    )
    name: str = Field(
        description="Display name for the workflow.",
    )
    description: str = Field(
        default="",
        description="Human-readable description.",
    )
    steps: list[WorkflowStep] = Field(
        default_factory=list,
        description="Ordered list of workflow steps.",
    )

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, v: list[WorkflowStep]) -> list[WorkflowStep]:
        if not v:
            raise ValueError("Workflow must have at least one step")
        # Validate step IDs are unique
        ids = [s.id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Workflow step IDs must be unique")
        return v
