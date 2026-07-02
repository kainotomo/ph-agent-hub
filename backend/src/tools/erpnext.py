# =============================================================================
# PH Agent Hub — ERPNext Tool Factory
# =============================================================================
# Builds MAF @tool-decorated async functions bound to a specific ERPNext
# instance.  All ERPNext HTTP logic lives in this module.
# =============================================================================

import json
import logging
import re
from typing import Any

import httpx
from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_slug(tool_name: str) -> str:
    """Derive a safe, readable slug from a tool's display name.

    Example: ``"kainotomo.com"`` → ``"kainotomo_com"``
    """
    return re.sub(r'[^a-zA-Z0-9]', '_', tool_name.lower()).strip('_')


def _make_tool_name(tool_name: str | None, func_name: str) -> str:
    """Build a globally unique tool name by prefixing with the instance slug.

    When ``tool_name`` is provided, returns ``"erpnext_{slug}__{func_name}"``.
    Otherwise returns ``func_name`` unchanged (backward compatibility).
    """
    if tool_name:
        slug = _make_tool_slug(tool_name)
        return f"erpnext_{slug}__{func_name}"
    return func_name


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _build_auth_header(api_key: str, api_secret: str) -> dict[str, str]:
    """Return the Authorization header dict for ERPNext REST API."""
    return {"Authorization": f"token {api_key}:{api_secret}"}


def _resolve_erpnext_credentials(
    tool_config: dict | None = None,
    user_credentials: list | None = None,
) -> dict:
    """Resolve ERPNext connection parameters from user credentials or tenant config.

    Per-user credentials take precedence over tenant-level ``tool.config``.

    Args:
        tool_config: Tenant-level tool config dict (from ``Tool.config``).
        user_credentials: Optional list of ``UserToolCredential`` ORM instances.

    Returns:
        A dict with ``base_url``, ``api_key``, ``api_secret``.
        Returns an empty dict when neither source has complete credentials.
    """
    # Phase 1 — try per-user credentials first
    if user_credentials:
        import json as _json

        for cred in user_credentials:
            if cred.status != "active":
                continue
            raw = cred.credentials
            if not raw:
                continue
            try:
                parsed = _json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                continue
            base_url = parsed.get("base_url", "").strip()
            api_key = parsed.get("api_key", "").strip()
            api_secret = parsed.get("api_secret", "").strip()
            if base_url and api_key and api_secret:
                return {"base_url": base_url, "api_key": api_key, "api_secret": api_secret}

    # Phase 2 — fall back to tenant-level tool.config
    config = tool_config or {}
    base_url = config.get("base_url", "").strip()
    api_key = config.get("api_key", "").strip()
    api_secret = config.get("api_secret", "").strip()
    if base_url and api_key and api_secret:
        return {"base_url": base_url, "api_key": api_key, "api_secret": api_secret}

    return {}


async def _safe_erpnext_response(resp: httpx.Response) -> dict:
    """Process an ERPNext HTTP response, returning the JSON body.

    On non-2xx status codes, returns the error body as a dict instead of
    raising an exception.  This lets the agent see the *actual* ERPNext
    error message (e.g. missing required arguments) so it can self-correct.

    ERPNext error responses include fields like ``exc_type``, ``exc``, and
    ``_server_messages`` which the tool-error detector recognises.
    """
    try:
        body: dict = resp.json()
    except Exception:
        body = {"error": f"ERPNext returned {resp.status_code} (non-JSON body)"}
    if resp.is_error:
        logger.warning(
            "ERPNext API error %d from %s %s: %s",
            resp.status_code,
            resp.request.method if resp.request else "?",
            resp.url,
            json.dumps(body, default=str)[:500],
        )
        return body  # let the agent see the error to self-correct
    return body


# ---------------------------------------------------------------------------
# Tool factories
# ---------------------------------------------------------------------------


def build_erpnext_tools(
    base_url: str,
    api_key: str,
    api_secret: str,
    httpx_client: httpx.AsyncClient,
    file_infos: list[dict] | None = None,
    tool_name: str | None = None,
    user_credentials: list | None = None,
    db: object | None = None,
) -> list:
    """Return a list of MAF @tool-decorated async functions bound to an
    ERPNext instance.

    Credentials are resolved in this order:
    1. Per-user ``user_credentials`` (when provided) — first active credential wins.
    2. Fall back to the ``base_url`` / ``api_key`` / ``api_secret`` parameters
       (typically from tenant-level ``tool.config``).

    Args:
        base_url: ERPNext site URL (e.g. ``https://erp.example.com``) — used
            as fallback when no per-user credential is available.
        api_key: ERPNext API key — fallback.
        api_secret: ERPNext API secret — fallback.
        httpx_client: A pre-configured ``httpx.AsyncClient`` whose lifecycle
            is managed by the caller.  Must already have ``base_url`` and
            ``Authorization`` headers set.
        file_infos: Optional list of FileUpload dicts (storage_key, bucket,
            original_filename, content_type, id) for the ``upload_file`` tool.
        tool_name: Optional display name of the Tool ORM record. When provided,
            each tool function is prefixed (e.g. ``erpnext_kainotomo_com__get_doc``)
            to prevent name collisions when multiple ERPNext instances are active.
        user_credentials: Optional list of ``UserToolCredential`` ORM instances
            for per-user ERPNext accounts. Takes precedence over base_url/api_key/
            api_secret.
        db: Optional async DB session (for future use, e.g. token persistence).

    Returns:
        A list of callables ready to pass to ``Agent(tools=...)``.
    """
    auth_header = _build_auth_header(api_key, api_secret)

    # ------------------------------------------------------------------
    @tool(name=_make_tool_name(tool_name, "get_doc"))
    async def get_doc(doctype: str, name: str) -> dict:
        """Retrieve a single ERPNext document by doctype and name.

        Args:
            doctype: The DocType name (e.g. "Sales Order").
            name: The document name/id.
        """
        url = f"/api/resource/{doctype}/{name}"
        resp = await httpx_client.get(url)
        data: dict = await _safe_erpnext_response(resp)
        logger.debug("get_doc %s/%s → %d bytes", doctype, name, len(json.dumps(data)))
        return data

    # ------------------------------------------------------------------
    @tool(name=_make_tool_name(tool_name, "get_list"))
    async def get_list(
        doctype: str,
        filters: list | dict | None = None,
        fields: list[str] | None = None,
        limit_page_length: int | None = None,
        limit_start: int | None = None,
        order_by: str | None = None,
        or_filters: list | dict | None = None,
    ) -> list[dict]:
        """Retrieve a list of ERPNext documents.

        Args:
            doctype: The DocType name (e.g. "Sales Order").
            filters: Optional ERPNext filter dict (AND logic).
            fields: Optional list of field names to return.
            limit_page_length: Optional max number of records.
            limit_start: Optional offset for pagination.
            order_by: Optional field name to sort by (e.g. "creation desc").
            or_filters: Optional ERPNext OR filter dict.
        """
        params: dict[str, Any] = {}
        if filters:
            params["filters"] = json.dumps(filters)
        if fields:
            params["fields"] = json.dumps(fields)
        if limit_page_length is not None:
            params["limit_page_length"] = limit_page_length
        if limit_start is not None:
            params["limit_start"] = limit_start
        if order_by is not None:
            params["order_by"] = order_by
        if or_filters:
            params["or_filters"] = json.dumps(or_filters)

        url = f"/api/resource/{doctype}"
        resp = await httpx_client.get(url, params=params)
        data: dict = await _safe_erpnext_response(resp)
        results: list[dict] = data.get("data", [])
        logger.debug(
            "get_list %s (filters=%s) → %d records",
            doctype,
            filters,
            len(results),
        )
        return results

    # ------------------------------------------------------------------
    @tool(name=_make_tool_name(tool_name, "create_doc"))
    async def create_doc(doctype: str, data: dict) -> dict:
        """Create a new ERPNext document.

        Args:
            doctype: The DocType name (e.g. "Supplier").
            data: Dict of field names to values for the new document.
        """
        url = f"/api/resource/{doctype}"
        resp = await httpx_client.post(url, json=data)
        result: dict = await _safe_erpnext_response(resp)
        logger.debug("create_doc %s → %s", doctype, result.get("data", {}).get("name", "?"))
        return result

    # ------------------------------------------------------------------
    @tool(name=_make_tool_name(tool_name, "update_doc"))
    async def update_doc(doctype: str, name: str, data: dict) -> dict:
        """Update an existing ERPNext document.

        Args:
            doctype: The DocType name (e.g. "Sales Order").
            name: The document name/id to update.
            data: Dict of field names to new values.
        """
        url = f"/api/resource/{doctype}/{name}"
        resp = await httpx_client.put(url, json=data)
        result: dict = await _safe_erpnext_response(resp)
        logger.debug("update_doc %s/%s", doctype, name)
        return result

    # ------------------------------------------------------------------
    @tool(name=_make_tool_name(tool_name, "delete_doc"))
    async def delete_doc(doctype: str, name: str) -> dict:
        """Delete an ERPNext document.

        Args:
            doctype: The DocType name (e.g. "Sales Order").
            name: The document name/id to delete.
        """
        url = f"/api/resource/{doctype}/{name}"
        resp = await httpx_client.delete(url)
        await _safe_erpnext_response(resp)
        logger.debug("delete_doc %s/%s", doctype, name)
        return {"message": f"Deleted {doctype} {name}"}

    # ------------------------------------------------------------------
    @tool(name=_make_tool_name(tool_name, "submit_doc"))
    async def submit_doc(doctype: str, name: str) -> dict:
        """Submit an ERPNext document (sets docstatus to 1, triggers submit hooks).

        Args:
            doctype: The DocType name (e.g. "Purchase Invoice").
            name: The document name/id to submit.
        """
        url = f"/api/resource/{doctype}/{name}"
        resp = await httpx_client.put(url, json={"docstatus": 1})
        result: dict = await _safe_erpnext_response(resp)
        logger.debug("submit_doc %s/%s", doctype, name)
        return result

    # ------------------------------------------------------------------
    @tool(name=_make_tool_name(tool_name, "cancel_doc"))
    async def cancel_doc(doctype: str, name: str) -> dict:
        """Cancel an ERPNext document (sets docstatus to 2).

        Args:
            doctype: The DocType name (e.g. "Purchase Invoice").
            name: The document name/id to cancel.
        """
        url = f"/api/resource/{doctype}/{name}"
        resp = await httpx_client.put(url, json={"docstatus": 2})
        result: dict = await _safe_erpnext_response(resp)
        logger.debug("cancel_doc %s/%s", doctype, name)
        return result

    # ------------------------------------------------------------------
    @tool(name=_make_tool_name(tool_name, "amend_doc"))
    async def amend_doc(doctype: str, name: str) -> dict:
        """Amend a submitted/cancelled ERPNext document, creating a new draft.

        Calls ``frappe.client.amend_doc`` which creates a new document with
        ``docstatus=0`` and ``amended_from`` set to the original.

        Args:
            doctype: The DocType name (e.g. "Sales Invoice").
            name: The document name/id to amend.
        """
        url = "/api/method/frappe.client.amend_doc"
        resp = await httpx_client.post(url, json={"doctype": doctype, "name": name})
        result: dict = await _safe_erpnext_response(resp)
        logger.debug("amend_doc %s/%s", doctype, name)
        return result

    # ------------------------------------------------------------------
    @tool(name=_make_tool_name(tool_name, "get_doctype_meta"))
    async def get_doctype_meta(doctype: str) -> list[dict]:
        """Get field definitions for a DocType so the agent knows which
        fields are mandatory, valid Link/Select options, etc.

        Args:
            doctype: The DocType name (e.g. "Purchase Invoice").
        """
        url = f"/api/resource/DocType/{doctype}"
        resp = await httpx_client.get(url)
        data: dict = await _safe_erpnext_response(resp)
        doc_data: dict = data.get("data", {})
        all_fields: list[dict] = doc_data.get("fields", [])
        # Return only the most relevant field attributes to keep output
        # compact (full field objects can be large and contain circular refs).
        relevant_keys = {
            "fieldname", "fieldtype", "label", "reqd", "options", "default",
        }
        results: list[dict] = [
            {k: v for k, v in field.items() if k in relevant_keys}
            for field in all_fields
        ]
        logger.debug("get_doctype_meta %s → %d fields", doctype, len(results))
        return results

    # ------------------------------------------------------------------
    @tool(name=_make_tool_name(tool_name, "call_method"))
    async def call_method(
        method: str,
        args: dict | None = None,
        http_method: str = "POST",
    ) -> dict:
        """Call any whitelisted ERPNext API method.

        Covers everything not exposed via resource CRUD: email, PDF generation,
        custom reports, payroll, etc.

        Args:
            method: The dotted method path (e.g. "frappe.core.doctype.file.file.upload_file"
                or "frappe.get_list").
            args: Optional dict of keyword arguments to pass to the method.
                For ``frappe.get_list`` you MUST pass ``doctype`` here, e.g.
                ``{"doctype": "Sales Order"}``.
            http_method: HTTP method to use, "GET" or "POST" (default "POST").
        """
        url = f"/api/method/{method}"
        if http_method.upper() == "GET":
            params: dict[str, Any] = {}
            if args:
                params = {k: json.dumps(v) if not isinstance(v, str) else v for k, v in args.items()}
            resp = await httpx_client.get(url, params=params)
        else:
            resp = await httpx_client.post(url, json=args or {})
        result: dict = await _safe_erpnext_response(resp)
        logger.debug("call_method %s (%s)", method, http_method)
        return result

    tools = [get_doc, get_list, create_doc, update_doc, delete_doc,
             submit_doc, cancel_doc, amend_doc, get_doctype_meta, call_method]

    # ------------------------------------------------------------------
    # upload_file tool (only if file_infos provided)
    # ------------------------------------------------------------------
    if file_infos:
        # Build a lookup dict keyed by original_filename (case-insensitive)
        _file_lookup: dict[str, dict] = {}
        for fi in file_infos:
            _file_lookup[fi["original_filename"].lower()] = fi

        @tool(name=_make_tool_name(tool_name, "upload_file"))
        async def upload_file(
            filename: str,
            doctype: str | None = None,
            docname: str | None = None,
        ) -> dict:
            """Upload a file to ERPNext, attaching it to a document.

            The file must have been previously uploaded to PH Agent Hub
            (visible in the current session).  Specify the exact
            ``original_filename`` as shown in the file list.

            Args:
                filename: The original filename of the uploaded file.
                doctype: Optional DocType to attach the file to.
                docname: Optional document name to attach the file to.
            """
            from ..storage.s3 import download_object

            file_info = _file_lookup.get(filename.lower())
            if file_info is None:
                available = list(_file_lookup.keys())
                return {
                    "error": f"File '{filename}' not found in uploaded files.",
                    "available_files": available,
                }

            # Download binary from MinIO
            try:
                file_bytes = await download_object(
                    file_info["bucket"], file_info["storage_key"]
                )
            except Exception as exc:
                return {"error": f"Failed to download file from storage: {exc}"}

            # Build multipart form
            files_payload: dict[str, Any] = {
                "file": (
                    file_info["original_filename"],
                    file_bytes,
                    file_info.get("content_type", "application/octet-stream"),
                )
            }
            data_payload: dict[str, str] = {"is_private": "0"}
            if doctype:
                data_payload["doctype"] = doctype
            if docname:
                data_payload["docname"] = docname

            # Use a separate httpx call for multipart (different content-type)
            base = base_url.rstrip("/")
            async with httpx.AsyncClient(
                base_url=base, headers=auth_header, timeout=60.0
            ) as upload_client:
                resp = await upload_client.post(
                    "/api/method/upload_file",
                    data=data_payload,
                    files=files_payload,
                )
                result: dict = await _safe_erpnext_response(resp)
                logger.debug(
                    "upload_file %s → ERPNext file %s",
                    filename,
                    result.get("message", {}).get("file_url", "?"),
                )
                return result

        tools.append(upload_file)

    return tools
