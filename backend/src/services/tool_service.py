# =============================================================================
# PH Agent Hub — Tool Service (CRUD)
# =============================================================================

from sqlalchemy import select, or_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import NotFoundError, ValidationError
from ..agents.workflows.roles import MODEL_ROLES, is_role_reference
from ..db.orm.groups import ToolGroup, UserGroupMember
from ..db.orm.tools import Tool
from ..db.orm.skills import SkillAllowedTool
from ..db.orm.sessions import SessionActiveTool
from ..db.orm.user_tool_preferences import UserToolPreference

VALID_TOOL_TYPES = {
    "erpnext", "membrane", "custom", "datetime", "web_search",
    "fetch_url", "weather", "calculator", "wikipedia", "rss_feed",
    "currency_exchange", "market_overview", "etf_data", "stock_data",
    "stock_screener", "portfolio", "sec_filings", "pdf_extractor",
    "code_interpreter", "sql_query", "document_generation", "browser",
    "rag_search", "github", "calendar", "image_generation",
    "slack", "email", "mcp", "tasks", "a2a", "subagent",
    "file_list", "memory",
}

TOOL_TYPE_TO_CATEGORY = {
    "currency_exchange": "financial",
    "market_overview": "financial",
    "etf_data": "financial",
    "stock_data": "financial",
    "stock_screener": "financial",
    "portfolio": "financial",
    "sec_filings": "financial",
    "pdf_extractor": "web",
    "web_search": "web",
    "fetch_url": "web",
    "rss_feed": "web",
    "wikipedia": "web",
    "erpnext": "enterprise",
    "membrane": "enterprise",
    "calculator": "utility",
    "datetime": "utility",
    "weather": "utility",
    "custom": "custom",
    "code_interpreter": "utility",
    "sql_query": "enterprise",
    "document_generation": "utility",
    "browser": "web",
    "rag_search": "web",
    "github": "devops",
    "calendar": "productivity",
    "tasks": "productivity",
    "image_generation": "creative",
    "slack": "communication",
    "email": "communication",
    "mcp": "mcp",
    "a2a": "communication",
    "subagent": "agents",
    "file_list": "system",
    "memory": "system",
}


def derive_tool_category(tool_type: str) -> str:
    """Map tool type to category. Unknown types fall back to general."""
    return TOOL_TYPE_TO_CATEGORY.get(tool_type, "general")


async def list_tools(
    db: AsyncSession,
    tenant_id: str | None = None,
    user_id: str | None = None,
    *,
    search: str | None = None,
    type: str | None = None,
    category: str | None = None,
    enabled: bool | None = None,
    is_public: bool | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    page_size: int = 25,
) -> tuple[list[Tool], int]:
    """Return tools with optional filtering, sorting, and pagination.

    When user_id is provided, only returns tools where:
    - is_public=True, OR
    - the tool is assigned to a group the user belongs to
    """
    stmt = select(Tool)
    if tenant_id is not None:
        stmt = stmt.where(Tool.tenant_id == tenant_id)

    if user_id is not None:
        # Subquery: tool IDs assigned to groups the user belongs to
        user_group_subq = (
            select(ToolGroup.tool_id)
            .join(UserGroupMember, UserGroupMember.group_id == ToolGroup.group_id)
            .where(UserGroupMember.user_id == user_id)
        )
        stmt = stmt.where(
            or_(
                Tool.is_public == True,  # noqa: E712
                Tool.id.in_(user_group_subq),
            )
        )

    if type is not None:
        stmt = stmt.where(Tool.type == type)
    if category is not None:
        stmt = stmt.where(Tool.category == category)
    if enabled is not None:
        stmt = stmt.where(Tool.enabled == enabled)
    if is_public is not None:
        stmt = stmt.where(Tool.is_public == is_public)

    from ..core.pagination import apply_search, apply_sorting, paginate
    stmt = apply_search(
        stmt, search,
        [Tool.name, Tool.type],
    )
    stmt = apply_sorting(
        stmt, sort_by, sort_dir,
        column_map={
            "name": Tool.name,
            "type": Tool.type,
            "category": Tool.category,
            "enabled": Tool.enabled,
            "created_at": Tool.created_at,
        },
        default_sort=Tool.created_at,
    )

    return await paginate(db, stmt, page=page, page_size=page_size)


def _validate_subagent_config(config: dict | None) -> None:
    """Validate the ``config`` payload of a ``subagent`` tool row.

    A subagent persona must declare the child's system prompt and the
    model role it runs on.  ``tool_deny`` is deliberately *not* validated
    against tool names: the child's tool set is the parent session's
    resolved pool minus this list, so denying an absent tool is a no-op.

    Raises:
        ValidationError: If the config is missing or malformed.
    """
    if not isinstance(config, dict):
        raise ValidationError(
            "Subagent tools require a config object with 'instructions' "
            "and 'model_role'."
        )

    instructions = config.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValidationError(
            "Subagent config requires a non-empty 'instructions' string."
        )

    model_role = config.get("model_role")
    if not isinstance(model_role, str) or not model_role:
        raise ValidationError(
            "Subagent config requires a non-empty 'model_role' string."
        )
    if not is_role_reference(model_role) or model_role not in MODEL_ROLES:
        raise ValidationError(
            f"Subagent 'model_role' must be one of the declared model roles "
            f"({', '.join(sorted(MODEL_ROLES))}); got '{model_role}'."
        )

    tool_deny = config.get("tool_deny")
    if tool_deny is not None:
        if not isinstance(tool_deny, list) or not all(
            isinstance(item, str) and item for item in tool_deny
        ):
            raise ValidationError(
                "Subagent 'tool_deny' must be a list of non-empty strings."
            )

    temperature = config.get("temperature")
    if temperature is not None:
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not 0 <= float(temperature) <= 2
        ):
            raise ValidationError(
                "Subagent 'temperature' must be a number between 0 and 2."
            )

    timeout_s = config.get("timeout_s")
    if timeout_s is not None:
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, int)
            or timeout_s <= 0
        ):
            raise ValidationError(
                "Subagent 'timeout_s' must be a positive integer."
            )

    include_session_context = config.get("include_session_context")
    if include_session_context is not None and not isinstance(
        include_session_context, bool
    ):
        raise ValidationError(
            "Subagent 'include_session_context' must be a boolean."
        )


async def get_tool_by_id(db: AsyncSession, tool_id: str) -> Tool | None:
    """Look up a tool by primary key."""
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    return result.scalar_one_or_none()


async def create_tool(
    db: AsyncSession,
    tenant_id: str,
    name: str,
    type: str,
    config: dict | None = None,
    code: str | None = None,
    description: str | None = None,
    enabled: bool = True,
    is_public: bool = False,
    approval_required: bool = False,
) -> Tool:
    """Create a new tool. Raises ValidationError if type is invalid."""
    if type not in VALID_TOOL_TYPES:
        raise ValidationError(
            f"Invalid tool type '{type}'. "
            f"Must be one of: {', '.join(sorted(VALID_TOOL_TYPES))}"
        )

    if type == "subagent":
        _validate_subagent_config(config)

    tool = Tool(
        tenant_id=tenant_id,
        name=name,
        description=description,
        type=type,
        config=config,
        code=code,
        enabled=enabled,
        is_public=is_public,
        approval_required=approval_required,
        category=derive_tool_category(type),
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return tool


async def update_tool(db: AsyncSession, tool_id: str, **fields) -> Tool:
    """Update a tool's fields. Raises NotFoundError if missing,
    ValidationError if type is invalid."""
    tool = await get_tool_by_id(db, tool_id)
    if tool is None:
        raise NotFoundError("Tool not found")

    if "type" in fields and fields["type"] not in VALID_TOOL_TYPES:
        raise ValidationError(
            f"Invalid tool type '{fields['type']}'. "
            f"Must be one of: {', '.join(sorted(VALID_TOOL_TYPES))}"
        )

    effective_type = fields.get("type", tool.type)
    if effective_type == "subagent":
        _validate_subagent_config(fields.get("config", tool.config))

    # Category is system-derived from type and not user-editable.
    fields.pop("category", None)

    if "type" in fields:
        fields["category"] = derive_tool_category(fields["type"])

    for key, value in fields.items():
        if hasattr(tool, key):
            setattr(tool, key, value)

    await db.commit()
    await db.refresh(tool)
    return tool


async def delete_tool(db: AsyncSession, tool_id: str) -> None:
    """Delete a tool by ID. Raises NotFoundError if missing.
    Removes all references (groups, skills, sessions, user preferences) first."""
    tool = await get_tool_by_id(db, tool_id)
    if tool is None:
        raise NotFoundError("Tool not found")

    # Remove tool from any assigned groups
    await db.execute(
        delete(ToolGroup).where(ToolGroup.tool_id == tool_id)
    )

    # Remove tool from skill allowed tools
    await db.execute(
        delete(SkillAllowedTool).where(SkillAllowedTool.tool_id == tool_id)
    )

    # Remove tool from session active tools
    await db.execute(
        delete(SessionActiveTool).where(SessionActiveTool.tool_id == tool_id)
    )

    # Remove tool from user preferences
    await db.execute(
        delete(UserToolPreference).where(UserToolPreference.tool_id == tool_id)
    )

    await db.delete(tool)
    await db.commit()
