# =============================================================================
# PH Agent Hub — Idempotent Admin Seed Script
# =============================================================================
# Creates the first admin user and default tenant on first run.
# Safe to run multiple times — skips existing records.
#
# Usage (from /app inside Docker container):
#   python scripts/seed.py
#
# Environment variables (NOT via Settings — seed-only):
#   ADMIN_EMAIL          — default: admin@phagent.local
#   ADMIN_PASSWORD       — default: admin
#   DEFAULT_TENANT_NAME  — default: Default
# =============================================================================

import asyncio
import os
import sys

# Make src/ importable when running from /app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from src.core.security import hash_password
from src.db.base import AsyncSessionLocal
from src.db.orm.tenants import Tenant
from src.db.orm.tools import Tool
from src.db.orm.users import User

# ---------------------------------------------------------------------------
# Configuration (from environment, not Settings)
# ---------------------------------------------------------------------------
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@phagent.local")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
DEFAULT_TENANT_NAME = os.getenv("DEFAULT_TENANT_NAME", "Default")
SEED_ALLOW_WEAK_PASSWORD = os.getenv("SEED_ALLOW_WEAK_PASSWORD", "false").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> None:
    # Safety check: refuse weak passwords unless explicitly allowed
    if ADMIN_PASSWORD == "admin" and not SEED_ALLOW_WEAK_PASSWORD:
        print(
            "[seed] ERROR: ADMIN_PASSWORD is set to the default 'admin' and "
            "SEED_ALLOW_WEAK_PASSWORD is not enabled.\n"
            "       Set SEED_ALLOW_WEAK_PASSWORD=true to override (dev only), "
            "or set a strong ADMIN_PASSWORD.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    async with AsyncSessionLocal() as db:
        # 1. Ensure at least one tenant exists.
        #    Prefer a tenant matching DEFAULT_TENANT_NAME, but if none exists
        #    with that name, use the first available tenant. This prevents
        #    duplicate tenant creation when DEFAULT_TENANT_NAME changes between
        #    deployments or the old tenant was renamed/deleted.
        result = await db.execute(
            select(Tenant).where(Tenant.name == DEFAULT_TENANT_NAME)
        )
        tenant = result.scalars().first()

        if tenant is None:
            # No tenant with the configured name — check if ANY tenant exists
            result = await db.execute(select(Tenant).limit(1))
            existing = result.scalars().first()

            if existing is not None:
                tenant = existing
                print(
                    f"[seed] Using existing tenant: {tenant.name} (id={tenant.id}) "
                    f"(DEFAULT_TENANT_NAME='{DEFAULT_TENANT_NAME}' not found)"
                )
            else:
                tenant = Tenant(name=DEFAULT_TENANT_NAME)
                db.add(tenant)
                await db.flush()
                print(f"[seed] Created tenant: {DEFAULT_TENANT_NAME} (id={tenant.id})")
        else:
            print(f"[seed] Tenant already exists: {DEFAULT_TENANT_NAME} (id={tenant.id})")

        # 2. Ensure admin user exists
        result = await db.execute(
            select(User).where(User.email == ADMIN_EMAIL)
        )
        admin = result.scalars().first()

        if admin is None:
            admin = User(
                tenant_id=tenant.id,
                email=ADMIN_EMAIL,
                password_hash=hash_password(ADMIN_PASSWORD),
                display_name="Admin",
                role="admin",
                is_active=True,
            )
            db.add(admin)
            await db.flush()
            print(f"[seed] Created admin user: {ADMIN_EMAIL} (id={admin.id})")
        else:
            print(f"[seed] Admin user already exists: {ADMIN_EMAIL} (id={admin.id})")

        # 3. Ensure default datetime tool exists
        result = await db.execute(
            select(Tool).where(
                Tool.tenant_id == tenant.id,
                Tool.type == "datetime",
                Tool.name == "Current Time",
            )
        )
        datetime_tool = result.scalars().first()

        if datetime_tool is None:
            datetime_tool = Tool(
                tenant_id=tenant.id,
                name="Current Time",
                type="datetime",
                config={"default_timezone": "UTC"},
                enabled=True,
                is_public=True,
            )
            db.add(datetime_tool)
            await db.flush()
            print(f"[seed] Created datetime tool: Current Time (id={datetime_tool.id})")
        else:
            print(f"[seed] Datetime tool already exists: Current Time (id={datetime_tool.id})")

        # 4. Ensure default web search tool exists
        result = await db.execute(
            select(Tool).where(
                Tool.tenant_id == tenant.id,
                Tool.type == "web_search",
                Tool.name == "Web Search",
            )
        )
        web_search_tool = result.scalars().first()

        if web_search_tool is None:
            web_search_tool = Tool(
                tenant_id=tenant.id,
                name="Web Search",
                type="web_search",
                config={
                    "max_results": 10,
                    "region": "us-en",
                    "safesearch": "moderate",
                    "backend": "auto",
                },
                enabled=True,
                is_public=True,
            )
            db.add(web_search_tool)
            await db.flush()
            print(f"[seed] Created web_search tool: Web Search (id={web_search_tool.id})")
        else:
            print(f"[seed] Web Search tool already exists: Web Search (id={web_search_tool.id})")

        # 5. Ensure default fetch URL tool exists
        result = await db.execute(
            select(Tool).where(
                Tool.tenant_id == tenant.id,
                Tool.type == "fetch_url",
                Tool.name == "Fetch URL",
            )
        )
        fetch_url_tool = result.scalars().first()

        if fetch_url_tool is None:
            fetch_url_tool = Tool(
                tenant_id=tenant.id,
                name="Fetch URL",
                type="fetch_url",
                config={"timeout": 30, "max_content_length": 100000},
                enabled=True,
                is_public=True,
            )
            db.add(fetch_url_tool)
            await db.flush()
            print(f"[seed] Created fetch_url tool: Fetch URL (id={fetch_url_tool.id})")
        else:
            print(f"[seed] Fetch URL tool already exists: Fetch URL (id={fetch_url_tool.id})")

        # 6. Ensure default weather tool exists
        result = await db.execute(
            select(Tool).where(
                Tool.tenant_id == tenant.id,
                Tool.type == "weather",
                Tool.name == "Weather",
            )
        )
        weather_tool = result.scalars().first()

        if weather_tool is None:
            weather_tool = Tool(
                tenant_id=tenant.id,
                name="Weather",
                type="weather",
                config={},
                enabled=True,
                is_public=True,
            )
            db.add(weather_tool)
            await db.flush()
            print(f"[seed] Created weather tool: Weather (id={weather_tool.id})")
        else:
            print(f"[seed] Weather tool already exists: Weather (id={weather_tool.id})")

        # 7. Ensure default calculator tool exists
        result = await db.execute(
            select(Tool).where(
                Tool.tenant_id == tenant.id,
                Tool.type == "calculator",
                Tool.name == "Calculator",
            )
        )
        calculator_tool = result.scalars().first()

        if calculator_tool is None:
            calculator_tool = Tool(
                tenant_id=tenant.id,
                name="Calculator",
                type="calculator",
                config={},
                enabled=True,
                is_public=True,
            )
            db.add(calculator_tool)
            await db.flush()
            print(f"[seed] Created calculator tool: Calculator (id={calculator_tool.id})")
        else:
            print(f"[seed] Calculator tool already exists: Calculator (id={calculator_tool.id})")

        # 8. Ensure default wikipedia tool exists
        result = await db.execute(
            select(Tool).where(
                Tool.tenant_id == tenant.id,
                Tool.type == "wikipedia",
                Tool.name == "Wikipedia",
            )
        )
        wikipedia_tool = result.scalars().first()

        if wikipedia_tool is None:
            wikipedia_tool = Tool(
                tenant_id=tenant.id,
                name="Wikipedia",
                type="wikipedia",
                config={"language": "en", "max_results": 5, "max_extract_chars": 10000},
                enabled=True,
                is_public=True,
            )
            db.add(wikipedia_tool)
            await db.flush()
            print(f"[seed] Created wikipedia tool: Wikipedia (id={wikipedia_tool.id})")
        else:
            print(f"[seed] Wikipedia tool already exists: Wikipedia (id={wikipedia_tool.id})")

        # 9. Ensure default currency exchange tool exists
        result = await db.execute(
            select(Tool).where(
                Tool.tenant_id == tenant.id,
                Tool.type == "currency_exchange",
                Tool.name == "Currency Exchange",
            )
        )
        currency_tool = result.scalars().first()

        if currency_tool is None:
            currency_tool = Tool(
                tenant_id=tenant.id,
                name="Currency Exchange",
                type="currency_exchange",
                config={"base_currency": "EUR"},
                enabled=True,
                is_public=True,
            )
            db.add(currency_tool)
            await db.flush()
            print(f"[seed] Created currency_exchange tool: Currency Exchange (id={currency_tool.id})")
        else:
            print(f"[seed] Currency Exchange tool already exists: Currency Exchange (id={currency_tool.id})")

        # 10. Ensure email tool exists (for OAuth email connection)
        result = await db.execute(
            select(Tool).where(
                Tool.tenant_id == tenant.id,
                Tool.type == "email",
                Tool.name == "Email",
            )
        )
        email_tool = result.scalars().first()

        if email_tool is None:
            email_tool = Tool(
                tenant_id=tenant.id,
                name="Email",
                type="email",
                config={},
                enabled=True,
                is_public=True,
            )
            db.add(email_tool)
            await db.flush()
            print(f"[seed] Created email tool: Email (id={email_tool.id})")
        else:
            print(f"[seed] Email tool already exists: Email (id={email_tool.id})")

        # 11. Ensure calendar tool exists (for OAuth calendar connection)
        result = await db.execute(
            select(Tool).where(
                Tool.tenant_id == tenant.id,
                Tool.type == "calendar",
                Tool.name == "Calendar",
            )
        )
        calendar_tool = result.scalars().first()

        if calendar_tool is None:
            calendar_tool = Tool(
                tenant_id=tenant.id,
                name="Calendar",
                type="calendar",
                config={},
                enabled=True,
                is_public=True,
            )
            db.add(calendar_tool)
            await db.flush()
            print(f"[seed] Created calendar tool: Calendar (id={calendar_tool.id})")
        else:
            print(f"[seed] Calendar tool already exists: Calendar (id={calendar_tool.id})")

        # 12. Ensure tasks tool exists (for OAuth tasks connection)
        result = await db.execute(
            select(Tool).where(
                Tool.tenant_id == tenant.id,
                Tool.type == "tasks",
                Tool.name == "Tasks",
            )
        )
        tasks_tool = result.scalars().first()

        if tasks_tool is None:
            tasks_tool = Tool(
                tenant_id=tenant.id,
                name="Tasks",
                type="tasks",
                config={},
                enabled=True,
                is_public=True,
            )
            db.add(tasks_tool)
            await db.flush()
            print(f"[seed] Created tasks tool: Tasks (id={tasks_tool.id})")
        else:
            print(f"[seed] Tasks tool already exists: Tasks (id={tasks_tool.id})")

        await db.commit()

    print("[seed] Done.")


if __name__ == "__main__":
    asyncio.run(main())
