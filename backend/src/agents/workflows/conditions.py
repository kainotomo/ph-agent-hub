# =============================================================================
# PH Agent Hub — Closed Condition Vocabulary
# =============================================================================
# Centrally declared, closed set of named predicates used in conditional
# routing for workflow definitions.  A condition is a **closed set of named
# predicates**: definitions reference a condition *by name* (data), the
# predicate itself is a module-level primitive (code).  Because MAF records
# only the predicate's ``__name__`` in ``graph_signature``, the name and the
# function are locked together — two conditions sharing a name would be
# indistinguishable to edit classification.
#
# The vocabulary is closed and centrally declared — developers must not
# invent arbitrary condition names.  New predicates must be added here;
# tenant definitions reference them by their ``@``-prefixed key.
# =============================================================================

from __future__ import annotations

import re
from typing import Callable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONDITION_PREFIX: str = "@"

# ---------------------------------------------------------------------------
# Predicate primitives (module-level functions)
# ---------------------------------------------------------------------------


def _always(_: str) -> bool:
    """Always returns ``True``."""
    return True


def _never(_: str) -> bool:
    """Always returns ``False``."""
    return False


def _non_empty(text: str) -> bool:
    """Returns ``True`` iff the stripped argument is non-empty."""
    return bool(text.strip())


def _contains_keyword(text: str) -> bool:
    """Returns ``True`` iff *text* contains the literal substring ``"keyword"`` (case-insensitive)."""
    return "keyword" in text.lower()

# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------


def _register(ref: str, fn: Callable[[str], bool]) -> Callable[[str], bool]:
    """Register a predicate function under *ref*.

    Raises:
        ValueError: If *fn* has a disallowed ``__name__`` (lambda,
            anonymous callable, or empty string).

    Returns:
        The original function so it can still be used as a decorator target.
    """
    if fn.__name__ in ("<lambda>", "<callable>", ""):
        raise ValueError(
            f"Condition function {fn!r} has a disallowed __name__: {fn.__name!r}"
        )
    return fn


# ---------------------------------------------------------------------------
# Vocabulary validation (runs at import time)
# ---------------------------------------------------------------------------

_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_vocabulary() -> None:
    """Validate every key in ``CONDITION_PRIMITIVES`` at import time.

    Raises:
        ValueError: If a key does not start with ``CONDITION_PREFIX``,
            does not match ``^[a-z][a-z0-9_]*$``, or contains whitespace.
    """
    for key in CONDITION_PRIMITIVES:
        if not key.startswith(CONDITION_PREFIX):
            raise ValueError(
                f"Condition reference {key!r} must start with {CONDITION_PREFIX!r}"
            )
        remainder = key[len(CONDITION_PREFIX):]
        if any(ch.isspace() for ch in key):
            raise ValueError(f"Condition reference {key!r} must not contain whitespace")
        if not _REFERENCE_RE.match(remainder):
            raise ValueError(
                f"Condition reference {key!r} must match "
                f"{CONDITION_PREFIX}^[a-z][a-z0-9_]*$, got suffix {remainder!r}"
            )


# ---------------------------------------------------------------------------
# Build the registry *before* validation so the validator can iterate it
# ---------------------------------------------------------------------------

CONDITION_PRIMITIVES: dict[str, Callable[[str], bool]] = {
    "@always": _register("@always", _always),
    "@never": _register("@never", _never),
    "@non_empty": _register("@non_empty", _non_empty),
    "@contains_keyword": _register("@contains_keyword", _contains_keyword),
}

# Validate after building
_validate_vocabulary()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def condition_names() -> tuple[str, ...]:
    """Return a sorted tuple of all known condition references."""
    return tuple(sorted(CONDITION_PRIMITIVES))


def is_condition_reference(value: str) -> bool:
    """Return ``True`` if *value* looks like a condition reference (@-prefixed).

    Returns ``False`` for an empty string.
    """
    return bool(value) and value.startswith(CONDITION_PREFIX)


def resolve_condition(ref: str) -> Callable[[str], bool]:
    """Resolve a condition reference to its predicate callable.

    Raises:
        ValueError: If *ref* is not exactly a key in ``CONDITION_PRIMITIVES``.
    """
    if ref in CONDITION_PRIMITIVES:
        return CONDITION_PRIMITIVES[ref]

    known = condition_names()
    raise ValueError(
        f"Unknown condition {ref!r}. Known conditions: {', '.join(known)}"
    )
