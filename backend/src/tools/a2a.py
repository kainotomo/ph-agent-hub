# =============================================================================
# PH Agent Hub — A2A Tool Callable Builder
# =============================================================================
# Given a Tool ORM record with type="a2a", resolves the associated A2A
# server config from the a2a_servers table and returns MAF tool callables
# that delegate to the remote A2A agent's skills.
# =============================================================================

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.encryption import decrypt
from ..core.exceptions import NotFoundError
from ..db.orm.a2a_servers import A2aServer
from ..db.orm.tools import Tool

logger = logging.getLogger(__name__)


async def build_a2a_tool_callables(
    db: AsyncSession,
    tool: Tool,
    tenant_id: str,
    session_id: str = "",
    cleanup_clients: list | None = None,
) -> list:
    """Build MAF callables for an A2A tool.

    Given a Tool record with type="a2a", loads the associated A2aServer
    config, creates an a2a.Client, and returns a list with a single
    wrapper FunctionTool that delegates to the remote agent's skill.

    The caller (runner.py) is responsible for calling .close() on any
    clients in cleanup_clients after the agent run.
    """
    if cleanup_clients is None:
        cleanup_clients = []

    config = tool.config or {}
    a2a_server_id = config.get("a2a_server_id")
    skill_id = config.get("skill_id")
    skill_name = config.get("skill_name", tool.name)
    skill_description = config.get("skill_description", "")
    input_modes = config.get("input_modes", [])
    output_modes = config.get("output_modes", [])
    examples = config.get("examples", [])
    tags = config.get("tags", [])

    if not a2a_server_id or not skill_id:
        logger.warning("A2A tool %s has missing a2a_server_id or skill_id", tool.id)
        return []

    # Load the A2aServer record
    result = await db.execute(
        select(A2aServer).where(A2aServer.id == a2a_server_id)
    )
    server = result.scalar_one_or_none()
    if server is None:
        logger.warning("A2A server %s not found for tool %s", a2a_server_id, tool.id)
        return []

    if not server.enabled:
        logger.info("A2A server %s is disabled, skipping tool %s", server.name, tool.id)
        return []

    # Build the tool callable
    callable_fn = await _make_a2a_skill_callable(
        server=server,
        skill_id=skill_id,
        skill_name=skill_name,
        skill_description=skill_description,
        input_modes=input_modes,
        output_modes=output_modes,
        examples=examples,
        tags=tags,
        cleanup_clients=cleanup_clients,
        db=db,
        session_id=session_id,
        tenant_id=tenant_id,
    )

    return [callable_fn]


async def _make_a2a_skill_callable(
    server: A2aServer,
    skill_id: str,
    skill_name: str,
    skill_description: str,
    input_modes: list[str],
    output_modes: list[str],
    examples: list[str],
    tags: list[str],
    cleanup_clients: list,
    db: AsyncSession | None = None,
    session_id: str = "",
    tenant_id: str = "",
):
    """Create a FunctionTool-like callable that delegates to a remote A2A skill.

    Returns a MAF-compatible function decorated with @tool that, when invoked,
    sends a message to the remote A2A agent via the a2a-sdk client.  The
    function supports all four A2A Part types (text, data, url, raw) and
    negotiates media types via ``SendMessageConfiguration.acceptedOutputModes``.
    """
    from agent_framework import tool

    # Resolve default I/O modes from the cached Agent Card (fallback to skill-level)
    cached_card = server.agent_card_cache or {}
    agent_default_inputs: list[str] = cached_card.get("defaultInputModes", [])
    agent_default_outputs: list[str] = cached_card.get("defaultOutputModes", [])

    effective_input_modes = input_modes if input_modes else agent_default_inputs
    effective_output_modes = output_modes if output_modes else agent_default_outputs

    # Build a rich docstring for the LLM
    doc_parts: list[str] = [skill_description or f"Call the remote A2A agent skill: {skill_name}"]

    if effective_input_modes:
        if "application/json" in effective_input_modes:
            doc_parts.append(
                "\nInput format: This skill accepts JSON. "
                "Pass your request as a JSON string and it will be sent as structured data."
            )
        else:
            doc_parts.append(
                f"\nInput format(s): {', '.join(effective_input_modes)}"
            )

    if effective_output_modes:
        doc_parts.append(
            f"Output format(s): {', '.join(effective_output_modes)}"
        )

    if tags:
        doc_parts.append(f"Tags: {', '.join(tags)}")

    if examples:
        doc_parts.append("Examples:")
        for i, ex in enumerate(examples, 1):
            doc_parts.append(f"  {i}. {ex}")

    rich_docstring = "\n".join(doc_parts)

    # Store the A2A client on the cleanup_clients list so the runner
    # can close it after the agent run
    a2a_client_ref = {"client": None}

    @tool
    async def a2a_skill_callable(query: str) -> str:
        """DOCSTRING_PLACEHOLDER"""
        nonlocal a2a_client_ref

        try:
            import uuid
            import httpx
            from a2a.client import (
                ClientFactory,
                ClientConfig,
            )
            from a2a.types import (
                SendMessageRequest,
                SendMessageConfiguration,
                Message as A2AMessage,
                Part,
                Role,
            )

            # Decrypt auth
            headers = {}
            auth_token = None
            if server.headers:
                try:
                    headers = json.loads(decrypt(server.headers))
                except Exception:
                    logger.warning(
                        "Failed to decrypt headers for A2A server %s", server.id
                    )
            if server.auth_token:
                try:
                    auth_token = decrypt(server.auth_token)
                except Exception:
                    logger.warning(
                        "Failed to decrypt auth_token for A2A server %s", server.id
                    )
                if auth_token and server.auth_scheme == "bearer":
                    headers.setdefault("Authorization", f"Bearer {auth_token}")
                elif auth_token and server.auth_scheme == "api_key":
                    headers.setdefault("Authorization", f"Bearer {auth_token}")

            # Create or reuse the A2A client
            if a2a_client_ref["client"] is None:
                httpx_client = httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=httpx.Timeout(30.0, read=300.0),
                    headers=headers,
                )
                cleanup_clients.append(httpx_client)

                config = ClientConfig(
                    streaming=False,
                    polling=True,
                    httpx_client=httpx_client,
                    supported_protocol_bindings=[server.protocol_binding],
                )
                factory = ClientFactory(config)
                client = await factory.create_from_url(
                    url=server.url.rstrip("/"),
                    relative_card_path=server.agent_card_path,
                    resolver_http_kwargs={"headers": headers},
                )
                a2a_client_ref["client"] = client

            client = a2a_client_ref["client"]

            # ---- Build the outgoing Part ---------------------------------
            part = Part()
            # If the skill declares JSON input and the query looks like JSON,
            # send as a structured data part.
            json_input_modes = {"application/json"}
            if effective_input_modes and set(effective_input_modes) & json_input_modes:
                try:
                    parsed = json.loads(query)
                    if isinstance(parsed, (dict, list)):
                        from google.protobuf.struct_pb2 import Struct
                        # Use protobuf Struct for JSON data
                        s = Struct()
                        s.update(parsed)
                        part.data.CopyFrom(s)
                        part.media_type = "application/json"
                    else:
                        part.text = query
                except (json.JSONDecodeError, Exception):
                    # Not valid JSON — fall back to text
                    part.text = query
            else:
                part.text = query

            # ---- Build the SendMessageRequest -----------------------------
            msg = A2AMessage()
            msg.message_id = str(uuid.uuid4())
            msg.role = Role.ROLE_USER
            msg.parts.append(part)
            request = SendMessageRequest()
            request.message.CopyFrom(msg)

            # ---- Media type negotiation -----------------------------------
            if effective_output_modes:
                config_block = SendMessageConfiguration()
                config_block.accepted_output_modes.extend(effective_output_modes)
                request.configuration.CopyFrom(config_block)

            # ---- Send with resilience wrapper (Issue #409) ----------------
            from ...services.a2a_client import send_message_resilient
            from ...core.exceptions import ServiceUnavailableError

            try:
                result_parts, log_info = await send_message_resilient(
                    server=server,
                    a2a_client=client,
                    send_message_request=request,
                    skill_id=skill_id,
                    skill_name=skill_name,
                    session_id=session_id,
                    tenant_id=tenant_id,
                    db=db,
                )
            except ServiceUnavailableError as exc:
                logger.warning(
                    "A2A circuit breaker blocked call: server=%s skill=%s "
                    "error=%s",
                    server.name, skill_id, exc,
                )
                return f"[A2A skill '{skill_name}' is currently unavailable: {exc.message}]"

            if not result_parts:
                return f"[A2A skill '{skill_name}' returned no response]"

            return "\n".join(result_parts)

        except Exception as exc:
            logger.error(
                "A2A skill call failed: server=%s skill=%s error=%s",
                server.name, skill_id, exc,
            )
            return (
                f"[A2A skill '{skill_name}' encountered an error: {exc}]"
            )

    # Set metadata for the skill
    a2a_skill_callable.__name__ = f"a2a_{skill_id.replace('-', '_')}"
    a2a_skill_callable.__doc__ = rich_docstring

    return a2a_skill_callable


# ---------------------------------------------------------------------------
# Response Part formatting helpers
# ---------------------------------------------------------------------------


def _format_response_part(r_part) -> str | None:
    """Format a single A2A ``Part`` into a human-readable string.

    Handles all four Part oneof types defined in A2A spec Section 4.1.6:
    ``text``, ``data``, ``url``, and ``raw``.
    """
    # Check which oneof field is set.
    # Prefer the protobuf-generated WhichOneof method when available and
    # returning a real string (not a MagicMock fallback).
    kind: str | None = None
    if hasattr(r_part, "WhichOneof") and callable(r_part.WhichOneof):
        try:
            raw_kind = r_part.WhichOneof("content")
            if isinstance(raw_kind, str) and raw_kind:
                kind = raw_kind
        except Exception:
            pass

    # Fallback: inspect content attributes directly
    if not kind:
        filename = getattr(r_part, "filename", "") or ""
        media_type = getattr(r_part, "media_type", "") or ""
        for candidate in ("text", "data", "url", "raw"):
            val = getattr(r_part, candidate, None)
            if val is not None and val != "" and val != b"":
                kind = candidate
                break

    filename = getattr(r_part, "filename", "") or ""
    media_type = getattr(r_part, "media_type", "") or ""

    if kind == "text" or (kind is None and hasattr(r_part, "text") and r_part.text):
        text_val = r_part.text
        if text_val and isinstance(text_val, str):
            return text_val

    elif kind == "data" or (kind is None and hasattr(r_part, "data") and r_part.data is not None):
        data_val = r_part.data
        try:
            # protobuf Struct / Value — use MessageToDict
            from google.protobuf.json_format import MessageToDict
            data_dict = MessageToDict(data_val)
            data_str = json.dumps(data_dict, ensure_ascii=False, indent=2)
        except Exception:
            # Fallback: plain dict or other JSON-serializable
            if isinstance(data_val, (dict, list)):
                data_str = json.dumps(data_val, ensure_ascii=False, indent=2)
            else:
                data_str = str(data_val)
        label = f"[data: {media_type}]" if media_type else "[data]"
        return f"{label}\n{data_str}"

    elif kind == "url" or (kind is None and hasattr(r_part, "url") and r_part.url):
        url_val = r_part.url
        if url_val and isinstance(url_val, str):
            label = f"[file: {filename}]" if filename else "[url]"
            return f"{label} {url_val}"

    elif kind == "raw" or (kind is None and hasattr(r_part, "raw") and r_part.raw):
        raw_bytes = r_part.raw
        size = len(raw_bytes) if isinstance(raw_bytes, (bytes, bytearray)) else 0
        label = f"[binary: {filename}]" if filename else "[binary]"
        detail = f" ({media_type}, {size} bytes)" if media_type else f" ({size} bytes)"
        return f"{label}{detail}"

    return None
