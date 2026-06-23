# =============================================================================
# PH Agent Hub — A2A Server (HTTP+JSON/REST Protocol Binding)
# =============================================================================
# Implements the full A2A task lifecycle per the A2A specification
# (Section 11 — HTTP+JSON/REST binding).
#
# Endpoints:
#   GET  /.well-known/agent-card.json  → AgentCard (agent discovery)
#   POST /message:send                 → Execute a task (sync or async)
#   POST /message:stream               → Execute a task (SSE streaming)
#   GET  /tasks/{task_id}              → Get task status
#   POST /tasks/{task_id}:cancel       → Cancel a running task
#
# Lifecycle (Issue #411):
#   - `returnImmediately: true`  spawns background processing, returns
#     immediately with TASK_STATE_SUBMITTED.  Client polls GET /tasks/{id}.
#   - `taskId` in request body    resumes a suspended task
#     (INPUT_REQUIRED / AUTH_REQUIRED) for multi-turn conversations.
#   - Tasks are persisted in the database (a2a_tasks table).
#   - Cancellation uses Redis-backed flags bridged to the agent runner.
# =============================================================================

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.dependencies import get_db
from ..core.redis import set_a2a_cancel
from ..db.base import AsyncSessionLocal
from ..db.orm.skills import Skill as SkillORM
from ..db.orm.messages import Message
from ..services import a2a_task_service as a2a_tasks
from ..services import session_service

logger = logging.getLogger(__name__)


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
            a2a_meta = skill.a2a_metadata or {}
            skills.append({
                "id": str(skill.id),
                "name": skill.name,
                "description": skill.description or "",
                "tags": a2a_meta.get("tags", []),
                "examples": a2a_meta.get("examples", []),
                "inputModes": a2a_meta.get("inputModes") or ["text/plain"],
                "outputModes": a2a_meta.get("outputModes") or ["text/plain"],
            })
    except Exception:
        logger.warning("Failed to load skills for Agent Card", exc_info=True)

    agent_card = {
        "name": settings.A2A_ORGANIZATION_NAME,
        "description": "PH Agent Hub — AI agent platform with A2A-compatible agent execution",
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
# Request model
# ---------------------------------------------------------------------------


class A2aSendMessageRequest(BaseModel):
    message: dict
    configuration: dict | None = None
    metadata: dict | None = None


# ---------------------------------------------------------------------------
# Message send (sync / async / multi-turn)
# ---------------------------------------------------------------------------


@router.post("/message:send")
async def a2a_send_message(
    body: A2aSendMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Execute an A2A task.

    Behaviour depends on the request fields:

    - ``body.message.taskId`` — Resume a suspended task (INPUT_REQUIRED /
      AUTH_REQUIRED). The new message is appended to the existing session
      history and the agent re-executes.
    - ``body.message.returnImmediately`` — Spawn background processing
      and return the task ID immediately (TASK_STATE_SUBMITTED). Client
      polls ``GET /tasks/{id}`` for completion.
    - Neither — Run synchronously inline (TASK_STATE_WORKING →
      TASK_STATE_COMPLETED).
    """
    msg = body.message or {}
    parts = msg.get("parts", [])
    text_content = _extract_text_from_parts(parts)
    return_immediately = msg.get("configuration", {}).get("returnImmediately", False)
    existing_task_id = msg.get("taskId")

    # ---- Resume suspended task (multi-turn) --------------------------------
    if existing_task_id:
        return await _resume_task(
            db=db,
            task_id=existing_task_id,
            text_content=text_content,
            body=body,
        )

    # ---- Create new task --------------------------------------------------
    task_id = str(uuid.uuid4())
    context_id = str(uuid.uuid4())

    # Create a persistent session for this task
    session = await session_service.create_session(
        db=db,
        tenant_id="00000000-0000-0000-0000-000000000000",  # default tenant
        user_id="a2a-system",
        title=f"A2A task {task_id[:8]}",
        is_temporary=False,
        auto_route_enabled=True,
    )

    # Persist the task record in the DB
    await a2a_tasks.create_task(
        db=db,
        task_id=task_id,
        context_id=context_id,
        session_id=session.id,
        state=a2a_tasks.TASK_STATE_SUBMITTED,
    )
    await db.commit()

    # ---- Async path (returnImmediately) -----------------------------------
    if return_immediately:
        # Spawn background processing; the caller polls GET /tasks/{id}
        asyncio.create_task(_process_a2a_task_background(
            task_id=task_id,
            context_id=context_id,
            session_id=session.id,
            text_content=text_content,
        ))
        task = await a2a_tasks.get_task(db, task_id)
        return {"task": a2a_tasks.task_to_dict(task)}

    # ---- Sync path (run inline) --------------------------------------------
    return await _run_task_sync(
        db=db,
        task_id=task_id,
        context_id=context_id,
        session_id=session.id,
        text_content=text_content,
    )


# ---------------------------------------------------------------------------
# Message stream (SSE streaming)
# ---------------------------------------------------------------------------


@router.post("/message:stream")
async def a2a_send_message_stream(
    body: A2aSendMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Execute an A2A task with SSE streaming.

    Same as /message:send but returns Server-Sent Events for real-time
    updates.  Supports ``returnImmediately`` (spawns background, closes
    stream) and ``taskId`` (resumes suspended task).
    """
    from sse_starlette.sse import EventSourceResponse

    msg = body.message or {}
    parts = msg.get("parts", [])
    text_content = _extract_text_from_parts(parts)
    return_immediately = msg.get("configuration", {}).get("returnImmediately", False)
    existing_task_id = msg.get("taskId")

    # ---- Resume suspended task (multi-turn) via SSE ------------------------
    if existing_task_id:
        # For resumption we use the synchronous path internally and stream
        # the final result as a single SSE event.
        result = await _resume_task(
            db=db, task_id=existing_task_id,
            text_content=text_content, body=body,
        )
        task_data = result.get("task", {})
        return EventSourceResponse(_single_artifact_stream(task_data))

    # ---- Create new task --------------------------------------------------
    task_id = str(uuid.uuid4())
    context_id = str(uuid.uuid4())

    session = await session_service.create_session(
        db=db,
        tenant_id="00000000-0000-0000-0000-000000000000",
        user_id="a2a-system",
        title=f"A2A task {task_id[:8]}",
        is_temporary=False,
        auto_route_enabled=True,
    )

    await a2a_tasks.create_task(
        db=db,
        task_id=task_id,
        context_id=context_id,
        session_id=session.id,
        state=a2a_tasks.TASK_STATE_SUBMITTED,
    )
    await db.commit()

    # ---- Async path (returnImmediately) via SSE ---------------------------
    if return_immediately:
        asyncio.create_task(_process_a2a_task_background(
            task_id=task_id,
            context_id=context_id,
            session_id=session.id,
            text_content=text_content,
        ))
        return EventSourceResponse(_single_artifact_stream(
            a2a_tasks.task_to_dict(
                await a2a_tasks.get_task(db, task_id)
            )
        ))

    # ---- Sync streaming path ----------------------------------------------
    async def event_generator():
        # Transition to WORKING and yield the initial event
        await a2a_tasks.update_task_state(db, task_id, a2a_tasks.TASK_STATE_WORKING)
        yield {
            "event": "message",
            "data": json.dumps({
                "task": {
                    "id": task_id,
                    "contextId": context_id,
                    "status": {
                        "state": a2a_tasks.TASK_STATE_WORKING,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                }
            }),
        }

        try:
            response_text = await _run_a2a_agent(
                session.id, text_content, db, task_id=task_id,
            )

            # Check if the agent requested user input via ask_user tool
            from ..core.redis import get_a2a_question, clear_a2a_question
            question = await get_a2a_question(task_id)
            if question:
                await clear_a2a_question(task_id)
                status_msg = {
                    "role": "agent",
                    "parts": [{"text": question}],
                }
                await a2a_tasks.update_task_state(
                    db, task_id, a2a_tasks.TASK_STATE_INPUT_REQUIRED,
                    status_message=status_msg,
                )
                await db.commit()
                logger.info(
                    "Stream task %s transitioned to INPUT_REQUIRED: %s",
                    task_id, question,
                )
                yield {
                    "event": "message",
                    "data": json.dumps({
                        "statusUpdate": {
                            "taskId": task_id,
                            "contextId": context_id,
                            "status": {
                                "state": a2a_tasks.TASK_STATE_INPUT_REQUIRED,
                                "message": status_msg,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                        }
                    }),
                }
                return

            # Check if the agent requested auth via request_auth tool
            from ..core.redis import get_a2a_auth_request, clear_a2a_auth_request
            auth_info = await get_a2a_auth_request(task_id)
            if auth_info:
                await clear_a2a_auth_request(task_id)
                provider = auth_info.get("provider", "unknown")
                tool_type = auth_info.get("tool_type", "unknown")
                scopes = auth_info.get("scopes")
                reason = auth_info.get("reason")
                parts: list[dict] = [
                    {
                        "text": (
                            reason or
                            f"Authentication required for {provider} ({tool_type})"
                        ),
                    },
                ]
                data_part: dict[str, object] = {
                    "provider": provider,
                    "tool_type": tool_type,
                }
                if scopes:
                    data_part["scopes"] = scopes
                parts.append({"data": data_part})
                status_msg = {"role": "agent", "parts": parts}
                await a2a_tasks.update_task_state(
                    db, task_id, a2a_tasks.TASK_STATE_AUTH_REQUIRED,
                    status_message=status_msg,
                )
                await db.commit()
                logger.info(
                    "Stream task %s transitioned to AUTH_REQUIRED: %s",
                    task_id, auth_info,
                )
                yield {
                    "event": "message",
                    "data": json.dumps({
                        "statusUpdate": {
                            "taskId": task_id,
                            "contextId": context_id,
                            "status": {
                                "state": a2a_tasks.TASK_STATE_AUTH_REQUIRED,
                                "message": status_msg,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                        }
                    }),
                }
                return

            artifact_id = str(uuid.uuid4())
            artifact = {
                "artifactId": artifact_id,
                "name": "Agent Response",
                "parts": [{"text": response_text}],
            }

            await a2a_tasks.add_artifact(db, task_id, artifact)
            await a2a_tasks.update_task_state(db, task_id, a2a_tasks.TASK_STATE_COMPLETED)
            await db.commit()

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

            yield {
                "event": "message",
                "data": json.dumps({
                    "statusUpdate": {
                        "taskId": task_id,
                        "contextId": context_id,
                        "status": {
                            "state": a2a_tasks.TASK_STATE_COMPLETED,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    }
                }),
            }

        except Exception as exc:
            logger.error("A2A stream task failed: %s", exc, exc_info=True)
            status_msg = {"role": "agent", "parts": [{"text": str(exc)}]}
            await a2a_tasks.update_task_state(
                db, task_id, a2a_tasks.TASK_STATE_FAILED,
                status_message=status_msg,
            )
            await db.commit()
            yield {
                "event": "message",
                "data": json.dumps({
                    "statusUpdate": {
                        "taskId": task_id,
                        "contextId": context_id,
                        "status": {
                            "state": a2a_tasks.TASK_STATE_FAILED,
                            "message": status_msg,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    }
                }),
            }

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Task management
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}")
async def a2a_get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the current status and artifacts of an A2A task."""
    try:
        task = await a2a_tasks.get_task(db, task_id)
    except Exception:
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
    return {"task": a2a_tasks.task_to_dict(task)}


@router.post("/tasks/{task_id}:cancel")
async def a2a_cancel_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a running A2A task.

    Sets a Redis cancellation flag that the agent runner polls between
    tool-call steps, then transitions the task to TASK_STATE_CANCELED.
    """
    try:
        task = await a2a_tasks.get_task(db, task_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": 404,
                    "message": f"Task '{task_id}' not found",
                }
            },
        )

    if task.state in a2a_tasks.TERMINAL_STATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": 400,
                    "message": "Task is already in a terminal state and cannot be canceled",
                }
            },
        )

    # Set Redis cancellation flag (bridges task → session → runner)
    if task.session_id:
        await set_a2a_cancel(task_id, task.session_id)

    task = await a2a_tasks.update_task_state(
        db, task_id, a2a_tasks.TASK_STATE_CANCELED,
    )
    await db.commit()
    return {"task": a2a_tasks.task_to_dict(task)}


# ---------------------------------------------------------------------------
# Internal: task resumption (multi-turn)
# ---------------------------------------------------------------------------


async def _resume_task(
    db: AsyncSession,
    task_id: str,
    text_content: str,
    body: A2aSendMessageRequest,
) -> dict:
    """Resume a suspended task (INPUT_REQUIRED / AUTH_REQUIRED).

    Validates the task state, appends the user's follow-up to session
    history, re-runs the agent, and returns the completed task dict.
    """
    try:
        task = await a2a_tasks.get_task(db, task_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": 404,
                    "message": f"Task '{task_id}' not found",
                }
            },
        )

    if task.state not in a2a_tasks.SUSPENDED_STATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": 400,
                    "message": (
                        f"Task '{task_id}' is in state '{task.state}' "
                        f"and cannot be resumed. Only tasks in "
                        f"INPUT_REQUIRED or AUTH_REQUIRED can be resumed."
                    ),
                }
            },
        )

    if not task.session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Task '{task_id}' has no backing session",
                }
            },
        )

    # ---- When resuming from AUTH_REQUIRED, inject a system message ----------
    # telling the agent that the user has completed authentication.  This
    # breaks the potential infinite loop where the agent calls request_auth,
    # gets AUTH_REQUIRED, resumes, and calls request_auth again.
    #
    # The system message is persisted to the session history so the agent
    # sees it in context on the next run.
    extra_fi_kwargs: dict = {}
    if task.state == a2a_tasks.TASK_STATE_AUTH_REQUIRED:
        provider = "unknown"
        tool_type = "unknown"
        raw_msg = json.loads(task.status_message) if task.status_message else {}
        if raw_msg and isinstance(raw_msg.get("parts"), list):
            for part in raw_msg["parts"]:
                if isinstance(part, dict) and "data" in part:
                    provider = part["data"].get("provider", "unknown")
                    tool_type = part["data"].get("tool_type", "unknown")
                    break

        # Persist a system message to the session so the agent sees it
        msg_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        system_msg = Message(
            id=msg_id,
            session_id=task.session_id,
            sender="system",
            content=[{
                "type": "text",
                "text": (
                    f"[AUTH_COMPLETED] The authentication process for "
                    f"{provider} ({tool_type}) has been completed. "
                    f"The credentials should now be available. "
                    f"Please retry the operation that required authentication "
                    f"without requesting authentication again."
                ),
            }],
            created_at=now,
        )
        db.add(system_msg)
        await db.commit()
        logger.info(
            "Injected auth-completed system message for task %s: "
            "provider=%s, tool_type=%s",
            task_id, provider, tool_type,
        )

        # Signal to the request_auth tool that auth was just completed
        extra_fi_kwargs = {
            "auth_completed": True,
            "auth_provider": provider,
            "auth_tool_type": tool_type,
        }

    # Re-run the agent with the follow-up message on the same session
    try:
        response_text = await _run_a2a_agent(
            session_id=task.session_id,
            text_content=text_content,
            db=db,
            task_id=task_id,
            extra_fi_kwargs=extra_fi_kwargs,
        )

        # Check if the agent requested more input (agent can ask follow-ups)
        from ..core.redis import get_a2a_question, clear_a2a_question
        question = await get_a2a_question(task_id)
        if question:
            await clear_a2a_question(task_id)
            status_msg = {
                "role": "agent",
                "parts": [{"text": question}],
            }
            await a2a_tasks.update_task_state(
                db, task_id, a2a_tasks.TASK_STATE_INPUT_REQUIRED,
                status_message=status_msg,
            )
            await db.commit()
            logger.info(
                "Resumed task %s transitioned to INPUT_REQUIRED: %s",
                task_id, question,
            )
            task = await a2a_tasks.get_task(db, task_id)
            return {"task": a2a_tasks.task_to_dict(task)}

        # Check if the agent requested auth via request_auth tool
        from ..core.redis import get_a2a_auth_request, clear_a2a_auth_request
        auth_info = await get_a2a_auth_request(task_id)
        if auth_info:
            await clear_a2a_auth_request(task_id)
            provider = auth_info.get("provider", "unknown")
            tool_type = auth_info.get("tool_type", "unknown")
            scopes = auth_info.get("scopes")
            reason = auth_info.get("reason")
            parts: list[dict] = [
                {
                    "text": (
                        reason or
                        f"Authentication required for {provider} ({tool_type})"
                    ),
                },
            ]
            data_part: dict[str, object] = {
                "provider": provider,
                "tool_type": tool_type,
            }
            if scopes:
                data_part["scopes"] = scopes
            parts.append({"data": data_part})
            status_msg = {"role": "agent", "parts": parts}
            await a2a_tasks.update_task_state(
                db, task_id, a2a_tasks.TASK_STATE_AUTH_REQUIRED,
                status_message=status_msg,
            )
            await db.commit()
            logger.info(
                "Resumed task %s transitioned to AUTH_REQUIRED: %s",
                task_id, auth_info,
            )
            task = await a2a_tasks.get_task(db, task_id)
            return {"task": a2a_tasks.task_to_dict(task)}

        artifact_id = str(uuid.uuid4())
        artifact = {
            "artifactId": artifact_id,
            "name": "Agent Response",
            "parts": [{"text": response_text}],
        }

        await a2a_tasks.add_artifact(db, task_id, artifact)
        await a2a_tasks.update_task_state(
            db, task_id, a2a_tasks.TASK_STATE_COMPLETED,
        )
        await db.commit()

        task = await a2a_tasks.get_task(db, task_id)
        return {"task": a2a_tasks.task_to_dict(task)}

    except Exception as exc:
        logger.error("A2A task resumption failed: %s", exc, exc_info=True)
        status_msg = {"role": "agent", "parts": [{"text": str(exc)}]}
        await a2a_tasks.update_task_state(
            db, task_id, a2a_tasks.TASK_STATE_FAILED,
            status_message=status_msg,
        )
        await db.commit()
        return {
            "task": a2a_tasks.task_to_dict(
                await a2a_tasks.get_task(db, task_id)
            )
        }


# ---------------------------------------------------------------------------
# Internal: sync task execution
# ---------------------------------------------------------------------------


async def _run_task_sync(
    db: AsyncSession,
    task_id: str,
    context_id: str,
    session_id: str,
    text_content: str,
) -> dict:
    """Run an A2A task synchronously and return the result dict.

    Transitions: SUBMITTED → WORKING → COMPLETED or
    INPUT_REQUIRED (or FAILED).
    """
    try:
        await a2a_tasks.update_task_state(db, task_id, a2a_tasks.TASK_STATE_WORKING)
        await db.commit()

        response_text = await _run_a2a_agent(
            session_id, text_content, db, task_id=task_id,
        )

        # Check if the agent requested user input via ask_user tool
        from ..core.redis import get_a2a_question, clear_a2a_question
        question = await get_a2a_question(task_id)
        if question:
            await clear_a2a_question(task_id)
            status_msg = {
                "role": "agent",
                "parts": [{"text": question}],
            }
            await a2a_tasks.update_task_state(
                db, task_id, a2a_tasks.TASK_STATE_INPUT_REQUIRED,
                status_message=status_msg,
            )
            await db.commit()
            logger.info(
                "Sync task %s transitioned to INPUT_REQUIRED: %s",
                task_id, question,
            )
            task = await a2a_tasks.get_task(db, task_id)
            return {"task": a2a_tasks.task_to_dict(task)}

        # Check if the agent requested auth via request_auth tool
        from ..core.redis import get_a2a_auth_request, clear_a2a_auth_request
        auth_info = await get_a2a_auth_request(task_id)
        if auth_info:
            await clear_a2a_auth_request(task_id)
            provider = auth_info.get("provider", "unknown")
            tool_type = auth_info.get("tool_type", "unknown")
            scopes = auth_info.get("scopes")
            reason = auth_info.get("reason")
            parts: list[dict] = [
                {
                    "text": (
                        reason or
                        f"Authentication required for {provider} ({tool_type})"
                    ),
                },
            ]
            data_part: dict[str, object] = {
                "provider": provider,
                "tool_type": tool_type,
            }
            if scopes:
                data_part["scopes"] = scopes
            parts.append({"data": data_part})
            status_msg = {"role": "agent", "parts": parts}
            await a2a_tasks.update_task_state(
                db, task_id, a2a_tasks.TASK_STATE_AUTH_REQUIRED,
                status_message=status_msg,
            )
            await db.commit()
            logger.info(
                "Sync task %s transitioned to AUTH_REQUIRED: %s",
                task_id, auth_info,
            )
            task = await a2a_tasks.get_task(db, task_id)
            return {"task": a2a_tasks.task_to_dict(task)}

        artifact_id = str(uuid.uuid4())
        artifact = {
            "artifactId": artifact_id,
            "name": "Agent Response",
            "parts": [{"text": response_text}],
        }

        await a2a_tasks.add_artifact(db, task_id, artifact)
        await a2a_tasks.update_task_state(db, task_id, a2a_tasks.TASK_STATE_COMPLETED)
        await db.commit()

        task = await a2a_tasks.get_task(db, task_id)
        return {"task": a2a_tasks.task_to_dict(task)}

    except Exception as exc:
        logger.error("A2A sync task failed: %s", exc, exc_info=True)
        status_msg = {"role": "agent", "parts": [{"text": str(exc)}]}
        await a2a_tasks.update_task_state(
            db, task_id, a2a_tasks.TASK_STATE_FAILED,
            status_message=status_msg,
        )
        await db.commit()
        return {
            "task": a2a_tasks.task_to_dict(
                await a2a_tasks.get_task(db, task_id)
            )
        }


# ---------------------------------------------------------------------------
# Internal: background task processing (returnImmediately)
# ---------------------------------------------------------------------------


async def _process_a2a_task_background(
    task_id: str,
    context_id: str,
    session_id: str,
    text_content: str,
) -> None:
    """Run the agent in the background for an A2A task.

    Opens its own DB session (``AsyncSessionLocal``) so it is independent
    of the request-scoped session.  Transitions:

        SUBMITTED → WORKING → COMPLETED (or FAILED)

    Checks the Redis cancellation flag before starting execution and
    before committing results.  For mid-stream cancellation during
    agent execution, the runner's ``check_stream_cancel()`` is used.
    """
    try:
        async with AsyncSessionLocal() as bg_db:
            # Check cancellation before starting work
            from ..core.redis import check_a2a_cancel
            if await check_a2a_cancel(task_id):
                logger.info("Background task %s cancelled before execution", task_id)
                await a2a_tasks.update_task_state(
                    bg_db, task_id, a2a_tasks.TASK_STATE_CANCELED,
                )
                await bg_db.commit()
                return

            try:
                await a2a_tasks.update_task_state(
                    bg_db, task_id, a2a_tasks.TASK_STATE_WORKING,
                )
                await bg_db.commit()

                # Check cancellation again before running agent
                if await check_a2a_cancel(task_id):
                    logger.info("Background task %s cancelled before agent run", task_id)
                    await a2a_tasks.update_task_state(
                        bg_db, task_id, a2a_tasks.TASK_STATE_CANCELED,
                    )
                    await bg_db.commit()
                    return

                response_text = await _run_a2a_agent(
                    session_id, text_content, bg_db, task_id=task_id,
                )

                # Check if the agent requested user input via ask_user tool
                from ..core.redis import get_a2a_question, clear_a2a_question
                question = await get_a2a_question(task_id)
                if question:
                    await clear_a2a_question(task_id)
                    status_msg = {
                        "role": "agent",
                        "parts": [{"text": question}],
                    }
                    await a2a_tasks.update_task_state(
                        bg_db, task_id, a2a_tasks.TASK_STATE_INPUT_REQUIRED,
                        status_message=status_msg,
                    )
                    await bg_db.commit()
                    logger.info(
                        "Background task %s transitioned to INPUT_REQUIRED: %s",
                        task_id, question,
                    )
                    return

                # Check if the agent requested auth via request_auth tool
                from ..core.redis import get_a2a_auth_request, clear_a2a_auth_request
                auth_info = await get_a2a_auth_request(task_id)
                if auth_info:
                    await clear_a2a_auth_request(task_id)
                    provider = auth_info.get("provider", "unknown")
                    tool_type = auth_info.get("tool_type", "unknown")
                    scopes = auth_info.get("scopes")
                    reason = auth_info.get("reason")
                    parts: list[dict] = [
                        {
                            "text": (
                                reason or
                                f"Authentication required for {provider} ({tool_type})"
                            ),
                        },
                    ]
                    data_part: dict[str, object] = {
                        "provider": provider,
                        "tool_type": tool_type,
                    }
                    if scopes:
                        data_part["scopes"] = scopes
                    parts.append({"data": data_part})
                    status_msg = {"role": "agent", "parts": parts}
                    await a2a_tasks.update_task_state(
                        bg_db, task_id, a2a_tasks.TASK_STATE_AUTH_REQUIRED,
                        status_message=status_msg,
                    )
                    await bg_db.commit()
                    logger.info(
                        "Background task %s transitioned to AUTH_REQUIRED: %s",
                        task_id, auth_info,
                    )
                    return

                artifact_id = str(uuid.uuid4())
                artifact = {
                    "artifactId": artifact_id,
                    "name": "Agent Response",
                    "parts": [{"text": response_text}],
                }

                await a2a_tasks.add_artifact(bg_db, task_id, artifact)
                await a2a_tasks.update_task_state(
                    bg_db, task_id, a2a_tasks.TASK_STATE_COMPLETED,
                )
                await bg_db.commit()

                logger.info(
                    "Background task %s completed successfully", task_id,
                )

            except Exception as exc:
                logger.error(
                    "Background task %s failed: %s", task_id, exc,
                    exc_info=True,
                )
                status_msg = {"role": "agent", "parts": [{"text": str(exc)}]}
                await a2a_tasks.update_task_state(
                    bg_db, task_id, a2a_tasks.TASK_STATE_FAILED,
                    status_message=status_msg,
                )
                await bg_db.commit()
    except Exception as outer_exc:
        # This should never happen — last-resort logging
        logger.critical(
            "Fatal error in background task %s: %s",
            task_id, outer_exc, exc_info=True,
        )


# ---------------------------------------------------------------------------
# Internal: agent execution
# ---------------------------------------------------------------------------


async def _run_a2a_agent(
    session_id: str,
    text_content: str,
    db: AsyncSession,
    task_id: str | None = None,
    extra_fi_kwargs: dict | None = None,
) -> str:
    """Run the ph-agent-hub agent on an existing session and return text.

    Resolves the session_data from the DB, calls the agent runner,
    and returns the assistant response text.

    When *task_id* is provided, ``function_invocation_kwargs`` are
    forwarded so the ``ask_user`` tool can store the question in Redis.
    """
    from ..agents.runner import run_agent

    # Load session_data from DB
    session = await session_service.get_session_by_id(db, session_id)
    if session is None:
        raise ValueError(f"Session '{session_id}' not found")

    # Build session_data dict the runner expects
    session_data = {
        "id": session.id,
        "tenant_id": session.tenant_id,
        "user_id": "a2a-system",
        "selected_model_id": session.selected_model_id,
        "selected_skill_id": session.selected_skill_id,
        "selected_template_id": session.selected_template_id,
        "is_temporary": session.is_temporary,
        "auto_route_enabled": session.auto_route_enabled,
        "auto_select_tools": session.auto_select_tools,
        "thinking_enabled": session.thinking_enabled,
        "temperature": session.temperature,
        "cross_session_retrieval_enabled": session.cross_session_retrieval_enabled,
    }

    fi_kwargs: dict | None = {"task_id": task_id} if task_id else None
    if fi_kwargs and extra_fi_kwargs:
        fi_kwargs.update(extra_fi_kwargs)
    result_text, _ = await run_agent(
        session_data=session_data,
        user_message=text_content,
        db=db,
        current_user=None,  # A2A guest
        function_invocation_kwargs=fi_kwargs,
    )
    return result_text


# ---------------------------------------------------------------------------
# Internal: SSE helper
# ---------------------------------------------------------------------------


async def _single_artifact_stream(task_data: dict):
    """Yield a single SSE ``message`` event for a completed task.

    Used by the ``returnImmediately`` path of ``/message:stream``.
    """
    yield {
        "event": "message",
        "data": json.dumps({"task": task_data}),
    }


# ---------------------------------------------------------------------------
# Internal: part extraction
# ---------------------------------------------------------------------------


def _extract_text_from_parts(parts: list[dict]) -> str:
    """Extract a combined text representation from A2A Parts of all types.

    Handles ``text``, ``data`` (JSON), ``url`` (file references), and
    ``raw`` (binary) parts per A2A spec Section 4.1.6.
    """
    chunks: list[str] = []
    for part in parts:
        if "text" in part and part["text"]:
            chunks.append(part["text"])
        elif "data" in part and part["data"] is not None:
            data_val = part["data"]
            if isinstance(data_val, str):
                chunks.append(data_val)
            else:
                chunks.append(json.dumps(data_val, ensure_ascii=False))
        elif "url" in part and part["url"]:
            filename = part.get("filename", "")
            label = f"[file: {filename}]" if filename else "[url]"
            chunks.append(f"{label} {part['url']}")
        elif "raw" in part and part["raw"]:
            filename = part.get("filename", "")
            raw_val = part["raw"]
            size = len(raw_val) if isinstance(raw_val, str) else 0
            label = f"[binary: {filename}]" if filename else "[binary]"
            chunks.append(f"{label} (base64, {size} chars)")
    return "\n".join(chunks)
