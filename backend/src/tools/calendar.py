# =============================================================================
# PH Agent Hub — Calendar Tool Factory
# =============================================================================
# Google Calendar or CalDAV integration. List/create events, find free slots.
# OAuth per user or service account at tenant level.
#
# Dependencies: httpx (already installed)
# =============================================================================

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx
from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT: float = 30.0
GOOGLE_CALENDAR_API_BASE: str = "https://www.googleapis.com/calendar/v3"
GRAPH_API_BASE: str = "https://graph.microsoft.com/v1.0/me"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_credentials(tool_config: dict) -> dict:
    """Resolve and decrypt credentials from config."""
    from ..core.encryption import decrypt

    creds = tool_config.get("credentials", {})
    if isinstance(creds, str):
        try:
            import json
            creds = json.loads(creds)
        except Exception:
            return {}

    # Decrypt sensitive fields
    decrypted = dict(creds)
    for key in ("client_secret", "refresh_token", "access_token", "api_key", "private_key"):
        if key in decrypted and decrypted[key]:
            try:
                decrypted[key] = decrypt(decrypted[key])
            except Exception:
                pass  # Already plaintext

    return decrypted


async def _get_google_access_token(credentials: dict) -> str | None:
    """Get or refresh a Google API access token."""
    # If access token provided directly and not expired
    access_token = credentials.get("access_token", "")
    if access_token:
        return access_token

    # If service account — use the key
    if "client_email" in credentials and "private_key" in credentials:
        try:
            import time
            from ..core.jwt import create_jwt

            now = int(time.time())
            assertion = create_jwt(
                issuer=credentials["client_email"],
                subject=credentials.get("calendar_id", credentials["client_email"]),
                audience="https://oauth2.googleapis.com/token",
                scope="https://www.googleapis.com/auth/calendar",
                private_key=credentials["private_key"],
                expiration=now + 3600,
            )

            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("access_token")
        except ImportError:
            logger.warning("JWT module not available for service account auth")
        except Exception as exc:
            logger.error("Service account auth failed: %s", exc)

    # If refresh token — use it
    refresh_token = credentials.get("refresh_token", "")
    client_id = credentials.get("client_id", "")
    client_secret = credentials.get("client_secret", "")

    if refresh_token and client_id and client_secret:
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("access_token")
        except Exception as exc:
            logger.error("Token refresh failed: %s", exc)

    # If API key — use it
    api_key = credentials.get("api_key", "")
    if api_key:
        return api_key  # Will be used as ?key= parameter

    return None


def _parse_datetime(dt_str: str) -> str:
    """Normalize a datetime string to RFC 3339 format."""
    if not dt_str:
        return ""

    # Try various formats
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(dt_str.replace("Z", "+00:00"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue

    # If all parsing fails, return as-is
    return dt_str


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


async def _detect_timezone(provider: str, access_token: str) -> str | None:
    """Detect the user's timezone from their connected account."""
    try:
        if provider in ("outlook", "microsoft"):
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                resp = await client.get(
                    f"{GRAPH_API_BASE}/mailboxSettings",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tz = data.get("timeZone", "")
                    if tz:
                        logger.info("Detected Microsoft timezone: %s", tz)
                        return tz

        elif provider in ("gmail", "google"):
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                resp = await client.get(
                    "https://www.googleapis.com/calendar/v3/users/me/settings/timezone",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tz = data.get("value", "")
                    if tz:
                        logger.info("Detected Google timezone: %s", tz)
                        return tz
    except Exception:
        logger.warning("Failed to detect timezone for %s", provider, exc_info=True)

    return None


def build_calendar_tools(
    tool_config: dict | None = None,
    user_credentials: list | None = None,
) -> list:
    """Return a list of MAF @tool-decorated async functions for calendar.

    Supports Google Calendar API (service account, OAuth, API key),
    and Microsoft Graph Calendar. When ``user_credentials`` are
    provided, per-user OAuth tokens override tenant-level config.

    Args:
        tool_config: ``Tool.config`` JSON dict.  May include:
            - ``provider`` (str): "google" (default)
            - ``credentials`` (dict): Google API credentials
            - ``calendar_id`` (str): Calendar ID (default "primary")
            - ``timezone`` (str): Timezone for events (default "UTC")
        user_credentials: List of ``UserToolCredential`` ORM rows.

    Returns:
        A list of callables ready to pass to ``Agent(tools=...)``.
    """
    config = tool_config or {}
    provider: str = config.get("provider", "google").lower()
    credentials: dict = _resolve_credentials(config)
    calendar_id: str = config.get("calendar_id", "primary")
    timezone_str: str = config.get("timezone", "UTC")

    # ---- Resolve user OAuth tokens if available (Issue #312) ----------
    user_creds_map = {}
    if user_credentials:
        uc = user_credentials[0]  # Use first available credential
        cp, cd, tk, ce = _parse_credential(uc)
        if tk.get("access_token"):
            user_creds_map = {
                "provider": cp,
                "access_token": tk["access_token"],
                "refresh_token": tk.get("refresh_token", ""),
                "calendar_id": config.get("calendar_id", "primary"),
            }

            # Auto-detect timezone from connected account
            detected_tz = await _detect_timezone(cp, tk["access_token"])
            if detected_tz:
                timezone_str = detected_tz

    # ------------------------------------------------------------------
    @tool
    async def list_events(
        date_from: str, date_to: str | None = None, max_results: int = 25,
        calendar_label: str | None = None,
    ) -> dict:
        """List calendar events in a date range.

        Args:
            date_from: Start date/time in ISO format (e.g., "2024-01-01" or "2024-01-01T00:00:00").
            date_to: End date/time (optional, defaults to 7 days after date_from).
            max_results: Maximum number of events to return (default 25).

        Returns:
            A dict with:
            - ``events``: list of event dicts (summary, start, end, location, description)
            - ``total``: number of events returned
            - ``error``: error message if failed
        """
        if not date_from:
            return {"error": "No start date provided", "events": [], "total": 0}

        time_min = _parse_datetime(date_from)

        if date_to:
            time_max = _parse_datetime(date_to)
        else:
            # Default to 7 days later
            try:
                dt = datetime.fromisoformat(time_min)
                time_max = (dt + timedelta(days=7)).isoformat()
            except ValueError:
                time_max = time_min

        # Determine which provider to use: user OAuth credentials take priority
        active_provider = provider
        active_token = None
        active_calendar_id = calendar_id

        if user_creds_map:
            up = user_creds_map.get("provider", "")
            if up in ("outlook", "microsoft"):
                active_provider = "microsoft"
                active_token = user_creds_map.get("access_token", "")
            elif up in ("gmail", "google"):
                active_provider = "google"
                active_token = user_creds_map.get("access_token", "")
                active_calendar_id = user_creds_map.get("calendar_id", "primary")

        if active_provider == "google":
            if not active_token:
                active_token = await _get_google_access_token(credentials)
            if not active_token:
                return {
                    "error": (
                        "Calendar is not configured. Please set up Google Calendar "
                        "credentials (service account, OAuth, or API key) in the tool config."
                    ),
                    "events": [],
                    "total": 0,
                }

            headers = {"Authorization": f"Bearer {active_token}"} if len(active_token) > 50 else {}
            params = {
                "timeMin": time_min,
                "timeMax": time_max,
                "maxResults": min(max_results, 250),
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeZone": timezone_str,
            }
            if len(active_token) <= 50:
                params["key"] = active_token  # API key mode

            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    response = await client.get(
                        f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(active_calendar_id)}/events",
                        params=params,
                        headers=headers,
                    )

                    if response.status_code == 401:
                        return {"error": "Calendar authentication failed. Check credentials.", "events": [], "total": 0}
                    elif response.status_code == 404:
                        return {"error": f"Calendar '{active_calendar_id}' not found.", "events": [], "total": 0}

                    response.raise_for_status()
                    data = response.json()

            except Exception as exc:
                logger.error("Google Calendar API failed: %s", exc)
                return {"error": f"Calendar API request failed: {str(exc)}", "events": [], "total": 0}

            items = data.get("items", [])
            events = []
            for item in items:
                start_info = item.get("start", {})
                end_info = item.get("end", {})

                events.append({
                    "id": item.get("id", ""),
                    "summary": item.get("summary", "Untitled"),
                    "description": item.get("description", ""),
                    "location": item.get("location", ""),
                    "start": start_info.get("dateTime", start_info.get("date", "")),
                    "end": end_info.get("dateTime", end_info.get("date", "")),
                    "status": item.get("status", ""),
                    "attendees": [
                        a.get("email", "")
                        for a in item.get("attendees", [])
                    ] if item.get("attendees") else [],
                    "html_link": item.get("htmlLink", ""),
                })

            return {"events": events, "total": len(events)}

        elif active_provider == "microsoft":
            if not active_token:
                return {
                    "error": "Microsoft Calendar access token not available. Reconnect your account.",
                    "events": [],
                    "total": 0,
                }

            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    response = await client.get(
                        f"{GRAPH_API_BASE}/calendarView",
                        params={
                            "startDateTime": time_min,
                            "endDateTime": time_max,
                            "$top": min(max_results, 250),
                            "$orderby": "start/dateTime",
                        },
                        headers={
                            "Authorization": f"Bearer {active_token}",
                            "Prefer": f"outlook.timezone=\"{timezone_str}\"",
                        },
                    )

                    if response.status_code == 401:
                        return {"error": "Microsoft token expired. Reconnect your account.", "events": [], "total": 0}

                    response.raise_for_status()
                    data = response.json()

            except Exception as exc:
                logger.error("Microsoft Graph Calendar API failed: %s", exc)
                return {"error": f"Calendar API request failed: {str(exc)}", "events": [], "total": 0}

            items = data.get("value", [])
            events = []
            for item in items:
                start_info = item.get("start", {})
                end_info = item.get("end", {})

                events.append({
                    "id": item.get("id", ""),
                    "summary": item.get("subject", "Untitled"),
                    "description": item.get("bodyPreview", ""),
                    "location": item.get("location", {}).get("displayName", ""),
                    "start": start_info.get("dateTime", start_info.get("date", "")),
                    "end": end_info.get("dateTime", end_info.get("date", "")),
                    "status": item.get("showAs", ""),
                    "attendees": [
                        a.get("emailAddress", {}).get("address", "")
                        for a in item.get("attendees", [])
                    ] if item.get("attendees") else [],
                    "html_link": "",
                })

            return {"events": events, "total": len(events)}

        else:
            return {"error": f"Calendar provider '{active_provider}' is not supported yet", "events": [], "total": 0}

    # ------------------------------------------------------------------
    @tool
    async def create_event(
        summary: str,
        start: str,
        end: str,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
    ) -> dict:
        """Create a new calendar event.

        Args:
            summary: Event title/summary.
            start: Start date/time in ISO format (e.g., "2024-01-15T14:00:00").
            end: End date/time in ISO format.
            description: Optional event description.
            location: Optional event location.
            attendees: Optional list of attendee email addresses.

        Returns:
            A dict with:
            - ``id``: the created event ID
            - ``summary``: event summary
            - ``start``: start time
            - ``end``: end time
            - ``html_link``: link to view the event in calendar
            - ``error``: error message if creation failed
        """
        if not summary or not summary.strip():
            return {"error": "No event summary provided"}
        if not start:
            return {"error": "No start time provided"}
        if not end:
            return {"error": "No end time provided"}

        start_iso = _parse_datetime(start)
        end_iso = _parse_datetime(end)

        # Determine which provider to use: user OAuth credentials take priority
        active_provider = provider
        active_token = None
        active_calendar_id = calendar_id

        if user_creds_map:
            up = user_creds_map.get("provider", "")
            if up in ("outlook", "microsoft"):
                active_provider = "microsoft"
                active_token = user_creds_map.get("access_token", "")
            elif up in ("gmail", "google"):
                active_provider = "google"
                active_token = user_creds_map.get("access_token", "")
                active_calendar_id = user_creds_map.get("calendar_id", "primary")

        if active_provider == "google":
            if not active_token:
                active_token = await _get_google_access_token(credentials)
            if not active_token or len(active_token) <= 50:
                return {
                    "error": (
                        "Calendar event creation requires OAuth or service account "
                        "credentials (API key is read-only)."
                    ),
                }

            headers = {
                "Authorization": f"Bearer {active_token}",
                "Content-Type": "application/json",
            }

            event_data = {
                "summary": summary.strip(),
                "start": {
                    "dateTime": start_iso,
                    "timeZone": timezone_str,
                },
                "end": {
                    "dateTime": end_iso,
                    "timeZone": timezone_str,
                },
            }
            if description:
                event_data["description"] = description
            if location:
                event_data["location"] = location
            if attendees:
                event_data["attendees"] = [{"email": a.strip()} for a in attendees]

            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    response = await client.post(
                        f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(active_calendar_id)}/events",
                        json=event_data,
                        headers=headers,
                    )

                    if response.status_code == 401:
                        return {"error": "Calendar authentication failed. Check credentials."}
                    elif response.status_code == 403:
                        return {"error": "Permission denied. The credentials may be read-only."}

                    response.raise_for_status()
                    data = response.json()

            except Exception as exc:
                logger.error("Failed to create calendar event: %s", exc)
                return {"error": f"Failed to create event: {str(exc)}"}

            return {
                "id": data.get("id", ""),
                "summary": data.get("summary", summary),
                "start": data.get("start", {}).get("dateTime", start_iso),
                "end": data.get("end", {}).get("dateTime", end_iso),
                "html_link": data.get("htmlLink", ""),
                "status": data.get("status", "confirmed"),
            }

        elif active_provider == "microsoft":
            if not active_token:
                return {"error": "Microsoft Calendar access token not available. Reconnect your account."}

            event_data = {
                "subject": summary.strip(),
                "start": {
                    "dateTime": start_iso,
                    "timeZone": timezone_str,
                },
                "end": {
                    "dateTime": end_iso,
                    "timeZone": timezone_str,
                },
            }
            if description:
                event_data["body"] = {"contentType": "text", "content": description}
            if location:
                event_data["location"] = {"displayName": location}
            if attendees:
                event_data["attendees"] = [
                    {"emailAddress": {"address": a.strip()}} for a in attendees
                ]

            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    response = await client.post(
                        f"{GRAPH_API_BASE}/events",
                        json=event_data,
                        headers={
                            "Authorization": f"Bearer {active_token}",
                            "Content-Type": "application/json",
                        },
                    )

                    if response.status_code == 401:
                        return {"error": "Microsoft token expired. Reconnect your account."}

                    response.raise_for_status()
                    data = response.json()

            except Exception as exc:
                logger.error("Failed to create Microsoft calendar event: %s", exc)
                return {"error": f"Failed to create event: {str(exc)}"}

            return {
                "id": data.get("id", ""),
                "summary": data.get("subject", summary),
                "start": data.get("start", {}).get("dateTime", start_iso),
                "end": data.get("end", {}).get("dateTime", end_iso),
                "html_link": "",
                "status": "confirmed",
            }

        else:
            return {"error": f"Calendar provider '{active_provider}' is not supported yet"}

    # ------------------------------------------------------------------
    @tool
    async def find_free_slots(date: str, duration_minutes: int = 60) -> dict:
        """Find free time slots on a given date.

        Looks at the day's events and returns gaps between them that are
        at least the requested duration.

        Args:
            date: The date to check (e.g., "2024-01-15").
            duration_minutes: Minimum duration in minutes for a free slot (default 60).

        Returns:
            A dict with:
            - ``date``: the date checked
            - ``free_slots``: list of free time ranges (start, end, duration_minutes)
            - ``error``: error message if failed
        """
        if not date:
            return {"error": "No date provided", "free_slots": [], "date": ""}

        # Set time range for the full day
        try:
            dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return {"error": f"Invalid date format: {date}", "free_slots": [], "date": date}

        day_start = dt.replace(hour=0, minute=0, second=0).isoformat()
        day_end = dt.replace(hour=23, minute=59, second=59).isoformat()

        # Get events for the day
        events_result = await list_events(day_start, day_end, max_results=100)
        if events_result.get("error"):
            # If not configured, show the whole day as free
            return {
                "date": date,
                "free_slots": [{
                    "start": day_start,
                    "end": day_end,
                    "duration_minutes": 24 * 60,
                }],
                "message": "Calendar not configured; assuming full day is free.",
            }

        events = events_result.get("events", [])

        # Sort events by start time
        events.sort(key=lambda e: e.get("start", ""))

        # Business hours (default 8 AM - 6 PM)
        work_start = dt.replace(hour=8, minute=0, second=0)
        work_end = dt.replace(hour=18, minute=0, second=0)

        free_slots = []
        current = work_start

        for event in events:
            event_start_str = event.get("start", "")
            event_end_str = event.get("end", "")

            try:
                event_start = datetime.fromisoformat(event_start_str)
                event_end = datetime.fromisoformat(event_end_str)
            except ValueError:
                continue

            # Gap before this event
            if event_start > current:
                gap_minutes = (event_start - current).total_seconds() / 60
                if gap_minutes >= duration_minutes:
                    free_slots.append({
                        "start": current.isoformat(),
                        "end": event_start.isoformat(),
                        "duration_minutes": int(gap_minutes),
                    })

            current = max(current, event_end)

        # Gap after last event
        if work_end > current:
            gap_minutes = (work_end - current).total_seconds() / 60
            if gap_minutes >= duration_minutes:
                free_slots.append({
                    "start": current.isoformat(),
                    "end": work_end.isoformat(),
                    "duration_minutes": int(gap_minutes),
                })

        return {
            "date": date,
            "free_slots": free_slots,
            "total_free_slots": len(free_slots),
            "business_hours": f"{work_start.strftime('%H:%M')} - {work_end.strftime('%H:%M')}",
        }

    # ------------------------------------------------------------------
    @tool
    async def delete_event(
        event_id: str,
        calendar_label: str | None = None,
    ) -> dict:
        """Delete an event from the calendar.

        The event ID comes from ``list_events`` results.

        Args:
            event_id: The unique ID of the event to delete.
            calendar_label: Connected account label (optional if only one).

        Returns:
            Dict with ``status`` and optionally ``error``.
        """
        if not event_id:
            return {"error": "No event ID provided", "status": "error"}

        # Determine which provider to use: user OAuth credentials take priority
        active_provider = provider
        active_token = None
        active_calendar_id = calendar_id

        if user_creds_map:
            up = user_creds_map.get("provider", "")
            if up in ("outlook", "microsoft"):
                active_provider = "microsoft"
                active_token = user_creds_map.get("access_token", "")
            elif up in ("gmail", "google"):
                active_provider = "google"
                active_token = user_creds_map.get("access_token", "")
                active_calendar_id = user_creds_map.get("calendar_id", "primary")

        if active_provider == "google":
            if not active_token:
                active_token = await _get_google_access_token(credentials)
            if not active_token or len(active_token) <= 50:
                return {"error": "Event deletion requires OAuth credentials (API key is read-only).", "status": "error"}

            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    response = await client.delete(
                        f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(active_calendar_id)}/events/{quote(event_id)}",
                        headers={"Authorization": f"Bearer {active_token}"},
                    )
                    if response.status_code == 401:
                        return {"error": "Calendar auth failed. Reconnect account.", "status": "error"}
                    if response.status_code == 404:
                        return {"error": "Event not found.", "status": "error"}
                    if response.status_code == 204:
                        return {"status": "ok", "message": "Event deleted."}
                    response.raise_for_status()
                    return {"status": "ok", "message": "Event deleted."}
            except Exception as exc:
                logger.error("Failed to delete Google Calendar event: %s", exc)
                return {"error": f"Failed to delete event: {str(exc)}", "status": "error"}

        elif active_provider == "microsoft":
            if not active_token:
                return {"error": "Microsoft Calendar access token not available. Reconnect your account.", "status": "error"}

            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    response = await client.delete(
                        f"{GRAPH_API_BASE}/events/{event_id}",
                        headers={"Authorization": f"Bearer {active_token}"},
                    )
                    if response.status_code == 401:
                        return {"error": "Microsoft token expired. Reconnect your account.", "status": "error"}
                    if response.status_code == 404:
                        return {"error": "Event not found.", "status": "error"}
                    if response.status_code == 204:
                        return {"status": "ok", "message": "Event deleted."}
                    response.raise_for_status()
                    return {"status": "ok", "message": "Event deleted."}
            except Exception as exc:
                logger.error("Failed to delete Microsoft Calendar event: %s", exc)
                return {"error": f"Failed to delete event: {str(exc)}", "status": "error"}

        else:
            return {"error": f"Calendar provider '{active_provider}' does not support deletion.", "status": "error"}

    # ------------------------------------------------------------------
    @tool
    async def update_event(
        event_id: str,
        summary: str | None = None,
        start: str | None = None,
        end: str | None = None,
        description: str | None = None,
        location: str | None = None,
        calendar_label: str | None = None,
    ) -> dict:
        """Update an existing calendar event (title, time, description, location).

        The event ID comes from ``list_events`` results.

        Args:
            event_id: The unique ID of the event to update.
            summary: New title (omit to keep current).
            start: New start date/time in ISO format.
            end: New end date/time in ISO format.
            description: New description.
            location: New location.
            calendar_label: Connected account label (optional if only one).

        Returns:
            Dict with ``status`` and optionally ``error``.
        """
        if not event_id:
            return {"error": "No event ID provided", "status": "error"}

        # Determine which provider to use: user OAuth credentials take priority
        active_provider = provider
        active_token = None
        active_calendar_id = calendar_id

        if user_creds_map:
            up = user_creds_map.get("provider", "")
            if up in ("outlook", "microsoft"):
                active_provider = "microsoft"
                active_token = user_creds_map.get("access_token", "")
            elif up in ("gmail", "google"):
                active_provider = "google"
                active_token = user_creds_map.get("access_token", "")
                active_calendar_id = user_creds_map.get("calendar_id", "primary")

        # Build the update body with only the provided fields
        update_body = {}

        if active_provider == "google":
            if not active_token:
                active_token = await _get_google_access_token(credentials)
            if not active_token or len(active_token) <= 50:
                return {"error": "Event update requires OAuth credentials (API key is read-only).", "status": "error"}

            if summary is not None:
                update_body["summary"] = summary
            if start is not None:
                update_body["start"] = {"dateTime": _parse_datetime(start), "timeZone": timezone_str}
            if end is not None:
                update_body["end"] = {"dateTime": _parse_datetime(end), "timeZone": timezone_str}
            if description is not None:
                update_body["description"] = description
            if location is not None:
                update_body["location"] = location

            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    response = await client.patch(
                        f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(active_calendar_id)}/events/{quote(event_id)}",
                        json=update_body,
                        headers={"Authorization": f"Bearer {active_token}", "Content-Type": "application/json"},
                    )
                    if response.status_code == 401:
                        return {"error": "Calendar auth failed. Reconnect account.", "status": "error"}
                    if response.status_code == 404:
                        return {"error": "Event not found.", "status": "error"}
                    if response.status_code == 200:
                        return {"status": "ok", "message": "Event updated."}
                    response.raise_for_status()
                    return {"status": "ok", "message": "Event updated."}
            except Exception as exc:
                logger.error("Failed to update Google Calendar event: %s", exc)
                return {"error": f"Failed to update event: {str(exc)}", "status": "error"}

        elif active_provider == "microsoft":
            if not active_token:
                return {"error": "Microsoft Calendar access token not available. Reconnect your account.", "status": "error"}

            if summary is not None:
                update_body["subject"] = summary
            if start is not None:
                update_body["start"] = {"dateTime": _parse_datetime(start), "timeZone": timezone_str}
            if end is not None:
                update_body["end"] = {"dateTime": _parse_datetime(end), "timeZone": timezone_str}
            if description is not None:
                update_body["body"] = {"contentType": "text", "content": description}
            if location is not None:
                update_body["location"] = {"displayName": location}

            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    response = await client.patch(
                        f"{GRAPH_API_BASE}/events/{event_id}",
                        json=update_body,
                        headers={"Authorization": f"Bearer {active_token}", "Content-Type": "application/json"},
                    )
                    if response.status_code == 401:
                        return {"error": "Microsoft token expired. Reconnect your account.", "status": "error"}
                    if response.status_code == 404:
                        return {"error": "Event not found.", "status": "error"}
                    if response.status_code == 200:
                        return {"status": "ok", "message": "Event updated."}
                    response.raise_for_status()
                    return {"status": "ok", "message": "Event updated."}
            except Exception as exc:
                logger.error("Failed to update Microsoft Calendar event: %s", exc)
                return {"error": f"Failed to update event: {str(exc)}", "status": "error"}

        else:
            return {"error": f"Calendar provider '{active_provider}' does not support updates.", "status": "error"}

    # ------------------------------------------------------------------
    @tool
    async def list_calendar_accounts() -> dict:
        """List connected calendar accounts available to the agent.

        Returns:
            Dict with ``accounts`` (list) and ``total``.
        """
        if not user_credentials:
            return {"accounts": [], "message": "No calendar accounts connected."}

        accounts = [
            {"label": c.label, "email": c.email_address or "", "provider": c.provider,
             "is_default": c.is_default, "status": c.status}
            for c in user_credentials if c.status == "active"
        ]
        return {"accounts": accounts, "total": len(accounts)}

    tools = [list_events, create_event, find_free_slots, delete_event, update_event]
    if user_credentials:
        tools.append(list_calendar_accounts)
    return tools


# ---------------------------------------------------------------------------
# Credential parsing helper (shared with email tool pattern)
# ---------------------------------------------------------------------------

def _parse_credential(cred) -> tuple[str, dict, dict, str | None]:
    """Extract (provider, creds_dict, tokens_dict, email) from a credential ORM row."""
    import json
    provider = cred.provider if hasattr(cred, "provider") else ""
    creds_raw = getattr(cred, "credentials", None)
    tokens_raw = getattr(cred, "oauth_tokens", None)
    email = getattr(cred, "email_address", None)
    return provider, json.loads(creds_raw) if creds_raw else {}, json.loads(tokens_raw) if tokens_raw else {}, email
