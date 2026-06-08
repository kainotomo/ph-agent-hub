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
                return await _send_via_gmail_api(to, subject, body, cc, is_html, tk["access_token"])
            elif cp in ("outlook", "microsoft") and tk.get("access_token"):
                return await _send_via_graph_api(to, subject, body, cc, is_html, tk["access_token"])
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
            return await _read_gmail_api(tk["access_token"], limit, "is:unread " if unread_only else "")
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            return await _read_outlook_api(tk["access_token"], limit, unread_only)
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
            return await _read_gmail_api(tk["access_token"], limit, query)
        elif cp in ("outlook", "microsoft") and tk.get("access_token"):
            return await _search_outlook_api(tk["access_token"], query, limit)
        elif cd.get("imap_host"):
            return await _search_imap(
                cd["imap_host"], int(cd.get("imap_port", 993)),
                cd.get("username", ""), cd.get("password", ""),
                query, folder, limit,
            )
        return {"error": "This account does not support search.", "emails": [], "total": 0}

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
        tools.extend([read_emails, search_emails, list_email_accounts])
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
