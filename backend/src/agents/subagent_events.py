"""Pure module for folding delegated sub-agent SSE events into per-sub-agent state.

This module is intentionally free of any imports from ``src.agents.runner`` to
avoid circular imports.
"""

import json
import logging

logger = logging.getLogger(__name__)

MAX_SUBAGENT_STEPS = 50
MAX_STEP_TEXT = 2000
MAX_PROMPT_CHARS = 1000


def _clip(value, limit: int) -> str:
    """Return ``value`` as a string, truncated to ``limit`` characters."""
    if value is None:
        return ""
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit]


def _new_record(cid: str) -> dict:
    """Build the initial per-sub-agent state record for ``cid``."""
    return {
        "parent_call_id": cid,
        "name": "",
        "tool_name": "",
        "prompt": "",
        "model_role": "",
        "model_name": "",
        "tools": [],
        "omitted": [],
        "depth": 0,
        "status": "running",
        "duration_ms": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "steps": [],
        "steps_truncated": False,
        "pending_reasoning": "",
        "pending_calls": {},
    }


def _append_step(record: dict, step: dict) -> None:
    """Append ``step`` to ``record["steps"]`` unless the step cap is reached."""
    if len(record["steps"]) >= MAX_SUBAGENT_STEPS:
        record["steps_truncated"] = True
        return
    record["steps"].append(step)


def _flush_reasoning(record: dict) -> None:
    """Persist pending reasoning as a step, then clear the pending buffer."""
    pending = record["pending_reasoning"]
    if pending.strip():
        _append_step(
            record,
            {"type": "reasoning", "text": _clip(pending, MAX_STEP_TEXT)},
        )
        record["pending_reasoning"] = ""


def new_state() -> dict:
    """Return a fresh state with an empty ``subagents`` mapping."""
    return {"subagents": {}}


def accumulate_subagent_event(event_dict: dict, state: dict) -> bool:
    """Fold a ``subagent_*`` SSE event into ``state``.

    Returns ``True`` when the event was a subagent event handled by this
    function (including unparseable data), ``False`` otherwise so the caller
    can fall through to other handling.
    """
    name = event_dict.get("event", "")
    if not name.startswith("subagent_"):
        return False

    try:
        payload = json.loads(event_dict["data"])
    except Exception:
        logger.debug("Failed to parse subagent event data: %r", event_dict.get("data"))
        return True

    if not isinstance(payload, dict):
        return True

    cid = payload.get("parent_call_id") or ""
    if not cid:
        return True

    record = state["subagents"].setdefault(cid, _new_record(cid))

    if name == "subagent_start":
        record["name"] = payload.get("name", "")
        record["tool_name"] = payload.get("tool_name", "")
        record["prompt"] = payload.get("prompt", "")
        record["model_role"] = payload.get("model_role", "")
        record["model_name"] = payload.get("model_name", "")
        record["tools"] = list(payload.get("tools") or [])
        record["omitted"] = list(payload.get("omitted") or [])
        record["depth"] = int(payload.get("depth") or 0)
        record["status"] = "running"
    elif name == "subagent_reasoning_token":
        record["pending_reasoning"] += payload.get("delta", "") or ""
    elif name == "subagent_token":
        pass
    elif name == "subagent_tool_start":
        _flush_reasoning(record)
        _append_step(
            record,
            {
                "type": "function_call",
                "name": payload.get("tool_name", ""),
                "arguments": payload.get("arguments", {}) or {},
                "id": payload.get("tool_call_id", ""),
            },
        )
        record["pending_calls"][payload.get("tool_call_id", "")] = {
            "name": payload.get("tool_name", "")
        }
    elif name == "subagent_tool_result":
        _flush_reasoning(record)
        _append_step(
            record,
            {
                "type": "function_result",
                "name": payload.get("tool_name", ""),
                "output": _clip(payload.get("output"), MAX_STEP_TEXT),
                "is_error": not bool(payload.get("success", True)),
            },
        )
    elif name == "subagent_complete":
        _flush_reasoning(record)
        record["status"] = "complete" if payload.get("success", True) else "error"
        record["duration_ms"] = payload.get("duration_ms")
        record["tokens_in"] = int(payload.get("tokens_in") or 0)
        record["tokens_out"] = int(payload.get("tokens_out") or 0)
    elif name == "subagent_error":
        _flush_reasoning(record)
        record["status"] = "error"
        record["error"] = _clip(payload.get("message", ""), MAX_STEP_TEXT)

    return True


def get_payload_for_call(state: dict, tool_call_id: str) -> dict | None:
    """Return a copy of the record for ``tool_call_id``, or ``None``."""
    if not tool_call_id or tool_call_id not in state["subagents"]:
        return None

    record = state["subagents"][tool_call_id]
    payload = {
        "name": record["name"],
        "tool_name": record["tool_name"],
        "prompt": _clip(record["prompt"], MAX_PROMPT_CHARS),
        "model_role": record["model_role"],
        "model_name": record["model_name"],
        "tools": list(record["tools"]),
        "omitted": list(record["omitted"]),
        "depth": record["depth"],
        "status": record["status"],
        "duration_ms": record["duration_ms"],
        "tokens_in": record["tokens_in"],
        "tokens_out": record["tokens_out"],
        "steps": list(record["steps"]),
        "steps_truncated": record["steps_truncated"],
    }
    if record.get("error"):
        payload["error"] = record["error"]
    return payload
