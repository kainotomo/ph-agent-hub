# =============================================================================
# PH Agent Hub — A2A Server (HTTP+JSON/REST Protocol Binding)
# =============================================================================
# Implements the A2A Agent-to-Agent protocol server side per the A2A
# specification (Section 11 — HTTP+JSON/REST binding).
#
# Enables ph-agent-hub agents to be discovered by and interoperate with
# any A2A-compliant client.
#
# Endpoints:
#   GET  /.well-known/agent-card.json  → AgentCard (agent discovery)
#   POST /message:send                 → Execute a task (sync)
#   POST /message:stream               → Execute a task (SSE streaming)
#   GET  /tasks/{task_id}              → Get task status
#   POST /tasks/{task_id}:cancel       → Cancel a running task
# =============================================================================

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.dependencies import get_db
from ..db.orm.skills import Skill as SkillORM
from ..db.orm.users import User as UserORM

logger = logging.getLogger(__name__)

# In-memory task store for MVP
_tasks: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["a2a"])


# ---------------------------------------------------------------------------
# Agent Card endpoint
# ---------------------------------------------------------------------------


@router.get("/.well-known/agent-card.json")
async def get_agent_card(request: Request):
    """Return the AgentCard JSON for this ph-agent-hub instance.

    See A2A spec Section 8 (Agent Discovery) and Section 4.4.1 (AgentCard).
    """
    base_url = (
        settings.A2A_PUBLIC_URL
        or str(request.base_url).rstrip("/")
    )

    # Build skills list from enabled skills in the database
    skills = []
    try:
        db: AsyncSession = request.state.db
        from sqlalchemy import select
        result = await db.execute(
            select(SkillORM).where(
                SkillORM.enabled == True,  # noqa: E712
            ).limit(50)
        )
        for skill in result.scalars().all():
            skills.append({
                "id": str(skill.id),
                "name": skill.name,
                "description": skill.description or "",
                "tags": [],
                "examples": [],
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain"],
            })
    except Exception:
        logger.warning("Failed to load skills for Agent Card", exc_info=True)

    agent_card = {
        "name": settings.A2A_ORGANIZATION_NAME,
        "description": f"PH Agent Hub — AI agent platform with A2A-compatible agent execution",
        "supportedInterfaces": [
            {
                "url": f"{base_url}/message:send",
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0",
            },
            {
                "url": f"{base_url}/message:stream",
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0",
            },
        ],
        "provider": {
            "organization": settings.A2A_ORGANIZATION_NAME,
            "url": settings.A2A_ORGANIZATION_URL or base_url,
        },
        "version": "1.0.0",
        "documentationUrl": settings.A2A_DOCS_URL or "",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "securitySchemes": {
            "bearer": {
                "httpAuthSecurityScheme": {
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            }
        },
        "security": [{"bearer": []}],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": skills,
    }

    return agent_card


# ---------------------------------------------------------------------------
# Message send (sync execution)
# ---------------------------------------------------------------------------


class A2aSendMessageRequest(BaseModel):
    message: dict
    configuration: dict | None = None
    metadata: dict | None = None


@router.post("/message:send")
async def a2a_send_message(
    body: A2aSendMessageRequest,
    request: Request,
):
    """Execute an A2A task synchronously.

    Receives a SendMessageRequest, runs it through the agent runner,
    and returns a Task or Message response per the A2A spec.
    """
    # Extract user message
    msg = body.message or {}
    parts = msg.get("parts", [])
    text_content = ""
    for part in parts:
        text_content += part.get("text", "")

    # Create a task ID
    task_id = str(uuid.uuid4())
    context_id = str(uuid.uuid4())

    # Store initial task state
    _tasks[task_id] = {
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": "TASK_STATE_SUBMITTED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "artifacts": [],
        "history": [],
    }

    try:
        # Update task to working
        _tasks[task_id]["status"] = {
            "state": "TASK_STATE_WORKING",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Execute via agent runner
        response_text = await _run_a2a_agent(text_content)

        # Create artifacts from response
        artifact_id = str(uuid.uuid4())
        artifact = {
            "artifactId": artifact_id,
            "name": "Agent Response",
            "parts": [{"text": response_text}],
        }

        _tasks[task_id]["status"] = {
            "state": "TASK_STATE_COMPLETED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _tasks[task_id]["artifacts"] = [artifact]

        return {
            "task": {
                "id": task_id,
                "contextId": context_id,
                "status": _tasks[task_id]["status"],
                "artifacts": [artifact],
            }
        }

    except Exception as exc:
        logger.error("A2A task execution failed: %s", exc, exc_info=True)
        _tasks[task_id]["status"] = {
            "state": "TASK_STATE_FAILED",
            "message": {"role": "agent", "parts": [{"text": str(exc)}]},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "task": {
                "id": task_id,
                "contextId": context_id,
                "status": _tasks[task_id]["status"],
                "artifacts": [],
            }
        }


# ---------------------------------------------------------------------------
# Message stream (SSE streaming)
# ---------------------------------------------------------------------------


@router.post("/message:stream")
async def a2a_send_message_stream(
    body: A2aSendMessageRequest,
    request: Request,
):
    """Execute an A2A task with SSE streaming.

    Same as /message:send but returns Server-Sent Events for real-time updates.
    """
    from sse_starlette.sse import EventSourceResponse

    msg = body.message or {}
    parts = msg.get("parts", [])
    text_content = ""
    for part in parts:
        text_content += part.get("text", "")

    task_id = str(uuid.uuid4())
    context_id = str(uuid.uuid4())

    _tasks[task_id] = {
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": "TASK_STATE_SUBMITTED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "artifacts": [],
        "history": [],
    }

    async def event_generator():
        # Send initial task state
        yield {
            "event": "message",
            "data": json.dumps({
                "task": {
                    "id": task_id,
                    "contextId": context_id,
                    "status": {
                        "state": "TASK_STATE_WORKING",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                }
            }),
        }

        try:
            _tasks[task_id]["status"] = {
                "state": "TASK_STATE_WORKING",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            response_text = await _run_a2a_agent(text_content)

            artifact_id = str(uuid.uuid4())
            artifact = {
                "artifactId": artifact_id,
                "name": "Agent Response",
                "parts": [{"text": response_text}],
            }

            _tasks[task_id]["status"] = {
                "state": "TASK_STATE_COMPLETED",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            _tasks[task_id]["artifacts"] = [artifact]

            # Send artifact update
            yield {
                "event": "message",
                "data": json.dumps({
                    "artifactUpdate": {
                        "taskId": task_id,
                        "contextId": context_id,
                        "artifact": artifact,
                    }
                }),
            }

            # Send completion
            yield {
                "event": "message",
                "data": json.dumps({
                    "statusUpdate": {
                        "taskId": task_id,
                        "contextId": context_id,
                        "status": {
                            "state": "TASK_STATE_COMPLETED",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    }
                }),
            }

        except Exception as exc:
            logger.error("A2A stream task failed: %s", exc, exc_info=True)
            _tasks[task_id]["status"] = {
                "state": "TASK_STATE_FAILED",
                "message": {"role": "agent", "parts": [{"text": str(exc)}]},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            yield {
                "event": "message",
                "data": json.dumps({
                    "statusUpdate": {
                        "taskId": task_id,
                        "contextId": context_id,
                        "status": _tasks[task_id]["status"],
                    }
                }),
            }

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Task management
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}")
async def a2a_get_task(task_id: str):
    """Get the current status and artifacts of an A2A task."""
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": 404,
                    "message": f"Task '{task_id}' not found",
                    "details": [{
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "TASK_NOT_FOUND",
                        "domain": "a2a-protocol.org",
                    }],
                }
            },
        )
    return {"task": task}


@router.post("/tasks/{task_id}:cancel")
async def a2a_cancel_task(task_id: str):
    """Cancel a running A2A task."""
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    current_state = task["status"]["state"]
    terminal_states = {
        "TASK_STATE_COMPLETED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_REJECTED",
    }

    if current_state in terminal_states:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task is already in a terminal state and cannot be canceled",
        )

    task["status"] = {
        "state": "TASK_STATE_CANCELED",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return {"task": task}


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


async def _run_a2a_agent(text_content: str) -> str:
    """Run the ph-agent-hub agent with the given text and return the response.

    For MVP, this creates a temporary session and runs the agent
    synchronously. In the future, this will support full async task
    lifecycle with proper session management and auth.
    """
    # Use a simple direct agent call for MVP
    from ..agents.runner import run_agent

    result_text, _ = await run_agent(
        message_text=text_content,
        user_id="a2a-system",
        tenant_id=None,  # Will use default/sample tenant
        session_id=None,  # Creates a temporary session
        is_temporary=True,
        is_demo=False,
        is_guest=True,
    )
    return result_text
