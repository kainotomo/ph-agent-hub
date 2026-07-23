# =============================================================================
# PH Agent Hub — Service: Scheduled Task Executor
# =============================================================================
#
# Called by the scheduler polling loop in main.py to execute a due
# ScheduledTask: creates a new chat session, runs the autopilot, records
# results, and sends notifications (in-app + optional email).
# =============================================================================

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from sqlalchemy import select as _sa_select

from ..core.config import settings
from ..db.base import AsyncSessionLocal
from ..db.orm.scheduled_tasks import ScheduledTask
from ..db.orm.users import User as UserORM
from . import session_service as _session_service


async def execute_scheduled_task(task: ScheduledTask) -> None:
    """Execute a due scheduled task.

    This function:
    1. Creates a new chat session for the execution
    2. Runs ``run_autopilot()`` with the task's goal
    3. Records the result (success/failure + session reference)
    4. Creates an in-app notification
    5. Optionally sends an email notification if the user has email configured
    """
    from ..services.scheduled_task_service import record_run_result as _record_run

    task_id = task.id
    goal = task.goal
    user_id = task.user_id
    tenant_id = task.tenant_id

    session_id: str | None = None
    status = ScheduledTask.STATE_FAILED
    error_msg: str | None = None

    try:
        async with AsyncSessionLocal() as db:
            # --- 1. Create a new chat session --------------------------------
            title = f"[Scheduled] {goal[:80]}"
            session = await _session_service.create_session(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                title=title,
            )
            session_id = session.id
            await db.commit()

            # Build session_data dict (same shape as _session_to_dict in chat.py)
            data = {
                "id": session.id,
                "tenant_id": session.tenant_id,
                "user_id": session.user_id,
                "title": session.title,
                "is_temporary": session.is_temporary,
                "is_pinned": session.is_pinned,
                "selected_template_id": session.selected_template_id,
                "selected_skill_id": session.selected_skill_id,
                "selected_model_id": session.selected_model_id,
                "thinking_enabled": session.thinking_enabled,
                "temperature": session.temperature,
                "auto_route_enabled": session.auto_route_enabled,
                "auto_select_tools": session.auto_select_tools,
                "cross_session_retrieval_enabled": session.cross_session_retrieval_enabled,
                "created_at": session.created_at.isoformat() if session.created_at else "",
                "updated_at": session.updated_at.isoformat() if session.updated_at else "",
                "active_tool_ids": [],
            }

            # --- 2. Get the user ORM -----------------------------------------
            _user = await db.execute(
                _sa_select(UserORM).where(UserORM.id == user_id)
            )
            current_user = _user.scalar_one_or_none()
            if current_user is None:
                raise ValueError(f"User {user_id} not found")

            # --- 3. Run autopilot (batch, non-streaming) ----------------------
            from ..agents.autopilot import run_autopilot as _run_autopilot

            logger.info(
                "Executing scheduled task %s: %s (session %s)",
                task_id, goal[:80], session_id,
            )

            try:
                _response_text, _msg_id = await _run_autopilot(
                    session_data=data,
                    goal=goal,
                    db=db,
                    current_user=current_user,
                )
                status = "SUCCESS"
                logger.info(
                    "Scheduled task %s completed successfully (session %s)",
                    task_id, session_id,
                )
            except Exception as exc:
                status = "FAILED"
                error_msg = str(exc)[:1000]
                logger.warning(
                    "Scheduled task %s failed: %s",
                    task_id, error_msg,
                )

            # --- 4. Record result --------------------------------------------
            try:
                await _record_run(
                    db,
                    task_id,
                    status=status,
                    session_id=session_id,
                    error=error_msg,
                )
            except Exception as rec_err:
                logger.error("Failed to record scheduled task result: %s", rec_err)

            # --- 5. In-app notification --------------------------------------
            try:
                await _create_task_notification(
                    db, task, status, session_id, error_msg,
                )
            except Exception as notif_err:
                logger.warning("Failed to create notification: %s", notif_err)

            await db.commit()

            # --- 6. Email notification (best-effort) -------------------------
            try:
                if status == "SUCCESS":
                    await _send_email_notification(
                        task, status, session_id,
                    )
            except Exception as email_err:
                logger.warning("Failed to send email notification: %s", email_err)

    except Exception as outer_exc:
        logger.error(
            "Fatal error executing scheduled task %s: %s",
            task_id, outer_exc,
            exc_info=True,
        )
        # Always try to record the failure
        try:
            async with AsyncSessionLocal() as fail_db:
                await _record_run(
                    fail_db, task_id,
                    status="FAILED",
                    session_id=session_id,
                    error=str(outer_exc)[:1000],
                )
                await fail_db.commit()
        except Exception:
            pass


async def _create_task_notification(
    db,
    task: ScheduledTask,
    status: str,
    session_id: str | None,
    error: str | None = None,
) -> None:
    """Create an in-app notification for a scheduled task execution."""
    from ..db.orm.notifications import Notification
    from ..services.notification_service import create_notification as _create_notif

    is_success = status == "SUCCESS"
    notif_type = (
        Notification.TYPE_TASK_SCHEDULED_COMPLETED
        if is_success
        else Notification.TYPE_TASK_SCHEDULED_FAILED
    )
    title = (
        f"✅ Scheduled task completed: {task.goal[:80]}"
        if is_success
        else f"❌ Scheduled task failed: {task.goal[:80]}"
    )
    body = error if not is_success else None

    await _create_notif(
        db,
        user_id=task.user_id,
        tenant_id=task.tenant_id,
        type=notif_type,
        title=title,
        body=body,
        reference_id=session_id,
        reference_type="session",
    )


async def _send_email_notification(
    task: ScheduledTask,
    status: str,
    session_id: str | None,
) -> None:
    """Send an email notification for a scheduled task execution, using the
    user's already-connected email account (Gmail/Outlook/IMAP).

    If the user has no email account configured, the notification is
    silently skipped (no error, no new SMTP config needed).
    """
    from ..db.base import AsyncSessionLocal
    from ..db.orm.tools import Tool as ToolORM
    from ..db.orm.users import User as UserORM
    from ..services.credential_service import list_credentials as _list_creds
    from ..tools.email import _find_credential, _parse_credential
    from ..tools.email import _send_via_smtp, _send_via_gmail_api, _send_via_graph_api
    from ..tools._oauth_refresh import ensure_fresh_token as _ensure_fresh_token

    async with AsyncSessionLocal() as _email_db:
        # --- 1. Find the email tool -----------------------------------------
        _tool_result = await _email_db.execute(
            _sa_select(ToolORM).where(ToolORM.tool_type == "email").limit(1)
        )
        email_tool = _tool_result.scalar_one_or_none()
        if email_tool is None:
            logger.debug("No email tool found — skipping email notification")
            return

        # --- 2. Get user's email credentials --------------------------------
        creds = await _list_creds(
            _email_db, task.user_id, tool_id=email_tool.id,
        )
        active_cred = _find_credential(creds)
        if active_cred is None:
            logger.debug(
                "User %s has no email credentials — skipping email notification",
                task.user_id,
            )
            return

        # --- 3. Get user's email address ------------------------------------
        _user_result = await _email_db.execute(
            _sa_select(UserORM).where(UserORM.id == task.user_id)
        )
        user = _user_result.scalar_one_or_none()
        recipient = user.email if user else None
        if not recipient:
            return

        # --- 4. Parse credential and send -----------------------------------
        provider, creds_dict, tokens_dict, email_addr = _parse_credential(active_cred)
        subject = (
            f"✅ Scheduled task completed: {task.goal[:80]}"
            if status == "SUCCESS"
            else f"❌ Scheduled task failed: {task.goal[:80]}"
        )
        session_link = (
            f"{settings.FRONTEND_URL}/chat/{session_id}"
            if session_id and settings.FRONTEND_URL
            else ""
        )
        body_text = (
            f"Your scheduled task \"{task.goal}\" has completed successfully.\n\n"
            f"Schedule: {task.schedule_description}\n"
            + (f"View results: {session_link}" if session_link else "")
            if status == "SUCCESS"
            else (
                f"Your scheduled task \"{task.goal}\" has failed.\n\n"
                f"Schedule: {task.schedule_description}\n"
                f"Error: {task.last_run_error or 'Unknown error'}"
            )
        )

        try:
            if provider in ("gmail", "google") and tokens_dict.get("access_token"):
                tokens_dict = await _ensure_fresh_token(
                    tokens_dict, provider, "Email",
                    credential_orm=active_cred, db=_email_db,
                )
                await _send_via_gmail_api(
                    recipient, subject, body_text,
                    access_token=tokens_dict.get("access_token", ""),
                )
            elif provider in ("outlook", "microsoft") and tokens_dict.get("access_token"):
                tokens_dict = await _ensure_fresh_token(
                    tokens_dict, provider, "Email",
                    credential_orm=active_cred, db=_email_db,
                )
                await _send_via_graph_api(
                    recipient, subject, body_text,
                    access_token=tokens_dict.get("access_token", ""),
                )
            elif creds_dict.get("smtp_host"):
                sender = email_addr or creds_dict.get("from_email", "")
                await _send_via_smtp(
                    recipient, subject, body_text, sender,
                    smtp_host=creds_dict["smtp_host"],
                    smtp_port=int(creds_dict.get("smtp_port", 587)),
                    smtp_username=creds_dict.get("username", ""),
                    smtp_password=creds_dict.get("password", ""),
                )
            logger.info(
                "Email notification sent for scheduled task %s to %s",
                task.id, recipient,
            )
        except Exception as send_err:
            logger.warning(
                "Failed to send email notification for task %s: %s",
                task.id, send_err,
            )
