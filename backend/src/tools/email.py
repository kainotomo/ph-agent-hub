# =============================================================================
# PH Agent Hub — Email Tool Factory
# =============================================================================
# Send and read emails via SMTP/SendGrid, IMAP, Gmail REST API,
# or Microsoft Graph API.
#
# Supports both tenant-level config (tool.config) and per-user
# credentials (user_tool_credentials). When user credentials are
# provided, they take precedence over the tenant-level config.
#
# Dependencies: httpx (already installed), smtplib (stdlib),
#               aioimaplib (optional, for async IMAP)
# =============================================================================

import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx
from agent_framework import tool

from ._oauth_refresh import ensure_fresh_token as _ensure_fresh_token
from ._oauth_refresh import refresh_token_if_expired as _refresh_token_if_expired

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT: float = 30.0
SENDGRID_API_BASE: str = "https://api.sendgrid.com/v3"
GMAIL_API_BASE: str = "https://gmail.googleapis.com/gmail/v1/users/me"
GRAPH_API_BASE: str = "https://graph.microsoft.com/v1.0/me"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_credentials(tool_config: dict) -> dict:
    """Resolve and decrypt credentials from config."""
    from ..core.encryption import decrypt

    creds = {}
    for key in ("smtp_password", "api_key", "smtp_username"):
        val = tool_config.get(key, "")
        if val:
            try:
                creds[key] = decrypt(val)
            except Exception:
                creds[key] = val

    for key in ("smtp_host", "smtp_port", "from_email", "from_name", "provider"):
        if key in tool_config:
            creds[key] = tool_config[key]

    return creds


def _check_recipient_allowed(recipient: str, allowed_recipients: list[str] | None) -> bool:
    """Check if a recipient is in the allowlist. Empty list means all allowed."""
    if not allowed_recipients:
        return True

    recipient_lower = recipient.lower().strip()
    for pattern in allowed_recipients:
        pattern_lower = pattern.lower().strip()
        if pattern_lower == "*":
            return True
        if pattern_lower.startswith("*@"):
            domain = pattern_lower[2:]
            if recipient_lower.endswith("@" + domain):
                return True
        if recipient_lower == pattern_lower:
            return True

    return False


def _build_email_message(
    to: str, subject: str, body: str, from_email: str,
    from_name: str = "", cc: str | None = None, is_html: bool = False,
) -> MIMEMultipart:
    """Build an email MIME message."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to
    if cc:
        msg["Cc"] = cc

    if is_html:
        msg.attach(MIMEText(body, "html", "utf-8"))
    else:
        html_body = body.replace("\n", "<br>\n")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(
            f"<html><body><p>{html_body}</p></body></html>", "html", "utf-8",
        ))
    return msg


def _build_mime_with_attachments(
    to: str, subject: str, body: str, from_email: str,
    from_name: str = "", cc: str | None = None, is_html: bool = False,
    attachments: list[dict] | None = None,
) -> MIMEMultipart:
    """Build a multipart/mixed MIME message with body text and optional file attachments.

    ``attachments`` is a list of dicts with keys:
        - ``filename`` (str): attachment file name
        - ``content`` (str): base64-encoded file content
        - ``mime_type`` (str): MIME type of the file
    """
    import base64
    from email.mime.base import MIMEBase
    from email.mime.multipart import MIMEMultipart as MMM
    from email.mime.text import MIMEText as MT

    msg = MMM("mixed")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to
    if cc:
        msg["Cc"] = cc

    # Body part
    body_alt = MMM("alternative")
    if is_html:
        body_alt.attach(MT(body, "html", "utf-8"))
    else:
        body_alt.attach(MT(body, "plain", "utf-8"))
        br_newline = "<br>\n"
        body_alt.attach(MT(
            f"<html><body><p>{body.replace(chr(10), br_newline)}</p></body></html>",
            "html", "utf-8",
        ))
    msg.attach(body_alt)

    # Attachment parts
    for att in (attachments or []):
        part = MIMEBase(*att.get("mime_type", "application/octet-stream").split("/", 1))
        raw_content = att.get("content", "") or ""
        # Validate base64: try decoding; if it fails, treat as plain text
        # and base64-encode it.  This guards against the model passing
        # extracted text instead of the content_base64 field.
        try:
            raw_bytes = base64.b64decode(raw_content)
        except Exception:
            logger.warning(
                "Attachment '%s' has non-base64 content — re-encoding as plain text",
                att.get("filename", "attachment"),
            )
            raw_bytes = raw_content.encode("utf-8")
        part.set_payload(raw_bytes)
        from email import encoders
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=att.get("filename", "attachment"))
        msg.attach(part)

    return msg


def _build_graph_attachments(attachments: list[dict] | None) -> list[dict]:
    """Build Graph API ``fileAttachment`` entries from an attachment list."""
    if not attachments:
        return []

    result = []
    for att in attachments:
        result.append({
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": att.get("filename", "attachment"),
            "contentType": att.get("mime_type", "application/octet-stream"),
            "contentBytes": att.get("content", ""),
        })
    return result


def _find_credential(
    user_credentials: list | None, account_label: str | None = None,
) -> Any | None:
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


def _parse_credential(cred: Any) -> tuple[str, dict, dict, str | None]:
    """Extract (provider, creds_dict, tokens_dict, email_address) from a credential ORM row."""
    provider = cred.provider if hasattr(cred, "provider") else ""
    creds_raw = getattr(cred, "credentials", None)
    tokens_raw = getattr(cred, "oauth_tokens", None)
    email = getattr(cred, "email_address", None)
    return provider, json.loads(creds_raw) if creds_raw else {}, json.loads(tokens_raw) if tokens_raw else {}, email  # noqa: E501


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_email_tools(
    tool_config: dict | None = None,
    user_credentials: list | None = None,
    db: object | None = None,
) -> list:
    """Return a list of MAF @tool-decorated async functions for email.

    Supports SMTP, SendGrid, IMAP, Gmail REST API, and Microsoft Graph API.

    Args:
        tool_config: ``Tool.config`` JSON dict (tenant-level).
        user_credentials: List of ``UserToolCredential`` ORM rows for
            per-user email accounts.
        db: Optional async DB session for persisting refreshed tokens.

    Returns:
        A list of MAF tool callables.
    """
    config = tool_config or {}
    creds = _resolve_credentials(config)
    provider: str = creds.get("provider", "smtp").lower()
    from_email: str = creds.get("from_email", "")
    from_name: str = creds.get("from_name", "")
    allowed_recipients: list[str] = config.get("allowed_recipients", [])

    async def _maybe_refresh(tk, cp, active_cred):
        """Refresh token if expired; persist if db is available."""
        return await _refresh_token_if_expired(
            tk, cp, "Email",
            credential_orm=active_cred, tokens_dict=tk, db=db,
        )

    async def _resolve_file_attachments(
        file_ids: list[str] | None,
    ) -> list[dict]:
        """Resolve uploaded file IDs to email attachment dicts.

        Fetches raw bytes from MinIO storage — avoids passing large
        base64 payloads through the LLM context.
        """
        if not file_ids or not db:
            return []
        import base64
        from sqlalchemy import select as _select
        from ..db.orm.file_uploads import FileUpload as _FU
        from ..storage.s3 import download_object as _dl

        fu_result = await db.execute(
            _select(_FU).where(_FU.id.in_(file_ids))
        )
        uploads = list(fu_result.scalars().all())

        resolved: list[dict] = []
        for upload in uploads:
            content_base64 = ""
            try:
                raw = await _dl(upload.bucket, upload.storage_key)
                content_base64 = base64.b64encode(raw).decode("ascii")
            except Exception:
                logger.warning(
                    "Could not download file %s for email attachment", upload.id,
                )
                continue
            resolved.append({
                "filename": upload.original_filename,
                "content": content_base64,
                "mime_type": upload.content_type,
            })
        return resolved

    # ------------------------------------------------------------------
    @tool
    async def send_email(
        to: str, subject: str, body: str,
        cc: str | None = None, is_html: bool = False,
        attachments: list[dict] | None = None,
        file_ids: list[str] | None = None,
        account_label: str | None = None,
    ) -> dict:
        """Send an email via SMTP, SendGrid, Gmail API, or Outlook API.

        If the user has connected email accounts, ``account_label``
        specifies which account to send from (e.g. "Work Gmail").
        Uses the default account if not specified.

        ATTACHMENTS: You may attach files using EITHER:
          - ``file_ids``: list of file IDs from ``list_uploaded_files()``
            (preferred — avoids large base64 data in the conversation).
          - ``attachments``: list of dicts with ``filename``, ``content``
            (base64), and ``mime_type`` (from ``download_file_for_attachment``).

        If both are provided, both sets of attachments are included.

        Args:
            to: Recipient email address.
            subject: Email subject line.
            body: Email body content.
            cc: Optional CC recipient.
            is_html: Set to True if body contains HTML.
            attachments: Optional list of file attachment dicts (base64).
            file_ids: Optional list of uploaded file IDs to attach directly.
            account_label: Connected account label (required when
                          multiple accounts are configured).

        Returns:
            A dict with ``to``, ``subject``, ``status``, optionally ``error``.
        """
        # Resolve file_ids into attachment dicts, then merge with
        # any base64 attachments provided directly.
        file_attachments = await _resolve_file_attachments(file_ids)
        all_attachments = (attachments or []) + file_attachments
        active_cred = _find_credential(user_credentials, account_label)
        if active_cred:
            cp, cd, tk, ce = _parse_credential(active_cred)
            if cp in ("gmail", "google") and tk.get("access_token"):
                await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
                result = await _send_via_gmail_api(to, subject, body, cc, is_html, tk.get("access_token", ""), all_attachments)
                if "expired" in result.get("error", "").lower():
                    refreshed = await _maybe_refresh(tk, cp, active_cred)
                    if refreshed:
                        result = await _send_via_gmail_api(to, subject, body, cc, is_html, tk["access_token"], all_attachments)
                return result
            elif cp in ("outlook", "microsoft") and tk.get("access_token"):
                await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
                result = await _send_via_graph_api(to, subject, body, cc, is_html, tk.get("access_token", ""), all_attachments)
                if "expired" in result.get("error", "").lower():
                    refreshed = await _maybe_refresh(tk, cp, active_cred)
                    if refreshed:
                        result = await _send_via_graph_api(to, subject, body, cc, is_html, tk["access_token"], all_attachments)
                return result
            elif cd.get("smtp_host"):
                sender = ce or cd.get("from_email", from_email)
                return await _send_via_smtp(
                    to, subject, body, sender,
                    smtp_host=cd["smtp_host"],
                    smtp_port=int(cd.get("smtp_port", 587)),
                    smtp_username=cd.get("username", ""),
                    smtp_password=cd.get("password", ""),
                    cc=cc, is_html=is_html, attachments=all_attachments,
                )

        # Fallback to tenant config
        if not to or not to.strip():
            return {"error": "No recipient email provided", "status": "error"}
        if not subject or not subject.strip():
            return {"error": "No email subject provided", "status": "error"}
        if not body or not body.strip():
            return {"error": "No email body provided", "status": "error"}

        recipient = to.strip()
        if "@" not in recipient:
            return {"error": f"Invalid recipient email: {recipient}", "status": "error"}

        if not _check_recipient_allowed(recipient, allowed_recipients):
            return {"error": f"Recipient '{recipient}' is not in the allowed list", "status": "error"}

        if provider == "smtp":
            return await _send_via_smtp(
                recipient, subject.strip(), body.strip(), from_email, from_name=from_name,
                smtp_host=creds.get("smtp_host", ""),
                smtp_port=int(creds.get("smtp_port", 587)),
                smtp_username=creds.get("smtp_username", ""),
                smtp_password=creds.get("smtp_password", ""),
                cc=cc, is_html=is_html, attachments=all_attachments,
            )
        elif provider == "sendgrid":
            return await _send_via_sendgrid(
                recipient, subject.strip(), body.strip(), from_email, from_name=from_name,
                api_key=creds.get("api_key", ""), cc=cc, is_html=is_html,
            )
        else:
            if user_credentials:
                accounts = [f"'{c.label}'" for c in user_credentials if c.status == "active"]
                if accounts:
                    return {"error": f"Available accounts: {', '.join(accounts)}. Specify via account_label.", "status": "error"}
            return {"error": f"Email provider '{provider}' is not supported", "status": "error"}

    # ------------------------------------------------------------------
    @tool
    async def read_emails(
        limit: int = 10, folder: str = "INBOX",
        unread_only: bool = False, account_label: str | None = None,
    ) -> dict:
        """Read recent emails from a connected account.

        Requires a connected email account (Gmail, Outlook, or IMAP).
        Returns sender, subject, and date for each email.

        Args:
            limit: Max emails (default 10, max 50).
            folder: IMAP folder / Gmail label (default "INBOX").
            unread_only: Only unread emails.
            account_label: Which account to use.

        Returns:
            Dict with ``emails`` (list) and ``total``.
        """
        limit = max(1, min(limit, 50))
        active_cred = _find_credential(user_credentials, account_label)
        if not active_cred:
            msg = "No connected email accounts found. Add one in Account Settings."
            if user_credentials:
                accounts = [f"'{c.label}'" for c in user_credentials if c.status == "active"]
                msg = f"Available: {', '.join(accounts)}. Specify via account_label." if accounts else msg
            return {"error": msg, "emails": [], "total": 0}

        cp, cd, tk, _ = _parse_credential(active_cred)

        if cp in ("gmail", "google") and tk.get("access_token"):
            await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
            result = await _read_gmail_api(tk.get("access_token", ""), limit, "is:unread " if unread_only else "")
            if "expired" in result.get("error", "").lower():
                refreshed = await _maybe_refresh(tk, cp, active_cred)
                if refreshed:
                    result = await _read_gmail_api(tk["access_token"], limit, "is:unread " if unread_only else "")
            return result
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
            result = await _read_outlook_api(tk.get("access_token", ""), limit, unread_only)
            if "expired" in result.get("error", "").lower():
                refreshed = await _maybe_refresh(tk, cp, active_cred)
                if refreshed:
                    result = await _read_outlook_api(tk["access_token"], limit, unread_only)
            return result
        elif cd.get("imap_host"):
            return await _read_imap(
                cd["imap_host"], int(cd.get("imap_port", 993)),
                cd.get("username", ""), cd.get("password", ""),
                folder, limit, unread_only,
            )
        return {"error": "This account does not support reading.", "emails": [], "total": 0}

    # ------------------------------------------------------------------
    @tool
    async def search_emails(
        query: str, limit: int = 10, folder: str = "INBOX",
        account_label: str | None = None,
    ) -> dict:
        """Search emails in a connected account.

        Args:
            query: Keywords or Gmail-style search.
            limit: Max results (default 10, max 50).
            folder: Folder/label to search.
            account_label: Which account to search.

        Returns:
            Dict with ``emails`` (list) and ``total``.
        """
        limit = max(1, min(limit, 50))
        if not query or not query.strip():
            return {"error": "No search query provided", "emails": [], "total": 0}

        active_cred = _find_credential(user_credentials, account_label)
        if not active_cred:
            return {"error": "No matching email account found", "emails": [], "total": 0}

        cp, cd, tk, _ = _parse_credential(active_cred)

        if cp in ("gmail", "google") and tk.get("access_token"):
            await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
            result = await _read_gmail_api(tk.get("access_token", ""), limit, query)
            if "expired" in result.get("error", "").lower():
                refreshed = await _maybe_refresh(tk, cp, active_cred)
                if refreshed:
                    result = await _read_gmail_api(tk["access_token"], limit, query)
            return result
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
            result = await _search_outlook_api(tk.get("access_token", ""), query, limit)
            if "expired" in result.get("error", "").lower():
                refreshed = await _maybe_refresh(tk, cp, active_cred)
                if refreshed:
                    result = await _search_outlook_api(tk["access_token"], query, limit)
            return result
        elif cd.get("imap_host"):
            return await _search_imap(
                cd["imap_host"], int(cd.get("imap_port", 993)),
                cd.get("username", ""), cd.get("password", ""),
                query, folder, limit,
            )
        return {"error": "This account does not support search.", "emails": [], "total": 0}

    # ------------------------------------------------------------------
    @tool
    async def get_email_body(
        email_id: str, account_label: str | None = None,
    ) -> dict:
        """Get the full body text of a specific email by its ID.

        The ID comes from ``read_emails`` or ``search_emails`` results.

        Args:
            email_id: The email's unique ID (from read_emails/search_emails).
            account_label: Which account the email belongs to.

        Returns:
            Dict with ``id``, ``from``, ``subject``, ``body`` (plain text),
            and optionally ``error``.
        """
        active_cred = _find_credential(user_credentials, account_label)
        if not active_cred:
            return {"error": "No matching email account found"}

        cp, cd, tk, _ = _parse_credential(active_cred)

        if cp in ("gmail", "google") and tk.get("access_token"):
            return await _get_gmail_body(tk["access_token"], email_id)
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            return await _get_outlook_body(tk["access_token"], email_id)
        elif cd.get("imap_host"):
            return await _get_imap_body(
                cd["imap_host"], int(cd.get("imap_port", 993)),
                cd.get("username", ""), cd.get("password", ""), email_id,
            )
        return {"error": "This account does not support reading email bodies."}

    # ------------------------------------------------------------------
    @tool
    async def mark_email_as_read(
        email_id: str, account_label: str | None = None,
    ) -> dict:
        """Mark a specific email as read in the remote inbox.

        Args:
            email_id: The email's unique ID.
            account_label: Which account the email is in.

        Returns:
            Dict with ``status`` and optionally ``error``.
        """
        active_cred = _find_credential(user_credentials, account_label)
        if not active_cred:
            return {"error": "No matching email account found", "status": "error"}

        cp, cd, tk, _ = _parse_credential(active_cred)

        if cp in ("gmail", "google") and tk.get("access_token"):
            return await _modify_gmail(tk["access_token"], email_id, remove_labels=["UNREAD"])
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            return await _mark_outlook_read(tk["access_token"], email_id, is_read=True)
        elif cd.get("imap_host"):
            return await _mark_imap_read(
                cd["imap_host"], int(cd.get("imap_port", 993)),
                cd.get("username", ""), cd.get("password", ""), email_id, read=True,
            )
        return {"error": "This account does not support marking emails as read.", "status": "error"}

    # ------------------------------------------------------------------
    @tool
    async def mark_email_as_unread(
        email_id: str, account_label: str | None = None,
    ) -> dict:
        """Mark a specific email as unread in the remote inbox.

        Args:
            email_id: The email's unique ID.
            account_label: Which account the email is in.

        Returns:
            Dict with ``status`` and optionally ``error``.
        """
        active_cred = _find_credential(user_credentials, account_label)
        if not active_cred:
            return {"error": "No matching email account found", "status": "error"}

        cp, cd, tk, _ = _parse_credential(active_cred)

        if cp in ("gmail", "google") and tk.get("access_token"):
            return await _modify_gmail(tk["access_token"], email_id, add_labels=["UNREAD"])
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            return await _mark_outlook_read(tk["access_token"], email_id, is_read=False)
        elif cd.get("imap_host"):
            return await _mark_imap_read(
                cd["imap_host"], int(cd.get("imap_port", 993)),
                cd.get("username", ""), cd.get("password", ""), email_id, read=False,
            )
        return {"error": "This account does not support marking emails as unread.", "status": "error"}

    # ------------------------------------------------------------------
    @tool
    async def list_folders(account_label: str | None = None) -> dict:
        """List all folders/labels in the connected email account.

        Returns each folder's name. For Gmail accounts, returns labels.
        For Outlook, returns mail folders.

        Args:
            account_label: Which account to list folders for.

        Returns:
            Dict with ``folders`` (list of dicts) and ``total``.
        """
        active_cred = _find_credential(user_credentials, account_label)
        if not active_cred:
            return {"error": "No matching email account found", "folders": [], "total": 0}

        cp, cd, tk, _ = _parse_credential(active_cred)

        if cp in ("gmail", "google") and tk.get("access_token"):
            return await _list_gmail_labels(tk["access_token"])
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            return await _list_outlook_folders(tk["access_token"])
        elif cd.get("imap_host"):
            return await _list_imap_folders(
                cd["imap_host"], int(cd.get("imap_port", 993)),
                cd.get("username", ""), cd.get("password", ""),
            )
        return {"error": "This account does not support folder listing.", "folders": [], "total": 0}

    # ------------------------------------------------------------------
    @tool
    async def move_email(
        email_id: str, folder: str, account_label: str | None = None,
    ) -> dict:
        """Move an email to a different folder/label.

        For IMAP accounts, this copies the email to the target folder and
        removes it from the current folder. For Gmail, it applies/removes
        labels. For Outlook, it moves to a different mail folder.

        Use ``list_folders`` first to see available folders.

        Args:
            email_id: The email's unique ID.
            folder: Target folder name (e.g. "Work", "Archive", "INBOX").
            account_label: Which account the email is in.

        Returns:
            Dict with ``status`` and optionally ``error``.
        """
        active_cred = _find_credential(user_credentials, account_label)
        if not active_cred:
            return {"error": "No matching email account found", "status": "error"}

        cp, cd, tk, _ = _parse_credential(active_cred)

        if cp in ("gmail", "google") and tk.get("access_token"):
            # Gmail: get current labels, then modify
            return await _move_gmail(tk["access_token"], email_id, folder)
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            return await _move_outlook(tk["access_token"], email_id, folder)
        elif cd.get("imap_host"):
            return await _move_imap(
                cd["imap_host"], int(cd.get("imap_port", 993)),
                cd.get("username", ""), cd.get("password", ""),
                email_id, folder,
            )
        return {"error": "This account does not support moving emails.", "status": "error"}

    # ------------------------------------------------------------------
    @tool
    async def list_email_accounts() -> dict:
        """List all connected email accounts.

        Returns:
            Dict with ``accounts`` (list) and ``total``.
        """
        if not user_credentials:
            return {"accounts": [], "message": "No email accounts connected. Add one in Account Settings."}

        accounts = [
            {"label": c.label, "email": c.email_address or "", "provider": c.provider,
             "is_default": c.is_default, "status": c.status}
            for c in user_credentials if c.status == "active"
        ]
        return {"accounts": accounts, "total": len(accounts)}

    # ------------------------------------------------------------------
    @tool
    async def forward_email(
        email_id: str, to: str,
        body: str | None = None,
        cc: str | None = None,
        account_label: str | None = None,
    ) -> dict:
        """Forward an existing email to another recipient.

        The original email content (body, formatting, attachments) is
        preserved. Optionally add a comment prepended to the forwarded
        content.

        Args:
            email_id: The email's unique ID (from read_emails/search_emails).
            to: Recipient email address to forward to.
            body: Optional comment to prepend before the forwarded content.
            cc: Optional CC recipient.
            account_label: Which account the email is in.

        Returns:
            Dict with ``to``, ``subject``, ``status``, optionally ``error``.
        """
        if not email_id:
            return {"error": "No email ID provided", "status": "error"}
        if not to or not to.strip():
            return {"error": "No recipient email provided", "status": "error"}

        active_cred = _find_credential(user_credentials, account_label)
        if not active_cred:
            return {"error": "No matching email account found", "status": "error"}

        cp, cd, tk, _ = _parse_credential(active_cred)

        if cp in ("gmail", "google") and tk.get("access_token"):
            await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
            result = await _forward_via_gmail_api(tk["access_token"], email_id, to.strip(), body, cc)
            if "expired" in result.get("error", "").lower():
                refreshed = await _maybe_refresh(tk, cp, active_cred)
                if refreshed:
                    result = await _forward_via_gmail_api(tk["access_token"], email_id, to.strip(), body, cc)
            return result
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
            result = await _forward_via_graph_api(tk["access_token"], email_id, to.strip(), body, cc)
            if "expired" in result.get("error", "").lower():
                refreshed = await _maybe_refresh(tk, cp, active_cred)
                if refreshed:
                    result = await _forward_via_graph_api(tk["access_token"], email_id, to.strip(), body, cc)
            return result
        elif cd.get("imap_host"):
            return await _forward_via_imap(
                cd["imap_host"], int(cd.get("imap_port", 993)),
                cd.get("username", ""), cd.get("password", ""),
                email_id, to.strip(), body, cc,
            )
        return {"error": "This account does not support forwarding.", "status": "error"}

    # ------------------------------------------------------------------
    @tool
    async def reply_email(
        email_id: str, body: str,
        reply_all: bool = False,
        attachments: list[dict] | None = None,
        file_ids: list[str] | None = None,
        account_label: str | None = None,
    ) -> dict:
        """Reply to an existing email.

        The reply is threaded to the original message (preserves the
        conversation chain). Uses In-Reply-To / References headers
        for SMTP/IMAP, or native threading for Gmail/Outlook.

        ATTACHMENTS: You may attach files using EITHER:
          - ``file_ids``: list of file IDs from ``list_uploaded_files()``
            (preferred — avoids large base64 data in the conversation).
          - ``attachments``: list of dicts with ``filename``, ``content``
            (base64), and ``mime_type`` (from ``download_file_for_attachment``).

        If both are provided, both sets of attachments are included.

        Args:
            email_id: The email's unique ID (from read_emails/search_emails).
            body: Reply body content (plain text).
            reply_all: If True, reply to all recipients instead of just sender.
            attachments: Optional list of file attachment dicts (base64).
            file_ids: Optional list of uploaded file IDs to attach directly.
            account_label: Which account the email is in.

        Returns:
            Dict with ``status`` and optionally ``error``.
        """
        if not email_id:
            return {"error": "No email ID provided", "status": "error"}
        if not body or not body.strip():
            return {"error": "No reply body provided", "status": "error"}

        file_attachments = await _resolve_file_attachments(file_ids)
        all_attachments_reply = (attachments or []) + file_attachments

        active_cred = _find_credential(user_credentials, account_label)
        if not active_cred:
            return {"error": "No matching email account found", "status": "error"}

        cp, cd, tk, _ = _parse_credential(active_cred)

        if cp in ("gmail", "google") and tk.get("access_token"):
            await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
            result = await _reply_via_gmail_api(tk["access_token"], email_id, body.strip(), reply_all, all_attachments_reply)
            if "expired" in result.get("error", "").lower():
                refreshed = await _maybe_refresh(tk, cp, active_cred)
                if refreshed:
                    result = await _reply_via_gmail_api(tk["access_token"], email_id, body.strip(), reply_all, all_attachments_reply)
            return result
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
            result = await _reply_via_graph_api(tk["access_token"], email_id, body.strip(), reply_all, all_attachments_reply)
            if "expired" in result.get("error", "").lower():
                refreshed = await _maybe_refresh(tk, cp, active_cred)
                if refreshed:
                    result = await _reply_via_graph_api(tk["access_token"], email_id, body.strip(), reply_all, all_attachments_reply)
            return result
        elif cd.get("imap_host"):
            return await _reply_via_imap(
                cd["imap_host"], int(cd.get("imap_port", 993)),
                cd.get("username", ""), cd.get("password", ""),
                email_id, body.strip(), reply_all, attachments,
            )
        return {"error": "This account does not support replying.", "status": "error"}

    # ------------------------------------------------------------------
    @tool
    async def delete_email(
        email_id: str,
        permanent: bool = False,
        account_label: str | None = None,
    ) -> dict:
        """Delete an email, moving it to trash or permanently removing it.

        Args:
            email_id: The email's unique ID (from read_emails/search_emails).
            permanent: If False (default), moves to trash/recoverable.
                       If True, permanently deletes. Use with caution.
            account_label: Which account the email is in.

        Returns:
            Dict with ``status`` and optionally ``error``.
        """
        if not email_id:
            return {"error": "No email ID provided", "status": "error"}

        active_cred = _find_credential(user_credentials, account_label)
        if not active_cred:
            return {"error": "No matching email account found", "status": "error"}

        cp, cd, tk, _ = _parse_credential(active_cred)

        if cp in ("gmail", "google") and tk.get("access_token"):
            await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
            result = await _delete_via_gmail_api(tk["access_token"], email_id, permanent)
            if "expired" in result.get("error", "").lower():
                refreshed = await _maybe_refresh(tk, cp, active_cred)
                if refreshed:
                    result = await _delete_via_gmail_api(tk["access_token"], email_id, permanent)
            return result
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
            result = await _delete_via_graph_api(tk["access_token"], email_id)
            if "expired" in result.get("error", "").lower():
                refreshed = await _maybe_refresh(tk, cp, active_cred)
                if refreshed:
                    result = await _delete_via_graph_api(tk["access_token"], email_id)
            return result
        elif cd.get("imap_host"):
            return await _delete_via_imap(
                cd["imap_host"], int(cd.get("imap_port", 993)),
                cd.get("username", ""), cd.get("password", ""),
                email_id, permanent,
            )
        return {"error": "This account does not support deleting emails.", "status": "error"}

    # ------------------------------------------------------------------
    @tool
    async def get_attachments(
        email_id: str,
        account_label: str | None = None,
    ) -> dict:
        """List attachments on a specific email.

        Returns metadata (filename, mime type, size) for each attachment.
        Does NOT download the attachment content.

        Args:
            email_id: The email's unique ID (from read_emails/search_emails).
            account_label: Which account the email is in.

        Returns:
            Dict with ``attachments`` (list of dicts) and ``total``.
        """
        if not email_id:
            return {"error": "No email ID provided", "attachments": [], "total": 0}

        active_cred = _find_credential(user_credentials, account_label)
        if not active_cred:
            return {"error": "No matching email account found", "attachments": [], "total": 0}

        cp, cd, tk, _ = _parse_credential(active_cred)

        if cp in ("gmail", "google") and tk.get("access_token"):
            return await _get_gmail_attachments(tk["access_token"], email_id)
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            return await _get_outlook_attachments(tk["access_token"], email_id)
        elif cd.get("imap_host"):
            return await _get_imap_attachments(
                cd["imap_host"], int(cd.get("imap_port", 993)),
                cd.get("username", ""), cd.get("password", ""),
                email_id,
            )
        return {"error": "This account does not support attachment listing.", "attachments": [], "total": 0}

    # ------------------------------------------------------------------
    @tool
    async def save_draft(
        to: str, subject: str, body: str,
        cc: str | None = None,
        is_html: bool = False,
        attachments: list[dict] | None = None,
        file_ids: list[str] | None = None,
        account_label: str | None = None,
    ) -> dict:
        """Save an email as a draft without sending.

        The draft is saved to the Drafts folder and can be sent later.

        ATTACHMENTS: You may attach files using EITHER:
          - ``file_ids``: list of file IDs from ``list_uploaded_files()``
            (preferred — avoids large base64 data in the conversation).
          - ``attachments``: list of dicts with ``filename``, ``content``
            (base64), and ``mime_type`` (from ``download_file_for_attachment``).

        If both are provided, both sets of attachments are included.

        Args:
            to: Recipient email address.
            subject: Email subject line.
            body: Email body content.
            cc: Optional CC recipient.
            is_html: Set to True if body contains HTML.
            attachments: Optional list of file attachment dicts (base64).
            file_ids: Optional list of uploaded file IDs to attach directly.
            account_label: Which account to save the draft with.

        Returns:
            Dict with ``id`` (draft ID), ``status``, optionally ``error``.
        """
        if not to or not to.strip():
            return {"error": "No recipient email provided", "status": "error"}
        if not subject or not subject.strip():
            return {"error": "No email subject provided", "status": "error"}
        if not body or not body.strip():
            return {"error": "No email body provided", "status": "error"}

        file_attachments = await _resolve_file_attachments(file_ids)
        all_attachments_draft = (attachments or []) + file_attachments
            return {"error": "No email body provided", "status": "error"}

        active_cred = _find_credential(user_credentials, account_label)
        if not active_cred:
            return {"error": "No matching email account found", "status": "error"}

        cp, cd, tk, ce = _parse_credential(active_cred)

        if cp in ("gmail", "google") and tk.get("access_token"):
            await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
            result = await _save_gmail_draft(tk["access_token"], to.strip(), subject.strip(), body, cc, is_html, attachments)
            if "expired" in result.get("error", "").lower():
                refreshed = await _maybe_refresh(tk, cp, active_cred)
                if refreshed:
                    result = await _save_gmail_draft(tk["access_token"], to.strip(), subject.strip(), body, cc, is_html, all_attachments_draft)
            return result
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            await _ensure_fresh_token(tk, cp, "Email", credential_orm=active_cred, tokens_dict=tk, db=db)
            result = await _save_outlook_draft(tk["access_token"], to.strip(), subject.strip(), body, cc, is_html, all_attachments_draft)
            if "expired" in result.get("error", "").lower():
                refreshed = await _maybe_refresh(tk, cp, active_cred)
                if refreshed:
                    result = await _save_outlook_draft(tk["access_token"], to.strip(), subject.strip(), body, cc, is_html, all_attachments_draft)
            return result
        elif cd.get("smtp_host") or cd.get("imap_host"):
            sender = ce or cd.get("from_email", from_email)
            return await _save_imap_draft(
                cd.get("imap_host", ""), int(cd.get("imap_port", 993)),
                cd.get("username", ""), cd.get("password", ""),
                to.strip(), subject.strip(), body, sender, cc, is_html, all_attachments_draft,
            )
        return {"error": "This account does not support saving drafts.", "status": "error"}

    tools = [send_email]
    if user_credentials:
        tools.extend([read_emails, search_emails, get_email_body,
                       mark_email_as_read, mark_email_as_unread,
                       list_folders, move_email,
                       list_email_accounts,
                       forward_email, reply_email, delete_email,
                       get_attachments, save_draft])
    return tools


# =============================================================================
# SMTP
# =============================================================================

async def _send_via_smtp(
    to, subject, body, from_email, from_name="",
    smtp_host="", smtp_port=587, smtp_username="", smtp_password="",
    cc=None, is_html=False, attachments=None,
):
    import asyncio
    if not smtp_host:
        return {"error": "SMTP host not configured.", "status": "error"}
    if not from_email:
        return {"error": "Sender email not configured.", "status": "error"}

    try:
        if attachments:
            msg = _build_mime_with_attachments(
                to, subject, body, from_email, from_name=from_name,
                cc=cc.strip() if cc else None, is_html=is_html,
                attachments=attachments,
            )
        else:
            msg = _build_email_message(
                to, subject, body, from_email, from_name,
                cc.strip() if cc else None, is_html,
            )

        def _send():
            if smtp_port == 465:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=DEFAULT_TIMEOUT)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=DEFAULT_TIMEOUT)
                server.starttls()
            if smtp_username and smtp_password:
                server.login(smtp_username, smtp_password)
            server.send_message(msg)
            server.quit()

        await asyncio.wait_for(asyncio.to_thread(_send), timeout=120.0)
        return {"to": to, "subject": subject, "status": "ok", "provider": "smtp"}
    except asyncio.TimeoutError:
        return {"error": "SMTP send timed out after 120 seconds (file may be too large).", "status": "error"}
    except smtplib.SMTPAuthenticationError:
        return {"error": "SMTP auth failed.", "status": "error"}
    except smtplib.SMTPException as exc:
        return {"error": f"SMTP error: {exc}", "status": "error"}
    except Exception as exc:
        return {"error": f"Email send failed: {exc}", "status": "error"}


# =============================================================================
# SendGrid
# =============================================================================

async def _send_via_sendgrid(
    to, subject, body, from_email, from_name="",
    api_key="", cc=None, is_html=False,
):
    if not api_key:
        return {"error": "SendGrid API key not configured.", "status": "error"}
    if not from_email:
        return {"error": "Sender email not configured.", "status": "error"}

    payload = {
        "personalizations": [{"to": [{"email": to}], "subject": subject}],
        "from": {"email": from_email, "name": from_name or from_email},
        "content": [{"type": "text/html" if is_html else "text/plain", "value": body}],
    }
    if cc:
        payload["personalizations"][0].setdefault("cc", []).append({"email": cc.strip()})

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{SENDGRID_API_BASE}/mail/send",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            if resp.status_code in (200, 201, 202):
                return {"to": to, "subject": subject, "status": "ok", "provider": "sendgrid"}
            return {"error": f"SendGrid error: HTTP {resp.status_code}", "status": "error"}
    except Exception as exc:
        return {"error": f"SendGrid request failed: {exc}", "status": "error"}


# =============================================================================
# Gmail API
# =============================================================================

async def _send_via_gmail_api(to, subject, body, cc=None, is_html=False, access_token="", attachments=None):
    import base64
    from email.mime.text import MIMEText as MimeText

    if not access_token:
        return {"error": "Gmail token expired. Reconnect.", "status": "error"}

    if attachments:
        msg = _build_mime_with_attachments(
            to, subject, body, "", from_name="",
            cc=cc, is_html=is_html, attachments=attachments,
        )
        msg.replace_header("From", "me")
    else:
        msg = MimeText(body, "html" if is_html else "plain", "utf-8")
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{GMAIL_API_BASE}/messages/send",
                json={"raw": raw},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code in (200, 201):
                return {"to": to, "subject": subject, "status": "ok", "provider": "gmail"}
            return {"error": f"Gmail API error: HTTP {resp.status_code}", "status": "error"}
    except Exception as exc:
        return {"error": f"Gmail send failed: {exc}", "status": "error"}


async def _read_gmail_api(access_token, max_results=10, query=""):
    if not access_token:
        return {"error": "Gmail token expired.", "emails": [], "total": 0}

    params = {"maxResults": max_results}
    if query:
        params["q"] = query

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GMAIL_API_BASE}/messages", params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code == 401:
                return {"error": "Gmail token expired.", "emails": [], "total": 0}
            r.raise_for_status()
            data = r.json()

        emails = []
        for ref in (data.get("messages") or [])[:max_results]:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
                d = await c.get(
                    f"{GMAIL_API_BASE}/messages/{ref['id']}",
                    params={"format": "metadata", "metadataHeaders": "From,Subject,Date"},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if d.status_code != 200:
                    continue
                h = {h["name"]: h["value"] for h in d.json().get("payload", {}).get("headers", [])}
                emails.append({"id": ref["id"], "from": h.get("From", ""), "subject": h.get("Subject", ""), "date": h.get("Date", "")})

        return {"emails": emails, "total": len(emails)}
    except Exception as exc:
        return {"error": f"Gmail read failed: {exc}", "emails": [], "total": 0}


# =============================================================================
# Microsoft Graph API
# =============================================================================

async def _send_via_graph_api(to, subject, body, cc=None, is_html=False, access_token="", attachments=None):
    if not access_token:
        return {"error": "Microsoft token expired.", "status": "error"}

    message = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML" if is_html else "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
    }
    if cc:
        message["message"]["ccRecipients"] = [{"emailAddress": {"address": cc.strip()}}]
    graph_attachments = _build_graph_attachments(attachments)
    if graph_attachments:
        message["message"]["attachments"] = graph_attachments

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            r = await client.post(
                f"{GRAPH_API_BASE}/sendMail", json=message,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            )
            if r.status_code in (200, 202):
                return {"to": to, "subject": subject, "status": "ok", "provider": "outlook"}
            return {"error": f"Graph API error: HTTP {r.status_code}", "status": "error"}
    except Exception as exc:
        return {"error": f"Graph send failed: {exc}", "status": "error"}


async def _read_outlook_api(access_token, max_results=10, unread_only=False):
    params = {
        "$top": max_results,
        "$select": "id,from,subject,receivedDateTime,isRead",
        "$orderby": "receivedDateTime desc",
    }
    if unread_only:
        params["$filter"] = "isRead eq false"

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GRAPH_API_BASE}/messages", params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code == 401:
                return {"error": "Microsoft token expired.", "emails": [], "total": 0}
            r.raise_for_status()
            data = r.json()

        emails = [
            {"id": m.get("id", ""), "from": m.get("from", {}).get("emailAddress", {}).get("address", ""),
             "subject": m.get("subject", ""), "date": m.get("receivedDateTime", "")}
            for m in data.get("value", [])
        ]
        return {"emails": emails, "total": len(emails)}
    except Exception as exc:
        return {"error": f"Outlook read failed: {exc}", "emails": [], "total": 0}


async def _search_outlook_api(access_token, query, max_results=10):
    params = {
        "$top": max_results,
        "$select": "id,from,subject,receivedDateTime",
        "$orderby": "receivedDateTime desc",
        "$search": f'"{query}"',
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GRAPH_API_BASE}/messages", params=params,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            )
            if r.status_code == 401:
                return {"error": "Microsoft token expired.", "emails": [], "total": 0}
            r.raise_for_status()
            data = r.json()

        emails = [
            {"id": m.get("id", ""), "from": m.get("from", {}).get("emailAddress", {}).get("address", ""),
             "subject": m.get("subject", ""), "date": m.get("receivedDateTime", "")}
            for m in data.get("value", [])
        ]
        return {"emails": emails, "total": len(emails)}
    except Exception as exc:
        return {"error": f"Outlook search failed: {exc}", "emails": [], "total": 0}


# =============================================================================
# IMAP
# =============================================================================

async def _read_imap(host, port, username, password, folder="INBOX", max_results=10, unread_only=False):
    import asyncio, imaplib, ssl
    import email as em
    from email.header import decode_header

    if not host or not username or not password:
        return {"error": "IMAP credentials incomplete", "emails": [], "total": 0}

    def _fetch():
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        conn.login(username, password)

        try:
            typ, _ = conn.select(folder)
            if typ != "OK":
                conn.select("INBOX")
        except Exception:
            conn.logout()
            return {"error": f"Folder '{folder}' inaccessible", "emails": [], "total": 0}

        search_cmd = "UNSEEN" if unread_only else "ALL"
        typ, data = conn.search(None, search_cmd)
        if typ != "OK":
            conn.logout()
            return {"emails": [], "total": 0}

        uids = data[0].split()[-max_results:] if data[0] else []
        emails = []
        for uid in uids:
            typ, md = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK" or not isinstance(md[0], tuple):
                continue
            msg = em.message_from_bytes(md[0][1])

            def _d(s):
                if not s:
                    return ""
                parts = decode_header(s)
                return " ".join(p.decode(c or "utf-8", errors="replace") if isinstance(p, bytes) else str(p) for p, c in parts)

            emails.append({"id": uid.decode(), "from": _d(msg.get("From", "")), "subject": _d(msg.get("Subject", "")), "date": msg.get("Date", "")})

        conn.logout()
        emails.reverse()
        return {"emails": emails, "total": len(emails)}

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as exc:
        return {"error": f"IMAP read failed: {exc}", "emails": [], "total": 0}


async def _search_imap(host, port, username, password, query, folder="INBOX", max_results=10):
    import asyncio, imaplib, ssl
    import email as em
    from email.header import decode_header

    if not host or not username or not password:
        return {"error": "IMAP credentials incomplete", "emails": [], "total": 0}

    def _search():
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        conn.login(username, password)

        try:
            conn.select(folder)
        except Exception:
            conn.logout()
            return {"error": f"Folder '{folder}' inaccessible", "emails": [], "total": 0}

        try:
            typ, data = conn.search(None, f'SUBJECT "{query}"')
            if typ != "OK":
                conn.logout()
                return {"emails": [], "total": 0}
        except imaplib.IMAP4.error:
            conn.logout()
            return {"emails": [], "total": 0}

        uids = data[0].split()[-max_results:] if data[0] else []
        emails = []
        for uid in uids:
            typ, md = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK" or not isinstance(md[0], tuple):
                continue
            msg = em.message_from_bytes(md[0][1])

            def _d(s):
                if not s:
                    return ""
                parts = decode_header(s)
                return " ".join(p.decode(c or "utf-8", errors="replace") if isinstance(p, bytes) else str(p) for p, c in parts)

            emails.append({"id": uid.decode(), "from": _d(msg.get("From", "")), "subject": _d(msg.get("Subject", "")), "date": msg.get("Date", "")})

        conn.logout()
        emails.reverse()
        return {"emails": emails, "total": len(emails)}

    try:
        return await asyncio.to_thread(_search)
    except Exception as exc:
        return {"error": f"IMAP search failed: {exc}", "emails": [], "total": 0}


# =============================================================================
# Email body fetch helpers
# =============================================================================


async def _get_imap_body(host, port, username, password, email_id):
    """Fetch the full plain-text body of an email via IMAP."""
    import asyncio, imaplib, ssl
    import email as em

    def _fetch():
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        conn.login(username, password)
        conn.select("INBOX")

        typ, md = conn.fetch(email_id.encode(), "(BODY[TEXT])")
        if typ != "OK" or not isinstance(md[0], tuple):
            conn.logout()
            return {"error": "Email not found"}

        raw = md[0][1]
        # Try to decode
        body = raw.decode("utf-8", errors="replace") if raw else ""
        # Also fetch headers for metadata
        typ2, md2 = conn.fetch(email_id.encode(), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        conn.logout()

        result = {"id": email_id, "body": body[:5000]}
        if typ2 == "OK" and isinstance(md2[0], tuple):
            msg = em.message_from_bytes(md2[0][1])
            from email.header import decode_header
            def _d(s):
                if not s:
                    return ""
                parts = decode_header(s)
                return " ".join(p.decode(c or "utf-8", "replace") if isinstance(p, bytes) else str(p) for p, c in parts)
            result["from"] = _d(msg.get("From", ""))
            result["subject"] = _d(msg.get("Subject", ""))
            result["date"] = msg.get("Date", "")
        return result

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as exc:
        return {"error": f"IMAP body fetch failed: {exc}"}


async def _mark_imap_read(host, port, username, password, email_id, read=True):
    """Mark an IMAP email as read (\\Seen) or unread (remove \\Seen)."""
    import asyncio, imaplib, ssl

    def _mark():
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        conn.login(username, password)
        conn.select("INBOX")

        if read:
            typ, _ = conn.store(email_id.encode(), "+FLAGS", "\\Seen")
        else:
            typ, _ = conn.store(email_id.encode(), "-FLAGS", "\\Seen")

        conn.logout()
        if typ == "OK":
            return {"status": "ok", "message": f"Marked as {'read' if read else 'unread'}"}
        return {"error": f"IMAP STORE failed: {typ}", "status": "error"}

    try:
        return await asyncio.to_thread(_mark)
    except Exception as exc:
        return {"error": f"IMAP mark failed: {exc}", "status": "error"}


# =============================================================================
# Gmail API body & modify helpers
# =============================================================================


async def _get_gmail_body(access_token, email_id):
    """Fetch full email body via Gmail API."""
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GMAIL_API_BASE}/messages/{email_id}",
                params={"format": "full"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code == 401:
                return {"error": "Gmail token expired.", "id": email_id}
            r.raise_for_status()
            data = r.json()

        # Extract plain-text body
        payload = data.get("payload", {})
        body = _extract_gmail_body(payload)

        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        return {
            "id": email_id,
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "body": body[:5000],
        }
    except Exception as exc:
        return {"error": f"Gmail body fetch failed: {exc}"}


def _extract_gmail_body(payload):
    """Recursively extract plain-text body from Gmail API payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            import base64
            return base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")
    parts = payload.get("parts", [])
    for part in parts:
        result = _extract_gmail_body(part)
        if result:
            return result
    return ""


async def _modify_gmail(access_token, email_id, add_labels=None, remove_labels=None):
    """Add or remove labels on a Gmail message (e.g., mark read/unread)."""
    body = {}
    if add_labels:
        body["addLabelIds"] = add_labels
    if remove_labels:
        body["removeLabelIds"] = remove_labels

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.post(
                f"{GMAIL_API_BASE}/messages/{email_id}/modify",
                json=body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            if r.status_code == 401:
                return {"error": "Gmail token expired.", "status": "error"}
            if r.status_code == 200:
                return {"status": "ok"}
            return {"error": f"Gmail modify failed: HTTP {r.status_code}", "status": "error"}
    except Exception as exc:
        return {"error": f"Gmail modify failed: {exc}", "status": "error"}


# =============================================================================
# Outlook body & mark helpers
# =============================================================================


async def _get_outlook_body(access_token, email_id):
    """Fetch full email body via Microsoft Graph API."""
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GRAPH_API_BASE}/messages/{email_id}",
                params={"$select": "id,from,subject,body,receivedDateTime"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code == 401:
                return {"error": "Microsoft token expired.", "id": email_id}
            r.raise_for_status()
            data = r.json()

        body_content = data.get("body", {}).get("content", "")
        return {
            "id": email_id,
            "from": data.get("from", {}).get("emailAddress", {}).get("address", ""),
            "subject": data.get("subject", ""),
            "body": body_content[:5000] if body_content else "",
        }
    except Exception as exc:
        return {"error": f"Outlook body fetch failed: {exc}"}


async def _mark_outlook_read(access_token, email_id, is_read=True):
    """Mark an Outlook email as read or unread."""
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.patch(
                f"{GRAPH_API_BASE}/messages/{email_id}",
                json={"isRead": is_read},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            if r.status_code == 401:
                return {"error": "Microsoft token expired.", "status": "error"}
            if r.status_code == 200:
                return {"status": "ok", "message": f"Marked as {'read' if is_read else 'unread'}"}
            return {"error": f"Graph patch failed: HTTP {r.status_code}", "status": "error"}
    except Exception as exc:
        return {"error": f"Outlook mark failed: {exc}", "status": "error"}


# =============================================================================
# Folder listing helpers
# =============================================================================


async def _list_imap_folders(host, port, username, password):
    """List IMAP folders/mailboxes."""
    import asyncio, imaplib, ssl

    def _list():
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        conn.login(username, password)
        folders = []
        typ, data = conn.list()
        if typ == "OK":
            for item in data:
                parts = item.decode().split(' "/" ')
                name = parts[1] if len(parts) == 2 else item.decode().strip()
                folders.append({"name": name})
        conn.logout()
        return {"folders": folders, "total": len(folders)}

    try:
        return await asyncio.to_thread(_list)
    except Exception as exc:
        return {"error": f"IMAP folder list failed: {exc}", "folders": [], "total": 0}


async def _list_gmail_labels(access_token):
    """List Gmail labels."""
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GMAIL_API_BASE}/labels",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code == 401:
                return {"error": "Gmail token expired.", "folders": [], "total": 0}
            r.raise_for_status()
            data = r.json()

        folders = [
            {"name": lbl["name"], "type": lbl.get("type", "")}
            for lbl in data.get("labels", [])
        ]
        return {"folders": folders, "total": len(folders)}
    except Exception as exc:
        return {"error": f"Gmail labels failed: {exc}", "folders": [], "total": 0}


async def _list_outlook_folders(access_token):
    """List Outlook mail folders."""
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GRAPH_API_BASE}/mailFolders",
                params={"$select": "id,displayName"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code == 401:
                return {"error": "Microsoft token expired.", "folders": [], "total": 0}
            r.raise_for_status()
            data = r.json()

        folders = [{"name": f["displayName"], "id": f["id"]} for f in data.get("value", [])]
        return {"folders": folders, "total": len(folders)}
    except Exception as exc:
        return {"error": f"Outlook folders failed: {exc}", "folders": [], "total": 0}


# =============================================================================
# Move email helpers
# =============================================================================


async def _move_imap(host, port, username, password, email_id, target_folder):
    """Move an email to a different IMAP folder via COPY + DELETE."""
    import asyncio, imaplib, ssl

    def _move():
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        conn.login(username, password)
        conn.select("INBOX")

        # Copy to target folder
        typ_copy, _ = conn.copy(email_id.encode(), target_folder)
        if typ_copy != "OK":
            conn.logout()
            return {"error": f"IMAP COPY failed: folder '{target_folder}' not found?", "status": "error"}

        # Mark as deleted in current folder
        conn.store(email_id.encode(), "+FLAGS", "\\Deleted")
        conn.expunge()
        conn.logout()
        return {"status": "ok", "message": f"Moved to '{target_folder}'"}

    try:
        return await asyncio.to_thread(_move)
    except Exception as exc:
        return {"error": f"IMAP move failed: {exc}", "status": "error"}


async def _move_gmail(access_token, email_id, target_label):
    """Move a Gmail message by modifying labels (remove INBOX, add target)."""
    # First get current labels to know what to remove
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GMAIL_API_BASE}/messages/{email_id}",
                params={"format": "metadata"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code == 401:
                return {"error": "Gmail token expired.", "status": "error"}
            r.raise_for_status()
            data = r.json()

        current_labels = data.get("labelIds", [])

        # If moving to a system label (TRASH, SPAM, etc.), use that directly
        system_labels = {"INBOX", "TRASH", "SPAM", "SENT", "DRAFT", "STARRED", "UNREAD", "IMPORTANT"}
        remove_labels = []
        add_labels = []

        if target_label.upper() in system_labels:
            # Moving to a system category — remove INBOX, add target
            if "INBOX" in current_labels and target_label.upper() != "INBOX":
                remove_labels.append("INBOX")
            if target_label.upper() not in current_labels:
                add_labels.append(target_label.upper())
        else:
            # Custom label — keep current labels, add the custom one
            add_labels.append(target_label)

        return await _modify_gmail(access_token, email_id, add_labels=add_labels or None, remove_labels=remove_labels or None)

    except Exception as exc:
        return {"error": f"Gmail move failed: {exc}", "status": "error"}


async def _move_outlook(access_token, email_id, target_folder):
    """Move an Outlook email via Graph API."""
    # First look up the folder ID
    try:
        folders_result = await _list_outlook_folders(access_token)
        if "error" in folders_result:
            return {"error": folders_result["error"], "status": "error"}

        target_id = None
        for f in folders_result["folders"]:
            if f["name"].lower() == target_folder.lower():
                target_id = f["id"]
                break

        if not target_id:
            available = ", ".join(f["name"] for f in folders_result["folders"][:15])
            return {
                "error": f"Folder '{target_folder}' not found. Available: {available}",
                "status": "error",
            }

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.post(
                f"{GRAPH_API_BASE}/messages/{email_id}/move",
                json={"destinationId": target_id},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            if r.status_code == 401:
                return {"error": "Microsoft token expired.", "status": "error"}
            if r.status_code in (200, 201):
                return {"status": "ok", "message": f"Moved to '{target_folder}'"}
            return {"error": f"Graph move failed: HTTP {r.status_code}", "status": "error"}

    except Exception as exc:
        return {"error": f"Outlook move failed: {exc}", "status": "error"}


# =============================================================================
# Forward email helpers
# =============================================================================


async def _forward_via_gmail_api(access_token, email_id, to, body=None, cc=None):
    """Forward an email via Gmail API by fetching raw message and resending."""
    import base64

    if not access_token:
        return {"error": "Gmail token expired.", "status": "error"}

    try:
        # Fetch the original message in raw format
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GMAIL_API_BASE}/messages/{email_id}",
                params={"format": "raw"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code == 401:
                return {"error": "Gmail token expired.", "status": "error"}
            r.raise_for_status()
            data = r.json()

        # Decode the original raw message
        raw_bytes = base64.urlsafe_b64decode(data["raw"] + "===")
        original_msg = raw_bytes.decode("utf-8", errors="replace")

        # Fetch metadata for subject
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r2 = await c.get(
                f"{GMAIL_API_BASE}/messages/{email_id}",
                params={"format": "metadata", "metadataHeaders": "Subject"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r2.raise_for_status()
            meta = r2.json()
            headers = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
            original_subject = headers.get("Subject", "")

        fwd_subject = f"Fwd: {original_subject}" if original_subject and not original_subject.startswith("Fwd:") else original_subject

        # Build new MIME message with original as attachment
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.message import MIMEMessage
        import email as em

        msg = MIMEMultipart("mixed")
        msg["To"] = to
        msg["Subject"] = fwd_subject
        if cc:
            msg["Cc"] = cc

        # Optional comment body
        if body:
            msg.attach(MIMEText(body, "plain", "utf-8"))

        # Attach original message
        orig_parsed = em.message_from_string(original_msg)
        attachment = MIMEMessage(orig_parsed)
        attachment.add_header("Content-Disposition", "attachment", filename="Forwarded message.eml")
        msg.attach(attachment)

        raw_send = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r3 = await c.post(
                f"{GMAIL_API_BASE}/messages/send",
                json={"raw": raw_send},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r3.status_code in (200, 201):
                return {"to": to, "subject": fwd_subject, "status": "ok", "provider": "gmail"}
            return {"error": f"Gmail forward failed: HTTP {r3.status_code}", "status": "error"}

    except Exception as exc:
        logger.error("Gmail forward failed: %s", exc)
        return {"error": f"Gmail forward failed: {exc}", "status": "error"}


async def _forward_via_graph_api(access_token, email_id, to, body=None, cc=None):
    """Forward an email via Microsoft Graph API using the native forward endpoint."""
    if not access_token:
        return {"error": "Microsoft token expired.", "status": "error"}

    try:
        message = {
            "message": {
                "toRecipients": [{"emailAddress": {"address": to}}],
            },
            "comment": body or "",
        }
        if cc:
            message["message"]["ccRecipients"] = [{"emailAddress": {"address": cc.strip()}}]

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.post(
                f"{GRAPH_API_BASE}/messages/{email_id}/forward",
                json=message,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            )
            if r.status_code == 401:
                return {"error": "Microsoft token expired.", "status": "error"}
            if r.status_code in (200, 202):
                return {"to": to, "status": "ok", "provider": "outlook"}
            return {"error": f"Graph forward failed: HTTP {r.status_code}", "status": "error"}

    except Exception as exc:
        logger.error("Graph forward failed: %s", exc)
        return {"error": f"Graph forward failed: {exc}", "status": "error"}


async def _forward_via_imap(host, port, username, password, email_id, to, body=None, cc=None):
    """Forward an email via IMAP fetch + SMTP send with original as attachment."""
    import asyncio, imaplib, ssl
    import email as em
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.message import MIMEMessage

    if not host or not username or not password:
        return {"error": "IMAP credentials incomplete", "status": "error"}

    def _forward():
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        conn.login(username, password)
        conn.select("INBOX")

        # Fetch full raw message
        typ, md = conn.fetch(email_id.encode(), "(BODY[])")
        if typ != "OK" or not isinstance(md[0], tuple):
            conn.logout()
            return {"error": "Email not found", "status": "error"}

        raw_bytes = md[0][1]
        # Parse to get subject
        orig_msg = em.message_from_bytes(raw_bytes)
        original_subject = orig_msg.get("Subject", "")

        conn.logout()

        fwd_subject = f"Fwd: {original_subject}" if original_subject and not original_subject.startswith("Fwd:") else original_subject

        # Build forward MIME
        msg = MIMEMultipart("mixed")
        msg["To"] = to
        msg["From"] = username
        msg["Subject"] = fwd_subject
        if cc:
            msg["Cc"] = cc

        if body:
            msg.attach(MIMEText(body, "plain", "utf-8"))

        attachment = MIMEMessage(orig_msg)
        attachment.add_header("Content-Disposition", "attachment", filename="Forwarded message.eml")
        msg.attach(attachment)

        # Send via SMTP
        import smtplib
        try:
            srv = smtplib.SMTP_SSL(host, port, timeout=DEFAULT_TIMEOUT) if port == 465 else smtplib.SMTP(host, port, timeout=DEFAULT_TIMEOUT)
            if port != 465:
                srv.starttls()
            srv.login(username, password)
            srv.send_message(msg)
            srv.quit()
            return {"to": to, "subject": fwd_subject, "status": "ok", "provider": "imap"}
        except smtplib.SMTPException as exc:
            return {"error": f"SMTP forward failed: {exc}", "status": "error"}

    try:
        return await asyncio.to_thread(_forward)
    except Exception as exc:
        return {"error": f"IMAP forward failed: {exc}", "status": "error"}


# =============================================================================
# Reply email helpers
# =============================================================================


async def _reply_via_gmail_api(access_token, email_id, body, reply_all=False, attachments=None):
    """Reply to an email via Gmail API, preserving thread context."""
    import base64
    from email.mime.text import MIMEText as MimeText

    if not access_token:
        return {"error": "Gmail token expired.", "status": "error"}

    try:
        # Fetch original message metadata for threading
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GMAIL_API_BASE}/messages/{email_id}",
                params={"format": "metadata", "metadataHeaders": "From,Subject,Message-ID,References"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code == 401:
                return {"error": "Gmail token expired.", "status": "error"}
            r.raise_for_status()
            data = r.json()

        headers = {h["name"]: h["value"] for h in data.get("payload", {}).get("headers", [])}
        original_from = headers.get("From", "")
        original_subject = headers.get("Subject", "")
        original_msg_id = headers.get("Message-ID", "")
        original_references = headers.get("References", "")

        import email.utils
        original_sender = email.utils.parseaddr(original_from)[1]

        reply_subject = f"Re: {original_subject}" if original_subject and not original_subject.startswith("Re:") else original_subject

        if attachments:
            msg = _build_mime_with_attachments(
                original_sender, reply_subject, body, "",
                cc=None, is_html=False, attachments=attachments,
            )
            msg.replace_header("From", "me")
            if original_msg_id:
                msg["In-Reply-To"] = original_msg_id
                msg["References"] = f"{original_references} {original_msg_id}".strip() if original_references else original_msg_id
        else:
            msg = MimeText(body, "plain", "utf-8")
            msg["To"] = original_sender
            msg["Subject"] = reply_subject
            if original_msg_id:
                msg["In-Reply-To"] = original_msg_id
                msg["References"] = f"{original_references} {original_msg_id}".strip() if original_references else original_msg_id

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r2 = await c.post(
                f"{GMAIL_API_BASE}/messages/send",
                json={"raw": raw, "threadId": data.get("threadId", "")},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r2.status_code in (200, 201):
                return {"status": "ok", "message": "Reply sent.", "provider": "gmail"}
            return {"error": f"Gmail reply failed: HTTP {r2.status_code}", "status": "error"}

    except Exception as exc:
        logger.error("Gmail reply failed: %s", exc)
        return {"error": f"Gmail reply failed: {exc}", "status": "error"}


async def _reply_via_graph_api(access_token, email_id, body, reply_all=False, attachments=None):
    """Reply to an email via Microsoft Graph API."""
    if not access_token:
        return {"error": "Microsoft token expired.", "status": "error"}

    endpoint = f"{GRAPH_API_BASE}/messages/{email_id}/replyAll" if reply_all else f"{GRAPH_API_BASE}/messages/{email_id}/reply"

    try:
        message = {
            "message": {},
            "comment": body,
        }
        graph_attachments = _build_graph_attachments(attachments)
        if graph_attachments:
            message["message"]["attachments"] = graph_attachments

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.post(
                endpoint,
                json=message,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            )
            if r.status_code == 401:
                return {"error": "Microsoft token expired.", "status": "error"}
            if r.status_code in (200, 202):
                return {"status": "ok", "message": "Reply sent.", "provider": "outlook"}
            return {"error": f"Graph reply failed: HTTP {r.status_code}", "status": "error"}

    except Exception as exc:
        logger.error("Graph reply failed: %s", exc)
        return {"error": f"Graph reply failed: {exc}", "status": "error"}


async def _reply_via_imap(host, port, username, password, email_id, body, reply_all=False, attachments=None):
    """Reply to an email via IMAP fetch + SMTP send with threading headers."""
    import asyncio, imaplib, ssl
    import email as em
    from email.mime.text import MIMEText as MimeText
    import email.utils

    if not host or not username or not password:
        return {"error": "IMAP credentials incomplete", "status": "error"}

    def _reply():
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        conn.login(username, password)
        conn.select("INBOX")

        typ, md = conn.fetch(email_id.encode(), "(BODY.PEEK[HEADER])")
        if typ != "OK" or not isinstance(md[0], tuple):
            conn.logout()
            return {"error": "Email not found", "status": "error"}

        orig_msg = em.message_from_bytes(md[0][1])
        conn.logout()

        original_from = orig_msg.get("From", "")
        original_subject = orig_msg.get("Subject", "")
        original_msg_id = orig_msg.get("Message-ID", "")
        original_references = orig_msg.get("References", "")
        original_to = orig_msg.get("To", "")

        original_sender = email.utils.parseaddr(original_from)[1]

        reply_subject = f"Re: {original_subject}" if original_subject and not original_subject.startswith("Re:") else original_subject

        if attachments:
            if reply_all:
                all_to = original_from
                if original_to:
                    all_to = f"{all_to}, {original_to}"
                msg = _build_mime_with_attachments(
                    all_to, reply_subject, body, username,
                    cc=None, is_html=False, attachments=attachments,
                )
            else:
                msg = _build_mime_with_attachments(
                    original_sender, reply_subject, body, username,
                    cc=None, is_html=False, attachments=attachments,
                )
            if original_msg_id:
                msg["In-Reply-To"] = original_msg_id
                msg["References"] = f"{original_references} {original_msg_id}".strip() if original_references else original_msg_id
        else:
            if reply_all:
                all_to = original_from
                if original_to:
                    all_to = f"{all_to}, {original_to}"
                msg = MimeText(body, "plain", "utf-8")
                msg["To"] = all_to
            else:
                msg = MimeText(body, "plain", "utf-8")
                msg["To"] = original_sender
            msg["Subject"] = reply_subject
            if original_msg_id:
                msg["In-Reply-To"] = original_msg_id
                msg["References"] = f"{original_references} {original_msg_id}".strip() if original_references else original_msg_id

        import smtplib
        try:
            srv = smtplib.SMTP_SSL(host, port, timeout=DEFAULT_TIMEOUT) if port == 465 else smtplib.SMTP(host, port, timeout=DEFAULT_TIMEOUT)
            if port != 465:
                srv.starttls()
            srv.login(username, password)
            srv.send_message(msg)
            srv.quit()
            return {"status": "ok", "message": "Reply sent.", "provider": "imap"}
        except smtplib.SMTPException as exc:
            return {"error": f"SMTP reply failed: {exc}", "status": "error"}

    try:
        return await asyncio.to_thread(_reply)
    except Exception as exc:
        return {"error": f"IMAP reply failed: {exc}", "status": "error"}


# =============================================================================
# Delete email helpers
# =============================================================================


async def _delete_via_gmail_api(access_token, email_id, permanent=False):
    """Delete a Gmail message: trash (default) or permanent."""
    if not access_token:
        return {"error": "Gmail token expired.", "status": "error"}

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            if permanent:
                r = await c.delete(
                    f"{GMAIL_API_BASE}/messages/{email_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            else:
                r = await c.post(
                    f"{GMAIL_API_BASE}/messages/{email_id}/trash",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if r.status_code == 401:
                return {"error": "Gmail token expired.", "status": "error"}
            if r.status_code in (200, 204):
                action = "permanently deleted" if permanent else "moved to trash"
                return {"status": "ok", "message": f"Email {action}.", "provider": "gmail"}
            return {"error": f"Gmail delete failed: HTTP {r.status_code}", "status": "error"}
    except Exception as exc:
        logger.error("Gmail delete failed: %s", exc)
        return {"error": f"Gmail delete failed: {exc}", "status": "error"}


async def _delete_via_graph_api(access_token, email_id):
    """Delete an Outlook message (moves to Deleted Items)."""
    if not access_token:
        return {"error": "Microsoft token expired.", "status": "error"}

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.delete(
                f"{GRAPH_API_BASE}/messages/{email_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code == 401:
                return {"error": "Microsoft token expired.", "status": "error"}
            if r.status_code in (200, 204):
                return {"status": "ok", "message": "Email moved to Deleted Items.", "provider": "outlook"}
            return {"error": f"Graph delete failed: HTTP {r.status_code}", "status": "error"}
    except Exception as exc:
        logger.error("Graph delete failed: %s", exc)
        return {"error": f"Graph delete failed: {exc}", "status": "error"}


async def _delete_via_imap(host, port, username, password, email_id, permanent=False):
    """Delete an IMAP email: mark \\Deleted (soft) or \\Deleted + EXPUNGE (permanent)."""
    import asyncio, imaplib, ssl

    def _delete():
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        conn.login(username, password)
        conn.select("INBOX")

        # Mark as deleted
        typ, _ = conn.store(email_id.encode(), "+FLAGS", "\\Deleted")
        if typ != "OK":
            conn.logout()
            return {"error": "IMAP STORE failed", "status": "error"}

        if permanent:
            conn.expunge()

        conn.logout()
        action = "permanently deleted" if permanent else "marked as deleted (recoverable)"
        return {"status": "ok", "message": f"Email {action}.", "provider": "imap"}

    try:
        return await asyncio.to_thread(_delete)
    except Exception as exc:
        return {"error": f"IMAP delete failed: {exc}", "status": "error"}


# =============================================================================
# Attachment listing helpers
# =============================================================================


async def _get_gmail_attachments(access_token, email_id):
    """List attachments on a Gmail message."""
    if not access_token:
        return {"error": "Gmail token expired.", "attachments": [], "total": 0}

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GMAIL_API_BASE}/messages/{email_id}",
                params={"format": "full"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code == 401:
                return {"error": "Gmail token expired.", "attachments": [], "total": 0}
            r.raise_for_status()
            data = r.json()

        attachments = []
        payload = data.get("payload", {})

        def _find_attachments(part):
            if part.get("filename"):
                body = part.get("body", {})
                attachments.append({
                    "filename": part.get("filename", ""),
                    "mime_type": part.get("mimeType", ""),
                    "size": body.get("size", 0),
                    "attachment_id": body.get("attachmentId", ""),
                })
            for sub in part.get("parts", []):
                _find_attachments(sub)

        _find_attachments(payload)

        return {"attachments": attachments, "total": len(attachments)}

    except Exception as exc:
        logger.error("Gmail attachments failed: %s", exc)
        return {"error": f"Gmail attachments failed: {exc}", "attachments": [], "total": 0}


async def _get_outlook_attachments(access_token, email_id):
    """List attachments on an Outlook message via Graph API."""
    if not access_token:
        return {"error": "Microsoft token expired.", "attachments": [], "total": 0}

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(
                f"{GRAPH_API_BASE}/messages/{email_id}/attachments",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code == 401:
                return {"error": "Microsoft token expired.", "attachments": [], "total": 0}
            r.raise_for_status()
            data = r.json()

        attachments = [
            {
                "filename": a.get("name", ""),
                "mime_type": a.get("contentType", ""),
                "size": a.get("size", 0),
                "attachment_id": a.get("id", ""),
            }
            for a in data.get("value", [])
        ]
        return {"attachments": attachments, "total": len(attachments)}

    except Exception as exc:
        logger.error("Outlook attachments failed: %s", exc)
        return {"error": f"Outlook attachments failed: {exc}", "attachments": [], "total": 0}


async def _get_imap_attachments(host, port, username, password, email_id):
    """List attachments on an IMAP email by parsing MIME parts."""
    import asyncio, imaplib, ssl
    import email as em

    def _fetch():
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        conn.login(username, password)
        conn.select("INBOX")

        typ, md = conn.fetch(email_id.encode(), "(BODY[])")
        if typ != "OK" or not isinstance(md[0], tuple):
            conn.logout()
            return {"error": "Email not found", "attachments": [], "total": 0}

        msg = em.message_from_bytes(md[0][1])
        conn.logout()

        attachments = []

        def _walk(part):
            if part.get_content_maintype() == "multipart":
                for sub in part.get_payload():
                    _walk(sub)
            elif part.get_filename():
                attachments.append({
                    "filename": part.get_filename(),
                    "mime_type": part.get_content_type(),
                    "size": len(part.get_payload(decode=True) or b""),
                })

        _walk(msg)
        return {"attachments": attachments, "total": len(attachments)}

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as exc:
        return {"error": f"IMAP attachments failed: {exc}", "attachments": [], "total": 0}


# =============================================================================
# Draft save helpers
# =============================================================================


async def _save_gmail_draft(access_token, to, subject, body, cc=None, is_html=False, attachments=None):
    """Save a draft via Gmail API."""
    import base64
    from email.mime.text import MIMEText as MimeText

    if not access_token:
        return {"error": "Gmail token expired.", "status": "error"}

    try:
        if attachments:
            msg = _build_mime_with_attachments(
                to, subject, body, "", from_name="",
                cc=cc, is_html=is_html, attachments=attachments,
            )
            msg.replace_header("From", "me")
        else:
            msg = MimeText(body, "html" if is_html else "plain", "utf-8")
            msg["To"] = to
            msg["Subject"] = subject
            if cc:
                msg["Cc"] = cc

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.post(
                f"{GMAIL_API_BASE}/drafts",
                json={"message": {"raw": raw}},
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            )
            if r.status_code == 401:
                return {"error": "Gmail token expired.", "status": "error"}
            if r.status_code in (200, 201):
                data = r.json()
                return {"id": data.get("id", ""), "status": "draft_saved", "provider": "gmail"}
            return {"error": f"Gmail draft save failed: HTTP {r.status_code}", "status": "error"}

    except Exception as exc:
        logger.error("Gmail draft save failed: %s", exc)
        return {"error": f"Gmail draft save failed: {exc}", "status": "error"}


async def _save_outlook_draft(access_token, to, subject, body, cc=None, is_html=False, attachments=None):
    """Save a draft via Microsoft Graph API."""
    if not access_token:
        return {"error": "Microsoft token expired.", "status": "error"}

    try:
        message = {
            "subject": subject,
            "body": {"contentType": "HTML" if is_html else "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
            "isDraft": True,
        }
        if cc:
            message["ccRecipients"] = [{"emailAddress": {"address": cc.strip()}}]
        graph_attachments = _build_graph_attachments(attachments)
        if graph_attachments:
            message["attachments"] = graph_attachments

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.post(
                f"{GRAPH_API_BASE}/messages",
                json=message,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            )
            if r.status_code == 401:
                return {"error": "Microsoft token expired.", "status": "error"}
            if r.status_code in (200, 201):
                data = r.json()
                return {"id": data.get("id", ""), "status": "draft_saved", "provider": "outlook"}
            return {"error": f"Graph draft save failed: HTTP {r.status_code}", "status": "error"}

    except Exception as exc:
        logger.error("Graph draft save failed: %s", exc)
        return {"error": f"Graph draft save failed: {exc}", "status": "error"}


async def _save_imap_draft(imap_host, imap_port, username, password, to, subject, body, sender, cc=None, is_html=False, attachments=None):
    """Save a draft by appending to the IMAP Drafts folder."""
    import asyncio, imaplib, ssl
    from email.mime.text import MIMEText as MimeText

    def _save():
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(imap_host, imap_port, ssl_context=ctx)
        conn.login(username, password)

        if attachments:
            msg = _build_mime_with_attachments(
                to, subject, body, sender,
                from_name="", cc=cc, is_html=is_html, attachments=attachments,
            )
        else:
            msg = MimeText(body, "html" if is_html else "plain", "utf-8")
            msg["To"] = to
            msg["From"] = sender
            msg["Subject"] = subject
            if cc:
                msg["Cc"] = cc

        # Try to append to Drafts folder
        drafts_folders = ["Drafts", "Draft", "[Gmail]/Drafts"]
        saved = False
        for folder in drafts_folders:
            try:
                typ, _ = conn.append(folder, "\\Draft", imaplib.Time2Internaldate(None), msg.as_bytes().encode("utf-8"))
                if typ == "OK":
                    saved = True
                    break
            except Exception:
                continue

        if not saved:
            # Fallback: try INBOX
            try:
                typ, _ = conn.append("INBOX", "\\Draft", imaplib.Time2Internaldate(None), msg.as_bytes().encode("utf-8"))
                if typ == "OK":
                    saved = True
            except Exception:
                pass

        conn.logout()

        if saved:
            return {"status": "draft_saved", "message": "Draft saved.", "provider": "imap"}
        return {"error": "Could not save draft to any folder.", "status": "error"}

    try:
        return await asyncio.to_thread(_save)
    except Exception as exc:
        return {"error": f"IMAP draft save failed: {exc}", "status": "error"}
