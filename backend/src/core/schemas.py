# =============================================================================
# PH Agent Hub — Shared Pydantic Helpers
# =============================================================================
# Utilities for distinguishing omitted vs. explicit-null fields in update
# request bodies.
# =============================================================================

from pydantic import BaseModel


def collect_update_fields(body: BaseModel, *, skip: set[str] | None = None) -> dict:
    """Return only the fields that the caller *explicitly* provided.

    This is the canonical way to build an update-kwargs dict from a Pydantic
    ``Update`` model.  It distinguishes:

    - **Field omitted** → not in the returned dict → caller wants to **keep**
      the existing value.
    - **Field sent as ``null``** → present in the dict with value ``None`` →
      caller wants to **clear** the (nullable) column.

    Internally it uses ``model_dump(exclude_unset=True)`` which is the same
    mechanism already used by ``chat.py:update_session``.

    Parameters
    ----------
    body : BaseModel
        The Pydantic model instance from the request body.
    skip : set[str] | None
        An optional set of field names that should *not* be included in the
        returned dict — for fields that need special validation outside this
        helper (e.g. ``tenant_id`` with manager-role checks).

    Returns
    -------
    dict
        A flat dict suitable for spreading as ``**update_kwargs`` into a
        service-layer update function.
    """
    fields = body.model_dump(exclude_unset=True)
    if skip:
        for key in skip:
            fields.pop(key, None)
    return fields
