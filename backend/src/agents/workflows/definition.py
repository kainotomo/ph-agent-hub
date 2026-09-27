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
#
# WorkflowBranch fields:
#   - ``source`` is the step id the conditional edge leaves from.
#   - ``condition`` is the literal string ``"default"`` (unconditional fallback)
#     or an ``@``-prefixed reference from the closed condition vocabulary
#     (see ``conditions.py``).
#   - ``target`` is the step id the edge leads to.
#
# WorkflowDefinition.branches is a list of conditional edges; an empty list
# means a plain sequential chain.
# =============================================================================

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .conditions import CONDITION_PREFIX, condition_names, is_condition_reference
from .roles import is_role_reference, validate_reference


# ---------------------------------------------------------------------------
# Workflow branch
# ---------------------------------------------------------------------------

DEFAULT_BRANCH: str = "default"


class WorkflowBranch(BaseModel):
    """A conditional edge from one step to another, authored as data.

    Attributes:
        source:    Step id the edge leaves from. Must be an existing step id.
        condition: A condition reference from the closed vocabulary
                   (``@``-prefixed, see ``conditions.py``), or the literal
                   string ``"default"`` for the unconditional fallback edge.
                   ``"default"`` is deliberately *not* ``@``-prefixed: it is
                   not a predicate, it is the absence of one.
        target:    Step id the edge leads to. Must be an existing step id and
                   must differ from ``source``.
    """

    source: str
    condition: str
    target: str

    @field_validator("source")
    @classmethod
    def validate_branch_source(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Branch 'source' must be a non-empty string")
        if any(ch.isspace() for ch in v):
            raise ValueError(
                f"Branch source {v!r} must not contain whitespace"
            )
        return v

    @field_validator("target")
    @classmethod
    def validate_branch_target(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Branch 'target' must be a non-empty string")
        if any(ch.isspace() for ch in v):
            raise ValueError(
                f"Branch target {v!r} must not contain whitespace"
            )
        return v

    @field_validator("condition")
    @classmethod
    def validate_branch_condition(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Branch 'condition' must be a non-empty string")
        if any(ch.isspace() for ch in v):
            raise ValueError(
                f"Branch condition {v!r} must not contain whitespace"
            )
        if v != DEFAULT_BRANCH and not v.startswith(CONDITION_PREFIX):
            raise ValueError(
                f"Branch condition {v!r} must be the literal "
                f"'{DEFAULT_BRANCH}' or an '@'-prefixed reference"
            )
        return v


# ---------------------------------------------------------------------------
# Workflow step
# ---------------------------------------------------------------------------

class WorkflowStep(BaseModel):
    """A single step (agent) within a workflow graph.

    Attributes:
        id:                Unique identifier (executor identity; must be stable across rebuilds).
                           Must be non-empty and contain no whitespace, because MAF falls back
                           to the agent's ``name`` when the id is falsy.
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

    @field_validator("id")
    @classmethod
    def validate_step_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Step 'id' must be a non-empty string")
        if any(ch.isspace() for ch in v):
            raise ValueError(
                f"Step id {v!r} must not contain whitespace: it is the "
                f"executor identity and the key used by 'output_of:<step_id>'"
            )
        return v

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
        branches:    Conditional edges between steps; an empty list means a
                     plain sequential chain.  Each branch references a
                     condition from the closed vocabulary (``@``-prefixed)
                     or the literal ``"default"`` for the unconditional
                     fallback edge.
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
    branches: list[WorkflowBranch] = Field(
        default_factory=list,
        description="Conditional edges; an empty list means a plain sequential chain.",
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

    @model_validator(mode="after")
    def validate_branches(self) -> "WorkflowDefinition":
        step_ids = {s.id for s in self.steps}
        if not self.branches:
            return self

        default_counts: dict[str, int] = {}

        for i, branch in enumerate(self.branches):
            # Source must be an existing step
            if branch.source not in step_ids:
                raise ValueError(
                    f"Branch #{i}: source '{branch.source}' is not a step id "
                    f"(known: {', '.join(sorted(step_ids))})"
                )
            # Target must be an existing step
            if branch.target not in step_ids:
                raise ValueError(
                    f"Branch #{i}: target '{branch.target}' is not a step id "
                    f"(known: {', '.join(sorted(step_ids))})"
                )
            # Source must not equal target
            if branch.source == branch.target:
                raise ValueError(
                    f"Branch #{i}: source and target must differ "
                    f"('{branch.source}')"
                )
            # Track default branches per source
            if branch.condition == DEFAULT_BRANCH:
                default_counts[branch.source] = default_counts.get(branch.source, 0) + 1

        # At most one default branch per source
        for src, count in default_counts.items():
            if count > 1:
                raise ValueError(
                    f"Source '{src}' has {count} default branches; only one is allowed"
                )

        # Unique (source, condition) pairs
        seen: set[tuple[str, str]] = set()
        for i, branch in enumerate(self.branches):
            key = (branch.source, branch.condition)
            if key in seen:
                raise ValueError(
                    f"Duplicate branch ({branch.source}, {branch.condition!r})"
                )
            seen.add(key)

        # Each branching source must have more than one outgoing branch
        outgoing: dict[str, int] = {}
        for branch in self.branches:
            outgoing[branch.source] = outgoing.get(branch.source, 0) + 1
        for src, count in outgoing.items():
            if count <= 1:
                raise ValueError(
                    f"Source '{src}' has only one outgoing branch; "
                    f"a branching step needs at least one conditional branch "
                    f"and one default fallback"
                )

        # Check that every branching source has exactly one default branch.
        # (The "more than one outgoing branch" check above already enforces
        #  at least two, but we must also enforce *exactly one default*
        #  — a source with only conditional branches is incomplete.)
        for src in outgoing:
            if default_counts.get(src, 0) == 0:
                raise ValueError(
                    f"Source '{src}' has no default branch; "
                    f"a branching step needs one default fallback"
                )

        # Validate that each condition is a known vocabulary member.
        known = set(condition_names())
        for i, branch in enumerate(self.branches):
            if branch.condition != DEFAULT_BRANCH:
                if not is_condition_reference(branch.condition):
                    raise ValueError(
                        f"Branch #{i}: condition {branch.condition!r} must be "
                        f"the literal 'default' or an '@'-prefixed reference"
                    )
                if branch.condition not in known:
                    raise ValueError(
                        f"Unknown condition {branch.condition!r} on branch "
                        f"{branch.source} -> {branch.target}. "
                        f"Known conditions: {', '.join(condition_names())}"
                    )

        return self
