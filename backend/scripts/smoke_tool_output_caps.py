#!/usr/bin/env python
"""Smoke test for the tool-output caps (Issue #584).

Run inside the backend container, from /app:

    docker exec -i phagent-hub-backend-1 python scripts/smoke_tool_output_caps.py

It drives the real code paths with a payload larger than the storage cap:

    SSE tool_result -> stored part -> list projection -> step detail -> history replay

The throwaway session it creates is deleted at the end, so it is safe to run
against a development database (never against production).  Exits non-zero on
the first failure so it can gate a deploy.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

# Allow "python scripts/smoke_tool_output_caps.py" from the backend root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from httpx import ASGITransport
from sqlalchemy import delete, select

from src.agents.runner import (
    HISTORY_TOOL_RESULT_MAX_CHARS,
    STORAGE_TOOL_OUTPUT_MAX_CHARS,
    _build_history_string,
    _extract_message_text,
    _maybe_accumulate_tool_events,
)
from src.api.chat import _build_content_summary
from src.core.jwt import create_access_token
from src.db.base import AsyncSessionLocal
from src.db.orm.messages import Message
from src.db.orm.sessions import Session
from src.db.orm.users import User
from src.main import app

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def big_output() -> str:
    """A single-line JSON payload well past the storage cap."""
    rows = [
        {"row": i, "revenue": i * 1000, "note": "n" * 60}
        for i in range(400)
    ]
    return json.dumps({"symbol": "AAPL", "statements": rows, "text": "T" * 6000})


async def main() -> int:
    output = big_output()
    check(
        "payload exceeds the storage cap",
        len(output) > STORAGE_TOOL_OUTPUT_MAX_CHARS,
        f"{len(output):,} chars",
    )

    # 1. SSE tool_result -> stored part
    event = {
        "event": "tool_result",
        "data": json.dumps({
            "tool_name": "get_financials",
            "output": output,
            "success": True,
            "tool_call_id": "smoke-1",
        }),
    }
    segments = _maybe_accumulate_tool_events(event, [])
    check("tool_result becomes one segment", len(segments) == 1)
    part = segments[0]
    stored = part["output"]

    check("stored part carries a marker", "[truncated:" in stored, stored[-90:])
    check(
        "stored length is the cap plus the marker",
        STORAGE_TOOL_OUTPUT_MAX_CHARS <= len(stored) <= STORAGE_TOOL_OUTPUT_MAX_CHARS + 200,
        f"{len(stored):,} chars (cap {STORAGE_TOOL_OUTPUT_MAX_CHARS:,})",
    )
    check(
        "stored part states the real total",
        f"of {len(output):,} chars shown" in stored,
    )

    # 2. list projection (what the chat list endpoint ships to the UI)
    projected = _build_content_summary([part])[0]
    check("list projection drops the body", "output" not in projected)
    check(
        "list projection reports the stored size",
        projected.get("output_chars") == len(stored),
        f"output_chars={projected.get('output_chars'):,}",
    )

    # 3. history replay (what a later turn sends back to the model)
    replay = _extract_message_text([part])
    check("replay is clipped, not silent", "head+tail" in replay)
    check(
        "replay states the real total",
        f"{HISTORY_TOOL_RESULT_MAX_CHARS:,} of {len(stored):,} chars shown" in replay,
    )

    history = _build_history_string(
        [
            {"sender": "user", "content": [{"type": "text", "text": "hi"}], "summarized": False},
            {"sender": "assistant", "content": [part], "summarized": False},
        ],
        1_000_000,
    )
    check("history includes the tool result", "[Tool result from get_financials:" in history)
    check("history carries the replay marker", "head+tail" in history)

    # 4. API round-trip: list endpoint and lazy step-detail endpoint
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).limit(1))).scalars().first()
        if user is None:
            print("[FAIL] no user in the database to authenticate as")
            return 1
        session_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        db.add(Session(
            id=session_id, tenant_id=user.tenant_id, user_id=user.id,
            title="smoke test — tool output caps (#584)",
        ))
        await db.commit()
        db.add(Message(
            id=message_id, session_id=session_id, sender="assistant",
            content=[part],
        ))
        await db.commit()
        token = create_access_token({
            "sub": user.id, "tenant_id": user.tenant_id, "role": user.role,
        })

    headers = {"Authorization": f"Bearer {token}"}
    transport = ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://smoke") as client:
            listed = await client.get(
                f"/api/chat/session/{session_id}/messages",
                params={"limit": 10},
                headers=headers,
            )
            check("list endpoint returns 200", listed.status_code == 200,
                  str(listed.status_code))
            if listed.status_code == 200:
                payload = listed.json()
                part_in_list = payload["items"][0]["content"][0]
                check(
                    "list payload stays small",
                    "output" not in part_in_list
                    and part_in_list.get("output_chars") == len(stored),
                )

            step = await client.get(
                f"/api/chat/session/{session_id}/message/{message_id}/step/0",
                headers=headers,
            )
            check("step endpoint returns 200", step.status_code == 200,
                  str(step.status_code))
            if step.status_code == 200:
                full_output = step.json().get("full_output", "")
                check(
                    "step detail returns the full stored body",
                    full_output == stored,
                    f"{len(full_output):,} chars",
                )
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Message).where(Message.session_id == session_id))
            await db.execute(delete(Session).where(Session.id == session_id))
            await db.commit()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("OK: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
