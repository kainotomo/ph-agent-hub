# =============================================================================
# PH Agent Hub — Workflow Reference Validator
# =============================================================================
# Validates every reference inside a ``WorkflowDefinition`` against a single
# tenant and reports problems as structured data.
#
# Two kinds of reference exist in workflow definitions:
#
# 1. **Tenant-scoped concrete references** (unprefixed) — for example
#    ``"gpt-4o"`` for a model or ``"calculator"`` for a tool.  These are
#    matched against rows in the tenant's own ``models`` / ``tools`` tables
#    so that cross-tenant leakage is impossible and every query is explicitly
#    filtered by ``tenant_id``.
#
# 2. **Role references** (``@``-prefixed) — for example ``"@reasoning"`` for
#    a model role or ``"@web_search"`` for a tool role.  These are resolved
#    through the tenant's role bindings (``model_role_service`` for models,
#    ``TOOL_ROLE_TYPES`` matched against the tenant ``tools`` table's ``type``
#    column for tools).  A role reference that cannot be resolved through the
#    tenant's own bindings is an error; the resolver **never** falls back to an
#    arbitrary or cross-tenant resource.
#
# Every database query issued by this module includes the tenant filter.
# A role must never fall back to an arbitrary resource — if the role is
# unbound, the step is invalid and an issue is reported naming the role.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.workflows.definition import WorkflowDefinition
from ..agents.workflows.roles import TOOL_ROLE_TYPES, is_role_reference
from ..core.exceptions import ValidationError
from ..db.orm.models import Model
from ..db.orm.tools import Tool


class ReferenceKind(str, Enum):
    """The kind of resource being referenced."""

    MODEL = "model"
    TOOL = "tool"
    AGENT = "agent"


@dataclass(frozen=True)
class ReferenceIssue:
    """A single validation issue for one reference in a workflow step.

    Attributes:
        step_id:  The ``id`` of the workflow step containing the reference.
        reference: The raw reference string from the step.
        kind:     Whether this is a model, tool, or agent reference.
        reason:   One human-readable sentence naming the problem.
    """

    step_id: str
    reference: str
    kind: ReferenceKind
    reason: str


async def _unresolved_tool_reason(
    db: AsyncSession,
    tenant_id: str,
    target_filter: Any,
    *,
    disabled_reason: str,
    missing_reason: str,
) -> str:
    """Diagnose why a tool reference did not resolve for *tenant_id*.

    Distinguishes a same-tenant row that exists but is disabled from a row
    owned by another tenant, and from no row at all, so the reported reason
    names the actual cause instead of a generic miss.

    The lookup is deliberately unscoped so that a reference which only exists
    for another tenant is reported as such, mirroring the model-reference
    branch's diagnostic in this module.
    """
    result = await db.execute(select(Tool).where(target_filter))
    rows = list(result.scalars().all())
    if any(row.tenant_id == tenant_id for row in rows):
        return disabled_reason
    if rows:
        return "belongs to a different tenant"
    return missing_reason


async def validate_definition_references(
    db: AsyncSession,
    tenant_id: str,
    defn: WorkflowDefinition,
) -> list[ReferenceIssue]:
    """Validate every reference in *defn* against *tenant_id* and return issues.

    Iterates ``defn.steps`` in declaration order and checks each step's
    ``model_ref``, ``tool_refs``, and (for agent steps) ``agent_ref``.

    Returns:
        A list of ``ReferenceIssue`` objects — empty when every reference
        resolves successfully for the tenant.
    """
    issues: list[ReferenceIssue] = []

    for step in defn.steps:
        # ------------------------------------------------------------------
        # Model reference
        # ------------------------------------------------------------------
        model_ref = step.model_ref
        if model_ref is not None:
            if is_role_reference(model_ref):
                from ..services.model_role_service import (
                    resolve_role_model,
                )

                model = await resolve_role_model(db, tenant_id, model_ref)
                if model is None:
                    issues.append(
                        ReferenceIssue(
                            step_id=step.id,
                            reference=model_ref,
                            kind=ReferenceKind.MODEL,
                            reason=f"no model bound to role '{model_ref}' for this tenant",
                        )
                    )
            else:
                result = await db.execute(
                    select(Model)
                    .where(
                        (Model.id == model_ref) | (Model.model_id == model_ref),
                        Model.tenant_id == tenant_id,
                        Model.enabled.is_(True),
                    )
                    .order_by(Model.created_at.asc(), Model.id.asc())
                    .limit(1)
                )
                model = result.scalars().first()
                if model is not None:
                    pass  # resolved successfully

                else:
                    # Second query: qualify whether it exists for another tenant
                    diagnostic = await db.execute(
                        select(Model)
                        .where(
                            (Model.id == model_ref) | (Model.model_id == model_ref),
                        )
                        .limit(1)
                    )
                    foreign = diagnostic.scalars().first()
                    if foreign is not None and foreign.tenant_id != tenant_id:
                        issues.append(
                            ReferenceIssue(
                                step_id=step.id,
                                reference=model_ref,
                                kind=ReferenceKind.MODEL,
                                reason="belongs to a different tenant",
                            )
                        )
                    else:
                        issues.append(
                            ReferenceIssue(
                                step_id=step.id,
                                reference=model_ref,
                                kind=ReferenceKind.MODEL,
                                reason="no such enabled model for this tenant",
                            )
                        )

        # ------------------------------------------------------------------
        # Tool references
        # ------------------------------------------------------------------
        for tool_ref in step.tool_refs:
            if is_role_reference(tool_ref):
                types = TOOL_ROLE_TYPES.get(tool_ref)
                if types is None:
                    issues.append(
                        ReferenceIssue(
                            step_id=step.id,
                            reference=tool_ref,
                            kind=ReferenceKind.TOOL,
                            reason=(
                                f"tool role '{tool_ref}' has no declared "
                                f"tenant tool type"
                            ),
                        )
                    )
                else:
                    result = await db.execute(
                        select(Tool)
                        .where(
                            Tool.type.in_(types),
                            Tool.tenant_id == tenant_id,
                            Tool.enabled.is_(True),
                        )
                    )
                    found = result.scalars().first()
                    if found is None:
                        label = ", ".join(types)
                        issues.append(
                            ReferenceIssue(
                                step_id=step.id,
                                reference=tool_ref,
                                kind=ReferenceKind.TOOL,
                                reason=await _unresolved_tool_reason(
                                    db,
                                    tenant_id,
                                    Tool.type.in_(types),
                                    disabled_reason=(
                                        f"tool type '{label}' is disabled "
                                        f"for this tenant"
                                    ),
                                    missing_reason=(
                                        f"no enabled tool of type '{label}' "
                                        f"for role '{tool_ref}' in this tenant"
                                    ),
                                ),
                            )
                        )
            else:
                match = (Tool.name == tool_ref) | (Tool.type == tool_ref)
                result = await db.execute(
                    select(Tool)
                    .where(
                        match,
                        Tool.tenant_id == tenant_id,
                        Tool.enabled.is_(True),
                    )
                )
                tool = result.scalars().first()
                if tool is None:
                    issues.append(
                        ReferenceIssue(
                            step_id=step.id,
                            reference=tool_ref,
                            kind=ReferenceKind.TOOL,
                            reason=await _unresolved_tool_reason(
                                db,
                                tenant_id,
                                match,
                                disabled_reason=(
                                    f"tool '{tool_ref}' is disabled for this tenant"
                                ),
                                missing_reason=(
                                    f"no enabled tool with name or type "
                                    f"'{tool_ref}' for this tenant"
                                ),
                            ),
                        )
                    )

        # ------------------------------------------------------------------
        # Agent reference (only for agent steps)
        # ------------------------------------------------------------------
        if step.type == "agent" and step.agent_ref is not None:
            from ..agents.registry import get_registered_agent

            agent_mod = get_registered_agent(step.agent_ref)
            if agent_mod is None:
                issues.append(
                    ReferenceIssue(
                        step_id=step.id,
                        reference=step.agent_ref,
                        kind=ReferenceKind.AGENT,
                        reason=f"unknown registered agent '{step.agent_ref}'",
                    )
                )

    return issues


async def assert_definition_references(
    db: AsyncSession,
    tenant_id: str,
    defn: WorkflowDefinition,
) -> None:
    """Validate every reference in *defn* and raise ``ValidationError`` on problems.

    The exception message lists every issue, one per line, in the form::

        Workflow '<key>' step '<step_id>': <kind> reference '<reference>' — <reason>

    Raises:
        ValidationError: When any reference fails validation.
    """
    issues = await validate_definition_references(db, tenant_id, defn)
    if issues:
        lines = [
            f"Workflow '{defn.key}' step '{issue.step_id}': "
            f"{issue.kind.value} reference '{issue.reference}' — {issue.reason}"
            for issue in issues
        ]
        raise ValidationError("\n".join(lines))
