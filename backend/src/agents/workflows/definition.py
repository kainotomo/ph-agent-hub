# =============================================================================
# PH Agent Hub — Workflow Definition Data Model
# =============================================================================
# Pydantic v2 models for defining workflow structure: steps, ordering, and
# per-step agent configuration.
#
# WorkflowStep fields:
#   - ``id`` is the executor identity and must be stable across rebuilds.
#   - ``name`` is display-only.
#   - ``type`` discriminates between ``"inline"`` (LLM-driven, uses
#     ``instructions``) and ``"agent"`` (pre-built agent, uses ``agent_ref``).
#   - ``agent_ref`` is required only when ``type == "agent"``.
#   - ``tool_refs`` restricts the step to a subset of the run's
#     already-resolved, tenant-scoped tool pool.  An ``@``-prefixed entry is
#     a role from the closed ``TOOL_ROLES`` vocabulary; an unprefixed entry
#     is a concrete tool reference.  An empty list means "inherit the run's
#     whole pool".  Refs are matched against MAF tool callable names at
#     build time; an unresolved ref fails the build loudly.
#   - ``context_mode`` accepts only ``"full"`` and ``"last_agent"``;
#     ``"custom"`` is deliberately excluded because it requires a
#     ``context_filter`` callable that cannot come from a definition.
# =============================================================================

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .roles import is_role_reference, validate_reference


# ---------------------------------------------------------------------------
# Workflow step
# ---------------------------------------------------------------------------

class WorkflowStep(BaseModel):
    """A single step (agent) within a workflow graph.

    Attributes:
        id:                Unique identifier (executor identity; must be stable across rebuilds).
        name:              Display-only name for the step.
        type:              Discriminator: ``"inline"`` (LLM-driven) or ``"agent"`` (pre-built).
        agent_ref:         Role reference (``@name``) required when ``type == "agent"``.
        instructions:      System-prompt required when ``type == "inline"``.
        model_ref:         Optional model role reference (``@reasoning``, etc.) or unprefixed tenant resource.
        tool_refs:         Tool references restricting this step to a subset of the run's resolved
                           tool pool.  ``@``-prefixed entries are ``TOOL_ROLES``; an unprefixed entry
                           is a concrete tool reference.  Empty means inherit the whole pool.
        reasoning_effort:  Optional CoT effort level.
        temperature:       Model temperature, clamped to [0.0, 2.0].  Default 0.7.
        input:             Description of the step's input source.
        context_mode:      How prior conversation context is passed: ``"full"`` or ``"last_agent"``.
        on_error:          How to handle step failure: ``"stop"`` or ``"continue"``.
    """

    id: str
    name: str
    type: Literal["inline", "agent"]
    agent_ref: str | None = None
    instructions: str | None = None
    model_ref: str | None = None
    tool_refs: list[str] = Field(default_factory=list)
    reasoning_effort: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    input: str = ""
    context_mode: Literal["full", "last_agent"] = "last_agent"
    on_error: Literal["stop", "continue"] = "stop"

    @field_validator("model_ref")
    @classmethod
    def validate_model_ref(cls, v: str | None) -> str | None:
        if v is None:
            return v
        validate_reference(v, "model")
        return v

    @field_validator("agent_ref")
    @classmethod
    def validate_agent_ref(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if is_role_reference(v):
            validate_reference(v, "agent")
        return v

    @field_validator("tool_refs")
    @classmethod
    def validate_tool_refs(cls, v: list[str]) -> list[str]:
        for ref in v:
            if not ref or not ref.strip():
                raise ValueError("tool_refs entries must be non-empty strings")
            validate_reference(ref, "tool")
        return v

    @model_validator(mode="after")
    def validate_step_shape(self) -> "WorkflowStep":
        if self.type == "agent" and not self.agent_ref:
            raise ValueError(
                f"Step '{self.id}': type 'agent' requires 'agent_ref'"
            )
        if self.type == "inline" and not self.instructions:
            raise ValueError(
                f"Step '{self.id}': type 'inline' requires 'instructions'"
            )
        if self.type == "agent" and self.instructions is not None:
            raise ValueError(
                f"Step '{self.id}': 'instructions' is only valid for type 'inline'"
            )
        if self.type == "inline" and self.agent_ref is not None:
            raise ValueError(
                f"Step '{self.id}': 'agent_ref' is only valid for type 'agent'"
            )
        return self


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

    @model_validator(mode="after")
    def validate_step_inputs(self) -> "WorkflowDefinition":
        step_ids = {s.id for s in self.steps}
        for step in self.steps:
            if step.input.startswith("output_of:"):
                target = step.input.split(":", 1)[1]
                if not target:
                    raise ValueError(
                        f"Step '{step.id}': 'output_of:{target}' must name a non-empty step id"
                    )
                if target not in step_ids:
                    raise ValueError(
                        f"Step '{step.id}': 'output_of:{target}' does not match any step id"
                    )
                if target == step.id:
                    raise ValueError(
                        f"Step '{step.id}': 'input' may not reference its own output"
                    )
        return self
