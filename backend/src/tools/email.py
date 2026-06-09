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

async def _refresh_token_if_expired(
    tokens: dict, provider: str, tool_name: str = "Email",
) -> dict | None:
    """Try to refresh an OAuth token. Returns updated tokens dict or None."""
    from ..core.oauth import refresh_oauth_token
    from ..core.config import settings

    refresh_token = tokens.get("refresh_token", "")
    if not refresh_token:
        return None

    if provider in ("gmail", "google"):
        client_id = settings.GOOGLE_CLIENT_ID
        client_secret = settings.GOOGLE_CLIENT_SECRET
    else:
        client_id = settings.MS_CLIENT_ID
        client_secret = settings.MS_CLIENT_SECRET

    try:
        result = await refresh_oauth_token(tokens, provider, client_id, client_secret)
        if result:
            tokens["access_token"] = result.get("access_token", tokens.get("access_token", ""))
            tokens["expires_at"] = result.get("expires_at", tokens.get("expires_at", 0))
            return tokens
    except Exception:
        logger.warning("%s token refresh failed", tool_name, exc_info=True)

    return None


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
) -> list:
    """Return a list of MAF @tool-decorated async functions for email.

    Supports SMTP, SendGrid, IMAP, Gmail REST API, and Microsoft Graph API.

    Args:
        tool_config: ``Tool.config`` JSON dict (tenant-level).
        user_credentials: List of ``UserToolCredential`` ORM rows for
            per-user email accounts.

    Returns:
        A list of MAF tool callables.
    """
    config = tool_config or {}
    creds = _resolve_credentials(config)
    provider: str = creds.get("provider", "smtp").lower()
    from_email: str = creds.get("from_email", "")
    from_name: str = creds.get("from_name", "")
    allowed_recipients: list[str] = config.get("allowed_recipients", [])

    # ------------------------------------------------------------------
    @tool
    async def send_email(
        to: str, subject: str, body: str,
        cc: str | None = None, is_html: bool = False,
        account_label: str | None = None,
    ) -> dict:
        """Send an email via SMTP, SendGrid, Gmail API, or Outlook API.

        If the user has connected email accounts, ``account_label``
        specifies which account to send from (e.g. "Work Gmail").
        Uses the default account if not specified.

        Args:
            to: Recipient email address.
            subject: Email subject line.
            body: Email body content.
            cc: Optional CC recipient.
            is_html: Set to True if body contains HTML.
            account_label: Connected account label (required when
                          multiple accounts are configured).

        Returns:
            A dict with ``to``, ``subject``, ``status``, optionally ``error``.
        """
        active_cred = _find_credential(user_credentials, account_label)
        if active_cred:
            cp, cd, tk, ce = _parse_credential(active_cred)
            if cp in ("gmail", "google") and tk.get("access_token"):
                result = await _send_via_gmail_api(to, subject, body, cc, is_html, tk["access_token"])
                if "expired" in result.get("error", "").lower():
                    refreshed = await _refresh_token_if_expired(tk, cp, "Gmail")
                    if refreshed:
                        result = await _send_via_gmail_api(to, subject, body, cc, is_html, tk["access_token"])
                return result
            elif cp in ("outlook", "microsoft") and tk.get("access_token"):
                result = await _send_via_graph_api(to, subject, body, cc, is_html, tk["access_token"])
                if "expired" in result.get("error", "").lower():
                    refreshed = await _refresh_token_if_expired(tk, cp, "Outlook")
                    if refreshed:
                        result = await _send_via_graph_api(to, subject, body, cc, is_html, tk["access_token"])
                return result
            elif cd.get("smtp_host"):
                sender = ce or cd.get("from_email", from_email)
                return await _send_via_smtp(
                    to, subject, body, sender,
                    smtp_host=cd["smtp_host"],
                    smtp_port=int(cd.get("smtp_port", 587)),
                    smtp_username=cd.get("username", ""),
                    smtp_password=cd.get("password", ""),
                    cc=cc, is_html=is_html,
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
                cc=cc, is_html=is_html,
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
            result = await _read_gmail_api(tk["access_token"], limit, "is:unread " if unread_only else "")
            if "expired" in result.get("error", "").lower():
                refreshed = await _refresh_token_if_expired(tk, cp, "Gmail")
                if refreshed:
                    result = await _read_gmail_api(tk["access_token"], limit, "is:unread " if unread_only else "")
            return result
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            result = await _read_outlook_api(tk["access_token"], limit, unread_only)
            if "expired" in result.get("error", "").lower():
                refreshed = await _refresh_token_if_expired(tk, cp, "Outlook")
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
            result = await _read_gmail_api(tk["access_token"], limit, query)
            if "expired" in result.get("error", "").lower():
                refreshed = await _refresh_token_if_expired(tk, cp, "Gmail")
                if refreshed:
                    result = await _read_gmail_api(tk["access_token"], limit, query)
            return result
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            result = await _search_outlook_api(tk["access_token"], query, limit)
            if "expired" in result.get("error", "").lower():
                refreshed = await _refresh_token_if_expired(tk, cp, "Outlook")
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

    tools = [send_email]
    if user_credentials:
        tools.extend([read_emails, search_emails, get_email_body,
                       mark_email_as_read, mark_email_as_unread,
                       list_folders, move_email,
                       list_email_accounts])
    return tools


# =============================================================================
# SMTP
# =============================================================================

async def _send_via_smtp(
    to, subject, body, from_email, from_name="",
    smtp_host="", smtp_port=587, smtp_username="", smtp_password="",
    cc=None, is_html=False,
):
    import asyncio
    if not smtp_host:
        return {"error": "SMTP host not configured.", "status": "error"}
    if not from_email:
        return {"error": "Sender email not configured.", "status": "error"}

    try:
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

        await asyncio.to_thread(_send)
        return {"to": to, "subject": subject, "status": "ok", "provider": "smtp"}
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

async def _send_via_gmail_api(to, subject, body, cc=None, is_html=False, access_token=""):
    import base64
    from email.mime.text import MIMEText as MimeText

    if not access_token:
        return {"error": "Gmail token expired. Reconnect.", "status": "error"}

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

async def _send_via_graph_api(to, subject, body, cc=None, is_html=False, access_token=""):
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
