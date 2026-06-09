# =============================================================================
# PH Agent Hub — Tasks Tool Factory
# =============================================================================
# List, create, and manage tasks via Google Tasks API or Microsoft To Do
# (Graph API). Supports per-user OAuth credentials.
#
# Dependencies: httpx (already installed)
# =============================================================================

import json
import logging
from typing import Any

import httpx
from agent_framework import tool

from ._oauth_refresh import refresh_token_if_expired

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
GOOGLE_TASKS_API = "https://tasks.googleapis.com/tasks/v1"
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0/me"


def _find_credential(user_credentials, account_label=None):
    """Find the best matching credential entry."""
    if not user_credentials:
        return None

    normalized = {c.label.lower().strip(): c for c in user_credentials}

    if account_label:
        key = account_label.lower().strip()
        cred = normalized.get(key)
        if cred:
            return cred
        for c in user_credentials:
            if c.email_address and c.email_address.lower().strip() == key:
                return c
        return None

    for c in user_credentials:
        if c.is_default and c.status == "active":
            return c
    for c in user_credentials:
        if c.status == "active":
            return c
    return None


def _parse_credential(cred) -> tuple[str, dict, dict, str | None]:
    """Extract (provider, creds_dict, tokens_dict, email) from credential."""
    provider = cred.provider if hasattr(cred, "provider") else ""
    creds_raw = getattr(cred, "credentials", None)
    tokens_raw = getattr(cred, "oauth_tokens", None)
    email = getattr(cred, "email_address", None)
    return provider, json.loads(creds_raw) if creds_raw else {}, json.loads(tokens_raw) if tokens_raw else {}, email


def build_tasks_tools(
    tool_config: dict | None = None,
    user_credentials: list | None = None,
) -> list:
    """Return a list of MAF @tool-decorated async functions for tasks.

    Supports Google Tasks and Microsoft To Do via OAuth credentials.

    Args:
        tool_config: ``Tool.config`` JSON dict (reserved for future use).
        user_credentials: List of ``UserToolCredential`` ORM rows.

    Returns:
        A list of MAF tool callables.
    """
    config = tool_config or {}

    # ------------------------------------------------------------------
    @tool
    async def list_task_lists(account_label: str | None = None) -> dict:
        """List task lists (Google Tasks) or task folders (Microsoft To Do).

        Returns each list with its ID and name so you can reference it
        in subsequent tool calls.

        Args:
            account_label: Connected account label (optional if only one).

        Returns:
            Dict with ``task_lists`` (list) and ``total``.
        """
        cred = _find_credential(user_credentials, account_label)
        if not cred:
            return _no_account_error("task")

        provider, _, tokens, _ = _parse_credential(cred)
        access_token = tokens.get("access_token", "")

        if not access_token:
            return {"error": "Access token not available. Reconnect account.", "task_lists": [], "total": 0}

        if provider in ("gmail", "google"):
            result = await _list_google_task_lists(access_token)
            if "Token expired" in result.get("error", ""):
                refreshed = await refresh_token_if_expired(tokens, provider, "Tasks", credential_orm=cred, tokens_dict=tokens)
                if refreshed:
                    result = await _list_google_task_lists(tokens["access_token"])
            return result
        elif provider in ("outlook", "microsoft"):
            result = await _list_microsoft_task_lists(access_token)
            if "Token expired" in result.get("error", ""):
                refreshed = await refresh_token_if_expired(tokens, provider, "Tasks", credential_orm=cred, tokens_dict=tokens)
                if refreshed:
                    result = await _list_microsoft_task_lists(tokens["access_token"])
            return result
        else:
            return {"error": f"Provider '{provider}' not supported for tasks.", "task_lists": [], "total": 0}

    # ------------------------------------------------------------------
    @tool
    async def list_tasks(
        list_name: str | None = None,
        include_completed: bool = False,
        limit: int = 25,
        account_label: str | None = None,
    ) -> dict:
        """List tasks in a specific task list.

        If ``list_name`` is omitted, uses the default list.

        Args:
            list_name: Name of the task list (e.g. "My Tasks", "Work").
                      Omit for the default list.
            include_completed: Whether to include completed tasks.
            limit: Max tasks to return (default 25, max 100).
            account_label: Connected account label.

        Returns:
            Dict with ``tasks`` (list) and ``total``.
        """
        limit = max(1, min(limit, 100))
        cred = _find_credential(user_credentials, account_label)
        if not cred:
            return _no_account_error("task")

        provider, _, tokens, _ = _parse_credential(cred)
        access_token = tokens.get("access_token", "")

        if not access_token:
            return {"error": "Access token not available.", "tasks": [], "total": 0}

        if provider in ("gmail", "google"):
            result = await _list_google_tasks(access_token, list_name, include_completed, limit)
            if "Token expired" in result.get("error", ""):
                refreshed = await refresh_token_if_expired(tokens, provider, "Tasks", credential_orm=cred, tokens_dict=tokens)
                if refreshed:
                    result = await _list_google_tasks(tokens["access_token"], list_name, include_completed, limit)
            return result
        elif provider in ("outlook", "microsoft"):
            result = await _list_microsoft_tasks(access_token, list_name, include_completed, limit)
            if "Token expired" in result.get("error", ""):
                refreshed = await refresh_token_if_expired(tokens, provider, "Tasks", credential_orm=cred, tokens_dict=tokens)
                if refreshed:
                    result = await _list_microsoft_tasks(tokens["access_token"], list_name, include_completed, limit)
            return result
        else:
            return {"error": f"Provider '{provider}' not supported.", "tasks": [], "total": 0}

    # ------------------------------------------------------------------
    @tool
    async def create_task(
        title: str,
        list_name: str | None = None,
        due_date: str | None = None,
        notes: str | None = None,
        account_label: str | None = None,
    ) -> dict:
        """Create a new task.

        Args:
            title: Task title.
            list_name: Task list name (default list if omitted).
            due_date: Due date in ISO format (e.g. "2026-06-15" or
                     "2026-06-15T14:00:00").
            notes: Optional notes or description.
            account_label: Connected account label.

        Returns:
            Dict with task ``id``, ``title``, and ``status``.
        """
        if not title or not title.strip():
            return {"error": "No task title provided"}

        cred = _find_credential(user_credentials, account_label)
        if not cred:
            return _no_account_error("task")

        provider, _, tokens, _ = _parse_credential(cred)
        access_token = tokens.get("access_token", "")

        if not access_token:
            return {"error": "Access token not available.", "status": "error"}

        if provider in ("gmail", "google"):
            result = await _create_google_task(access_token, title.strip(), list_name, due_date, notes)
            if "Token expired" in result.get("error", ""):
                refreshed = await refresh_token_if_expired(tokens, provider, "Tasks", credential_orm=cred, tokens_dict=tokens)
                if refreshed:
                    result = await _create_google_task(tokens["access_token"], title.strip(), list_name, due_date, notes)
            return result
        elif provider in ("outlook", "microsoft"):
            result = await _create_microsoft_task(access_token, title.strip(), list_name, due_date, notes)
            if "Token expired" in result.get("error", ""):
                refreshed = await refresh_token_if_expired(tokens, provider, "Tasks", credential_orm=cred, tokens_dict=tokens)
                if refreshed:
                    result = await _create_microsoft_task(tokens["access_token"], title.strip(), list_name, due_date, notes)
            return result
        else:
            return {"error": f"Provider '{provider}' not supported."}

    # ------------------------------------------------------------------
    @tool
    async def update_task(
        task_id: str,
        title: str | None = None,
        completed: bool | None = None,
        due_date: str | None = None,
        notes: str | None = None,
        list_name: str | None = None,
        account_label: str | None = None,
    ) -> dict:
        """Update an existing task (mark complete, rename, etc.).

        Args:
            task_id: ID of the task to update.
            title: New title (omit to keep current).
            completed: Mark as completed (True) or not.
            due_date: New due date in ISO format.
            notes: New notes.
            list_name: Task list the task belongs to.
            account_label: Connected account label.

        Returns:
            Dict with ``id``, ``status``, and updated fields.
        """
        if not task_id:
            return {"error": "No task ID provided"}

        cred = _find_credential(user_credentials, account_label)
        if not cred:
            return _no_account_error("task")

        provider, _, tokens, _ = _parse_credential(cred)
        access_token = tokens.get("access_token", "")

        if not access_token:
            return {"error": "Access token not available.", "status": "error"}

        if provider in ("gmail", "google"):
            result = await _update_google_task(access_token, task_id, title, completed, due_date, notes, list_name)
            if "Token expired" in result.get("error", ""):
                refreshed = await refresh_token_if_expired(tokens, provider, "Tasks", credential_orm=cred, tokens_dict=tokens)
                if refreshed:
                    result = await _update_google_task(tokens["access_token"], task_id, title, completed, due_date, notes, list_name)
            return result
        elif provider in ("outlook", "microsoft"):
            result = await _update_microsoft_task(access_token, task_id, title, completed, due_date, notes, list_name)
            if "Token expired" in result.get("error", ""):
                refreshed = await refresh_token_if_expired(tokens, provider, "Tasks", credential_orm=cred, tokens_dict=tokens)
                if refreshed:
                    result = await _update_microsoft_task(tokens["access_token"], task_id, title, completed, due_date, notes, list_name)
            return result
        else:
            return {"error": f"Provider '{provider}' not supported."}

    # ------------------------------------------------------------------
    @tool
    async def list_task_accounts() -> dict:
        """List connected task accounts available to the agent.

        Returns:
            Dict with ``accounts`` (list).
        """
        if not user_credentials:
            return {"accounts": [], "message": "No task accounts connected."}

        accounts = [
            {"label": c.label, "email": c.email_address or "", "provider": c.provider,
             "is_default": c.is_default, "status": c.status}
            for c in user_credentials if c.status == "active"
        ]
        return {"accounts": accounts, "total": len(accounts)}

    # ------------------------------------------------------------------
    @tool
    async def delete_task(
        task_id: str,
        list_name: str | None = None,
        account_label: str | None = None,
    ) -> dict:
        """Delete a task permanently.

        Args:
            task_id: ID of the task to delete.
            list_name: Task list the task belongs to (optional).
            account_label: Connected account label (optional if only one).

        Returns:
            Dict with ``status`` and optionally ``error``.
        """
        if not task_id:
            return {"error": "No task ID provided", "status": "error"}

        cred = _find_credential(user_credentials, account_label)
        if not cred:
            return _no_account_error("task")

        provider, _, tokens, _ = _parse_credential(cred)
        access_token = tokens.get("access_token", "")

        if not access_token:
            return {"error": "Access token not available.", "status": "error"}

        if provider in ("gmail", "google"):
            result = await _delete_google_task(access_token, task_id, list_name)
            if "Token expired" in result.get("error", ""):
                refreshed = await refresh_token_if_expired(tokens, provider, "Tasks", credential_orm=cred, tokens_dict=tokens)
                if refreshed:
                    result = await _delete_google_task(tokens["access_token"], task_id, list_name)
            return result
        elif provider in ("outlook", "microsoft"):
            result = await _delete_microsoft_task(access_token, task_id, list_name)
            if "Token expired" in result.get("error", ""):
                refreshed = await refresh_token_if_expired(tokens, provider, "Tasks", credential_orm=cred, tokens_dict=tokens)
                if refreshed:
                    result = await _delete_microsoft_task(tokens["access_token"], task_id, list_name)
            return result
        else:
            return {"error": f"Provider '{provider}' not supported.", "status": "error"}

    tools = []
    if user_credentials:
        tools = [list_task_lists, list_tasks, create_task, update_task, delete_task, list_task_accounts]
    return tools


# =============================================================================
# Helpers
# =============================================================================

def _no_account_error(tool_type: str) -> dict:
    """Return an error dict when no credential is found."""
    return {
        "error": f"No connected {tool_type} accounts found. Add one in Account Settings.",
        "task_lists": [], "tasks": [], "total": 0,
    }


def _get_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


# =============================================================================
# Google Tasks API
# =============================================================================

async def _list_google_task_lists(access_token: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GOOGLE_TASKS_API}/users/@me/lists",
                headers=_get_headers(access_token),
            )
            if r.status_code == 401:
                return {"error": "Token expired. Reconnect.", "task_lists": [], "total": 0}
            r.raise_for_status()
            data = r.json()

        lists = [{"id": item["id"], "name": item["title"]} for item in data.get("items", [])]
        return {"task_lists": lists, "total": len(lists)}
    except Exception as exc:
        logger.error("Google task lists failed: %s", exc)
        return {"error": f"Failed to list task lists: {exc}", "task_lists": [], "total": 0}


async def _get_google_task_list_id(access_token: str, list_name: str | None) -> str:
    """Resolve a task list name to its Google Tasks ID."""
    if not list_name:
        return "@default"

    result = await _list_google_task_lists(access_token)
    for tl in result.get("task_lists", []):
        if tl["name"].lower() == list_name.lower():
            return tl["id"]
    return "@default"


async def _list_google_tasks(access_token, list_name, include_completed, limit):
    task_list_id = await _get_google_task_list_id(access_token, list_name)

    params = {"maxResults": limit}
    if not include_completed:
        params["showCompleted"] = "false"
        params["showHidden"] = "false"

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GOOGLE_TASKS_API}/lists/{task_list_id}/tasks",
                params=params, headers=_get_headers(access_token),
            )
            if r.status_code == 401:
                return {"error": "Token expired.", "tasks": [], "total": 0}
            r.raise_for_status()
            data = r.json()

        tasks = [
            {"id": t["id"], "title": t.get("title", ""), "due": t.get("due", ""),
             "notes": t.get("notes", ""), "status": t.get("status", "")}
            for t in data.get("items", [])
        ]
        return {"tasks": tasks, "total": len(tasks)}
    except Exception as exc:
        return {"error": f"Failed to list tasks: {exc}", "tasks": [], "total": 0}


async def _create_google_task(access_token, title, list_name, due_date, notes):
    task_list_id = await _get_google_task_list_id(access_token, list_name)

    body = {"title": title}
    if due_date:
        body["due"] = due_date
    if notes:
        body["notes"] = notes

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.post(
                f"{GOOGLE_TASKS_API}/lists/{task_list_id}/tasks",
                json=body, headers={**_get_headers(access_token), "Content-Type": "application/json"},
            )
            if r.status_code in (200, 201):
                data = r.json()
                return {"id": data.get("id", ""), "title": title, "status": "created"}
            if r.status_code == 401:
                return {"error": "Token expired.", "status": "error"}
            return {"error": f"Google Tasks error: HTTP {r.status_code}", "status": "error"}
    except Exception as exc:
        return {"error": f"Failed to create task: {exc}", "status": "error"}


async def _update_google_task(access_token, task_id, title, completed, due_date, notes, list_name):
    task_list_id = await _get_google_task_list_id(access_token, list_name)

    body = {}
    if title is not None:
        body["title"] = title
    if due_date is not None:
        body["due"] = due_date
    if notes is not None:
        body["notes"] = notes
    if completed is True:
        body["status"] = "completed"
    elif completed is False:
        body["status"] = "needsAction"

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.patch(
                f"{GOOGLE_TASKS_API}/lists/{task_list_id}/tasks/{task_id}",
                json=body, headers={**_get_headers(access_token), "Content-Type": "application/json"},
            )
            if r.status_code == 200:
                return {"id": task_id, "status": "updated"}
            if r.status_code == 401:
                return {"error": "Token expired.", "status": "error"}
            return {"error": f"Google Tasks error: HTTP {r.status_code}", "status": "error"}
    except Exception as exc:
        return {"error": f"Failed to update task: {exc}", "status": "error"}


async def _delete_google_task(access_token, task_id, list_name):
    task_list_id = await _get_google_task_list_id(access_token, list_name)
    if not task_list_id:
        return {"error": "No task list found.", "status": "error"}

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.delete(
                f"{GOOGLE_TASKS_API}/lists/{task_list_id}/tasks/{task_id}",
                headers=_get_headers(access_token),
            )
            if r.status_code in (200, 204):
                return {"status": "ok", "message": "Task deleted."}
            if r.status_code == 401:
                return {"error": "Token expired.", "status": "error"}
            return {"error": f"Google Tasks error: HTTP {r.status_code}", "status": "error"}
    except Exception as exc:
        return {"error": f"Failed to delete task: {exc}", "status": "error"}


# =============================================================================
# Microsoft To Do (Graph API)
# =============================================================================

async def _list_microsoft_task_lists(access_token):
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GRAPH_API_BASE}/todo/lists",
                headers=_get_headers(access_token),
            )
            if r.status_code == 401:
                return {"error": "Token expired.", "task_lists": [], "total": 0}
            r.raise_for_status()
            data = r.json()

        lists = [{"id": item["id"], "name": item.get("displayName", "")} for item in data.get("value", [])]
        return {"task_lists": lists, "total": len(lists)}
    except Exception as exc:
        return {"error": f"Failed to list task lists: {exc}", "task_lists": [], "total": 0}


async def _get_microsoft_task_list_id(access_token, list_name):
    result = await _list_microsoft_task_lists(access_token)
    # Propagate token expiry errors so callers can trigger refresh
    if result.get("error"):
        return None

    if not list_name:
        lists = result.get("task_lists", [])
        return lists[0]["id"] if lists else ""

    for tl in result.get("task_lists", []):
        if tl["name"].lower() == list_name.lower():
            return tl["id"]
    return ""


async def _list_microsoft_tasks(access_token, list_name, include_completed, limit):
    list_id = await _get_microsoft_task_list_id(access_token, list_name)
    if list_id is None:
        return {"error": "Token expired.", "tasks": [], "total": 0}
    if not list_id:
        return {"error": "No task list found.", "tasks": [], "total": 0}

    # Use minimal query params — $select with complex types can cause 400
    params = {"$top": limit}

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GRAPH_API_BASE}/todo/lists/{list_id}/tasks",
                params=params, headers=_get_headers(access_token),
            )
            if r.status_code == 401:
                return {"error": "Token expired.", "tasks": [], "total": 0}
            if r.status_code == 400:
                # Try without any params
                r = await c.get(
                    f"{GRAPH_API_BASE}/todo/lists/{list_id}/tasks",
                    headers=_get_headers(access_token),
                )
                if r.status_code == 401:
                    return {"error": "Token expired.", "tasks": [], "total": 0}
            r.raise_for_status()
            data = r.json()

        tasks = [
            {"id": t["id"], "title": t.get("title", ""), "due": t.get("dueDateTime", {}).get("dateTime", "") if t.get("dueDateTime") else "",
             "status": t.get("status", "")}
            for t in data.get("value", [])
        ]
        # Client-side filtering for uncompleted tasks if needed
        if not include_completed:
            tasks = [t for t in tasks if t.get("status") != "completed"]

        return {"tasks": tasks, "total": len(tasks)}
    except Exception as exc:
        return {"error": f"Failed to list tasks: {exc}", "tasks": [], "total": 0}


async def _create_microsoft_task(access_token, title, list_name, due_date, notes):
    list_id = await _get_microsoft_task_list_id(access_token, list_name)
    if list_id is None:
        return {"error": "Token expired.", "status": "error"}
    if not list_id:
        return {"error": "No task list found.", "status": "error"}

    body = {"title": title}
    if due_date:
        body["dueDateTime"] = {"dateTime": due_date, "timeZone": "UTC"}
    if notes:
        body["body"] = {"content": notes, "contentType": "text"}

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.post(
                f"{GRAPH_API_BASE}/todo/lists/{list_id}/tasks",
                json=body, headers={**_get_headers(access_token), "Content-Type": "application/json"},
            )
            if r.status_code in (200, 201):
                data = r.json()
                return {"id": data.get("id", ""), "title": title, "status": "created"}
            if r.status_code == 401:
                return {"error": "Token expired.", "status": "error"}
            return {"error": f"Graph API error: HTTP {r.status_code}", "status": "error"}
    except Exception as exc:
        return {"error": f"Failed to create task: {exc}", "status": "error"}


async def _update_microsoft_task(access_token, task_id, title, completed, due_date, notes, list_name):
    list_id = await _get_microsoft_task_list_id(access_token, list_name)
    if list_id is None:
        return {"error": "Token expired.", "status": "error"}
    if not list_id:
        return {"error": "No task list found.", "status": "error"}

    body = {}
    if title is not None:
        body["title"] = title
    if due_date is not None:
        body["dueDateTime"] = {"dateTime": due_date, "timeZone": "UTC"}
    if notes is not None:
        body["body"] = {"content": notes, "contentType": "text"}
    if completed is True:
        body["status"] = "completed"
    elif completed is False:
        body["status"] = "notStarted"

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.patch(
                f"{GRAPH_API_BASE}/todo/lists/{list_id}/tasks/{task_id}",
                json=body, headers={**_get_headers(access_token), "Content-Type": "application/json"},
            )
            if r.status_code == 200:
                return {"id": task_id, "status": "updated"}
            if r.status_code == 401:
                return {"error": "Token expired.", "status": "error"}
            return {"error": f"Graph API error: HTTP {r.status_code}", "status": "error"}
    except Exception as exc:
        return {"error": f"Failed to update task: {exc}", "status": "error"}


async def _delete_microsoft_task(access_token, task_id, list_name):
    list_id = await _get_microsoft_task_list_id(access_token, list_name)
    if list_id is None:
        return {"error": "Token expired.", "status": "error"}
    if not list_id:
        return {"error": "No task list found.", "status": "error"}

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.delete(
                f"{GRAPH_API_BASE}/todo/lists/{list_id}/tasks/{task_id}",
                headers=_get_headers(access_token),
            )
            if r.status_code in (200, 204):
                return {"status": "ok", "message": "Task deleted."}
            if r.status_code == 401:
                return {"error": "Token expired.", "status": "error"}
            return {"error": f"Graph API error: HTTP {r.status_code}", "status": "error"}
    except Exception as exc:
        return {"error": f"Failed to delete task: {exc}", "status": "error"}
