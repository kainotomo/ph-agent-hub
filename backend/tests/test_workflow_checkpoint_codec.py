# =============================================================================
# PH Agent Hub — Workflow Checkpoint Codec Tests
# =============================================================================
# Unit tests for the DB-storage checkpoint codec: JSON envelope encoding and
# restricted decoding via MAF's own checkpoint codec.
# =============================================================================

import pytest

from agent_framework import WorkflowCheckpoint, WorkflowCheckpointException

from src.agents.workflows.checkpoint_codec import (
    assert_codec_available,
    decode_checkpoint,
    encode_checkpoint,
)

pytestmark = pytest.mark.unit


def test_round_trip():
    checkpoint = WorkflowCheckpoint(
        workflow_name="wf",
        graph_signature_hash="abc123",
        state={"a": 1, "b": ["x", "y"], "c": {"d": "e"}},
    )

    payload = encode_checkpoint(checkpoint)
    restored = decode_checkpoint(payload)

    assert restored.workflow_name == checkpoint.workflow_name
    assert restored.graph_signature_hash == checkpoint.graph_signature_hash
    assert restored.checkpoint_id == checkpoint.checkpoint_id
    assert restored.state == checkpoint.state


def test_non_json_payload_raises():
    with pytest.raises(WorkflowCheckpointException):
        decode_checkpoint("not-json")


def test_json_but_wrong_shape_raises():
    with pytest.raises(WorkflowCheckpointException):
        decode_checkpoint("[1, 2, 3]")


def test_missing_required_fields_raises():
    with pytest.raises(WorkflowCheckpointException):
        decode_checkpoint('{"checkpoint_id": "x"}')


def test_assert_codec_available():
    assert_codec_available()
