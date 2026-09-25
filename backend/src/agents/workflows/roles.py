# =============================================================================
# PH Agent Hub — Closed Role Vocabulary
# =============================================================================
# Centrally declared, closed set of role identifiers used in workflow
# definitions.  Developers may reference only roles defined here.  An
# `@`-prefixed value (e.g. ``"@reasoning"``) denotes a logical role whose
# concrete resource is bound by tenant configuration; an unprefixed value
# (e.g. ``"gpt-4o"``) denotes a concrete tenant resource, validated on a
# different path.
#
# The vocabulary is closed and centrally declared — developers must not
# invent arbitrary role names.  Tenant role-to-resource bindings are
# resolved elsewhere (issue #550).
#
# ``TOOL_ROLES`` is declared here for issue #550 and is not consumed in
# this issue.
# =============================================================================

REFERENCE_PREFIX: str = "@"

MODEL_ROLES: frozenset[str] = frozenset({"@reasoning", "@general", "@fast"})

TOOL_ROLES: frozenset[str] = frozenset({"@web_search"})

AGENT_ROLES: frozenset[str] = frozenset()

_VOCABULARIES: dict[str, frozenset[str]] = {
    "model": MODEL_ROLES,
    "tool": TOOL_ROLES,
    "agent": AGENT_ROLES,
}


def is_role_reference(value: str) -> bool:
    """Return ``True`` if *value* looks like a role reference (@-prefixed)."""
    return value.startswith(REFERENCE_PREFIX)


def known_roles(kind: str) -> frozenset[str]:
    """Return the set of known role references for *kind* (``"model"``,
    ``"tool"``, or ``"agent"``).

    Raises:
        ValueError: If *kind* is not a recognised reference kind.
    """
    if kind not in _VOCABULARIES:
        raise ValueError(f"Unknown reference kind '{kind}'")
    return _VOCABULARIES[kind]


def validate_reference(value: str, kind: str) -> None:
    """Validate that a reference value is either an unprefixed concrete
    tenant resource or a known role for the given *kind*.

    An unprefixed value (e.g. ``"gpt-4o"``) is accepted immediately — it is
    a concrete tenant resource reference validated on a different path.

    An ``@``-prefixed value must be present in the declared vocabulary for
    *kind*; otherwise a :exc:`ValueError` is raised.

    Raises:
        ValueError: If the value is an ``@``-prefixed role that is not
            known, or if *kind* is not a recognised reference kind.
    """
    if not is_role_reference(value):
        return  # concrete tenant resource — validated elsewhere

    known = known_roles(kind)
    if value not in known:
        raise ValueError(
            f"Unknown {kind} role '{value}'. "
            f"Known {kind} roles: "
            f"{', '.join(sorted(known)) or '(none)'}"
        )
