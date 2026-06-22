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
        cleanup_clients=cleanup_clients,
    )

    return [callable_fn]


async def _make_a2a_skill_callable(
    server: A2aServer,
    skill_id: str,
    skill_name: str,
    skill_description: str,
    cleanup_clients: list,
):
    """Create a FunctionTool-like callable that delegates to a remote A2A skill.

    Returns a MAF-compatible function decorated with @tool that, when invoked,
    sends a message to the remote A2A agent via the a2a-sdk client.
    """
    from agent_framework import tool

    # Store the A2A client on the cleanup_clients list so the runner
    # can close it after the agent run
    a2a_client_ref = {"client": None}

    @tool
    async def a2a_skill_callable(query: str) -> str:
        """Call a remote A2A agent skill.

        Args:
            query: The text query to send to the remote agent.

        Returns:
            The agent's response text.
        """
        nonlocal a2a_client_ref

        try:
            import httpx
            from a2a.client import (
                ClientFactory,
                ClientConfig,
            )
            from a2a.types import (
                SendMessageRequest,
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

            # Build a SendMessageRequest
            import uuid
            msg = A2AMessage()
            msg.message_id = str(uuid.uuid4())
            msg.role = Role.ROLE_USER
            part = Part()
            part.text = query
            msg.parts.append(part)
            request = SendMessageRequest()
            request.message.CopyFrom(msg)

            # Send and collect response
            result_text_parts = []
            async for stream_response in client.send_message(request):
                if stream_response.HasField("task"):
                    task = stream_response.task
                    if task.artifacts:
                        for artifact in task.artifacts:
                            for part in artifact.parts:
                                if part.text:
                                    result_text_parts.append(part.text)
                elif stream_response.HasField("message"):
                    msg_resp = stream_response.message
                    for part in msg_resp.parts:
                        if part.text:
                            result_text_parts.append(part.text)

            if not result_text_parts:
                return f"[A2A skill '{skill_name}' returned no text response]"

            return "\n".join(result_text_parts)

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
    a2a_skill_callable.__doc__ = (
        skill_description or f"Call the remote A2A agent skill: {skill_name}"
    )

    return a2a_skill_callable
