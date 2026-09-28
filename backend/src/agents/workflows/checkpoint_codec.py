# =============================================================================
# PH Agent Hub — Workflow Checkpoint Codec
# =============================================================================
# Serializes and deserializes MAF ``WorkflowCheckpoint`` payloads for database
# storage.
#
# The MAF checkpoint envelope is JSON with base64-encoded pickles embedded
# inside it: primitives and collections stay human-readable JSON, while
# non-JSON-native values are pickled by MAF and carried as marked base64
# strings.  Decoding therefore reverses MAF's encoding — it does not build its
# own.
#
# MAF's own restricted unpickler is used: ``decode_checkpoint_value`` is always
# called with an explicit ``allowed_types`` frozenset, which selects MAF's
# ``RestrictedUnpickler``.  A bare ``pickle.loads`` is never acceptable here
# because checkpoint bytes are database-resident data that must be
# reconstructed under an allowlist, never with arbitrary class instantiation.
#
# This module imports from a PRIVATE MAF path
# (``agent_framework._workflows._checkpoint_encoding``), which is pinned by
# ``backend/requirements.txt`` (``agent-framework==1.19.0``).  A MAF upgrade may
# rename or remove that path; ``assert_codec_available`` exists to fail loudly
# with actionable guidance if it does.
#
# Fallback: if that private path disappears on a MAF upgrade,
# ``agent-framework-azure-cosmos`` ships a supported distributed
# ``CheckpointStorage`` that can replace this codec-based persistence path.
# =============================================================================

from __future__ import annotations

import json
from typing import Any

from agent_framework import WorkflowCheckpoint, WorkflowCheckpointException

# Place to add DSH application types as "module:qualname" strings.  These are
# passed as ``allowed_types`` to MAF's ``decode_checkpoint_value`` so that
# application-defined values stored in checkpoint state can be reconstructed
# under MAF's RestrictedUnpickler.  Empty by default: only MAF's built-in safe
# set (primitives, datetime, uuid, framework types, ...) is allowed today.
ALLOWED_CHECKPOINT_TYPES: frozenset[str] = frozenset()


def _codec() -> tuple[Any, Any]:
    """Import and return (encode_checkpoint_value, decode_checkpoint_value)."""
    # Imported inside the function so a missing private path never breaks
    # module import; callers surface it via assert_codec_available().
    from agent_framework._workflows._checkpoint_encoding import (
        decode_checkpoint_value,
        encode_checkpoint_value,
    )

    return encode_checkpoint_value, decode_checkpoint_value


def assert_codec_available() -> None:
    """Raise RuntimeError if the pinned MAF private codec path is unavailable."""
    try:
        _codec()
    except ImportError as exc:
        raise RuntimeError(
            "agent_framework._workflows._checkpoint_encoding is unavailable. "
            "MAF was upgraded and the codec path changed; backend/requirements.txt "
            "pins agent-framework==1.19.0, which previously provided this private "
            "path. Either re-pin to a compatible MAF version or replace this "
            "codec-based persistence with the supported distributed "
            "CheckpointStorage shipped by agent-framework-azure-cosmos."
        ) from exc


def encode_checkpoint(checkpoint: Any) -> str:
    """Encode a WorkflowCheckpoint to a JSON string for database storage."""
    checkpoint_id = getattr(checkpoint, "checkpoint_id", None)
    try:
        encode_checkpoint_value, _ = _codec()
        encoded = encode_checkpoint_value(checkpoint.to_dict())
        return json.dumps(encoded)
    except Exception as exc:
        suffix = f" (checkpoint_id={checkpoint_id})" if checkpoint_id is not None else ""
        raise WorkflowCheckpointException(
            f"Failed to encode checkpoint{suffix}: {exc}"
        ) from exc


def decode_checkpoint(payload: str) -> Any:
    """Decode a JSON payload string back into a WorkflowCheckpoint."""
    try:
        _, decode_checkpoint_value = _codec()
        decoded = json.loads(payload)
        # Never pass allowed_types=None: that would fall back to MAF's
        # unrestricted pickle.loads path.  ALLOWED_CHECKPOINT_TYPES is always
        # an explicit frozenset so MAF's RestrictedUnpickler is selected.
        value = decode_checkpoint_value(decoded, allowed_types=ALLOWED_CHECKPOINT_TYPES)
        return WorkflowCheckpoint.from_dict(value)
    except (json.JSONDecodeError, WorkflowCheckpointException) as exc:
        raise WorkflowCheckpointException(
            f"Failed to decode checkpoint payload: {exc}"
        ) from exc
    except Exception as exc:
        raise WorkflowCheckpointException(
            f"Failed to decode checkpoint payload: {exc}"
        ) from exc
