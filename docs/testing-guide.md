# Testing Guide — PH Agent Hub

This document describes how to run tests, understand the test suite structure, and contribute new tests.

---

## Quick Start

```bash
# From the infrastructure directory (Docker stack must be running)
cd infrastructure
docker compose exec backend pytest /app/tests/ -m "not e2e and not slow"
```

This runs all unit, integration, security, tenant-isolation, and regression tests without requiring a full Docker stack for E2E tests.

---

## Test Suite Overview

The project uses **pytest** for backend testing. Tests are organized by marker and type:

| Marker | Type | CI Tier | Description |
|--------|------|---------|-------------|
| `unit` | Unit | Tier 1 | Pure logic, no external services |
| `integration` | Integration | Tier 1 | Requires DB/Redis via `conftest.py` fixtures |
| `security` | Security | Tier 2 | Auth, RBAC, encryption, abuse scenarios |
| `tenant_isolation` | Isolation | Tier 2 | Cross-tenant data separation |
| `regression` | Regression | Tier 2 | Known bug patterns and fixed issues |
| `slow` | Slow | Manual | Tests taking > 5 seconds |
| `e2e` | E2E | Tier 3 | Full Docker Compose stack required |
| `live_api` | Live | Manual | Real external API calls (requires credentials) |

### CI Tier Strategy

1. **Tier 1** (every PR push): `pytest -m "unit or integration" --cov-fail-under=70`
2. **Tier 2** (every PR push): `pytest -m "security or tenant_isolation or regression" -v`
3. **Tier 3** (merge to main): `pytest -m "e2e"` with Docker Compose services

---

## Test File Index

### Integration Tests (`test_*.py`, run with `@pytest.mark.integration`)

| File | Tests | What It Covers |
|------|-------|----------------|
| `test_chat_api.py` | 27 | Session CRUD, message send, file upload, feedback, ownership, tenant isolation |
| `test_memory_api.py` | 15 | Memory CRUD, pagination, cross-user/tenant isolation |
| `test_credentials_api.py` | 15 | Credential CRUD, OAuth URLs, ownership, tenant isolation with tenant_id column |
| `test_upload_flow.py` | 11 | File type/size validation, temp session guard, ownership, DeepSeek+image rejection |
| `test_filename_sanitization.py` | 19 | Filename sanitization utilities — path traversal, special chars, Unicode, RFC 5987 encoding (unit) |
| `test_concurrency.py` | 8 | Stream cancellation, temp session TTL/races, rate limiter concurrency |
| `test_regression.py` | 10 | Prompt/credential/chat/memory tenant isolation, temp upload guard |
| `test_group_service.py` | 3 | Group CRUD idempotency (Issue #348) |

### Security & Isolation Tests

| File | Type | What It Covers |
|------|------|----------------|
| `test_auth.py` | unit | JWT creation, decoding, expiry, tampering |
| `test_auth_endpoints.py` | integration | Login, refresh, logout, /me endpoints |
| `test_rbac.py` | unit | require_admin and require_admin_or_manager guards |
| `test_tenant_isolation.py` | integration | Service-layer tenant isolation |
| `test_tenant_isolation_api.py` | integration | API-layer cross-tenant access prevention |
| `test_embed_isolation.py` | integration | Embed widget guest token tenant binding |
| `test_oauth.py` | unit | Google/Microsoft OAuth code exchange |
| `test_credential_security.py` | integration | Credential encryption and ownership |
| `test_rate_limiter.py` | integration | Rate limiting on login |
| `test_abuse_scenarios.py` | integration | Forged tokens, SQL injection, XSS |
| `test_rag_service.py` | unit+integration | RAG pipeline (chunking, similarity, indexing, search) |
| `test_demo.py` | integration | Demo tenant service, JWT creation |

### E2E Tests (require Docker stack: `docker compose up -d`)

| File | Tests | What It Covers |
|------|-------|----------------|
| `e2e_rag.py` | 1 | Full RAG pipeline E2E |
| `e2e_demo.py` | 6 | Demo tenant provisioning and token validation |
| `e2e_auto_routing.py` | 4 | Intelligent model routing |
| `e2e_auto_select_tools.py` | 1 | Auto tool selection |
| `e2e_user_journey.py` | 5 | Full user journey (login → session → chat → history) |
| `e2e_admin_flow.py` | 5 | Admin management flow (tenant → user → model → group) |

---

## Running Tests Locally

### Prerequisites

1. Docker Compose stack running: `docker compose up -d` from `infrastructure/`
2. Migrations applied (automatic in Docker startup)

### Run All Non-E2E Tests

```bash
docker compose exec backend pytest /app/tests/ -m "not e2e and not slow"
```

### Run Specific Test File

```bash
docker compose exec backend pytest /app/tests/test_chat_api.py -v
```

### Run Tests by Marker

```bash
# All security tests
docker compose exec backend pytest /app/tests/ -m security -v

# All tenant isolation tests
docker compose exec backend pytest /app/tests/ -m tenant_isolation -v

# Regression tests only
docker compose exec backend pytest /app/tests/ -m regression -v

# Concurrency tests (slow)
docker compose exec backend pytest /app/tests/ -m "integration and slow" -v
```

### Run E2E Tests

```bash
# E2E tests require the Docker stack to be running
docker compose exec backend pytest /app/tests/ -m e2e -v
```

### Run with Coverage

```bash
docker compose exec backend pytest /app/tests/ \
  -m "not e2e and not slow" \
  --cov=src \
  --cov-report=term \
  --cov-report=xml
```

---

## Environment Variables

| Variable | Required For | Default (CI) |
|----------|-------------|--------------|
| `DATABASE_URL` | All non-E2E tests | `mysql+aiomysql://root:root@127.0.0.1:3306/phagent_hub` |
| `REDIS_URL` | Redis-dependent tests | `redis://localhost:6379/0` |
| `JWT_SECRET` | All auth tests | `testonly` |
| `ENCRYPTION_KEY` | Encryption tests | Fernet-generated per CI run |
| `E2E_DATABASE_URL` | E2E tests | `mysql+aiomysql://phagent:pRep5v3Nzw_aMMV@mariadb:3306/phagent_hub` |

---

## Test Patterns

### HTTP Integration Test Pattern

```python
import httpx
import pytest
import pytest_asyncio

from src.main import app

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture
async def async_client(override_get_db) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestFeature:
    async def test_create(self, async_client, auth_headers, test_user):
        headers = auth_headers(test_user)
        resp = await async_client.post("/api/endpoint", json={"key": "val"}, headers=headers)
        assert resp.status_code == 201, resp.text
```

### Tenant Isolation Pattern

```python
async def test_cross_tenant_isolation(
    self, async_client, auth_headers, test_user, second_user
):
    """Verify tenant B cannot access tenant A's data."""
    # Create as tenant A
    headers_a = auth_headers(test_user)
    create_resp = await async_client.post("/api/resource", json={...}, headers=headers_a)
    resource_id = create_resp.json()["id"]

    # Access as tenant B → should fail
    headers_b = auth_headers(second_user)
    resp = await async_client.get(f"/api/resource/{resource_id}", headers=headers_b)
    assert resp.status_code in (403, 404)
```

### Mocking the Agent Runner

For chat API tests, mock `run_agent` to avoid calling real LLMs:

```python
from unittest.mock import patch

@patch("src.api.chat.run_agent")
async def test_send_message(self, mock_run_agent, ...):
    mock_run_agent.return_value = ("Response text", "message-uuid")
    resp = await async_client.post("/api/chat/session/{id}/message", json={...})
```

### E2E Test Pattern

```python
import pytest

pytestmark = [pytest.mark.e2e]

@pytest.mark.e2e
class TestFeatureE2E:
    async def test_feature(self, e2e_db_session):
        from src.db.base import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            # Test setup and assertions
            await db.rollback()
```

---

## Coverage Targets

| Scope | Target |
|-------|--------|
| `src/services/` + `src/api/` | ≥30% (growing) |
| Overall | Tracked but not gated |

Coverage is enforced in CI via `--cov-fail-under=30` on `src/services` and `src/api` for Tier 1.

---

## Adding New Tests

1. Choose the right file or create a new `test_*.py` file in `backend/tests/`
2. Add appropriate markers: `@pytest.mark.integration`, `@pytest.mark.security`, etc.
3. Use existing fixtures from `conftest.py` (`db_session`, `test_tenant`, `test_user`, `auth_headers`, `async_client`)
4. For HTTP tests, use the `async_client` + `auth_headers` pattern
5. For cross-tenant tests, use `second_tenant` and `second_user` fixtures
6. For E2E tests, use `@pytest.mark.e2e` and the `e2e_db_session` fixture
7. For regression tests, add `@pytest.mark.regression`
