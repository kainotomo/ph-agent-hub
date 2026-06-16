# Security Testing — PH Agent Hub

This document describes the security-focused test suite for PH Agent Hub. These tests validate authentication, authorization, tenant isolation, OAuth integrity, and rate limiting.

## Running Security Tests

```bash
# Run all security-focused tests
pytest backend/tests/ -m security -v

# Run tenant isolation tests specifically
pytest backend/tests/ -m tenant_isolation -v

# Run both security and tenant isolation markers together
pytest backend/tests/ -m "security or tenant_isolation" -v

# Run all tests except security (useful during feature development)
pytest backend/tests/ -m "not security and not tenant_isolation"
```

## Test Markers

| Marker | Description |
|--------|-------------|
| `@pytest.mark.security` | Authentication, authorization, and security boundary tests |
| `@pytest.mark.tenant_isolation` | Tests verifying cross-tenant data separation |
| `@pytest.mark.unit` | Fast, no external services (pure logic) |
| `@pytest.mark.integration` | Requires DB/Redis via conftest fixtures |
| `@pytest.mark.e2e` | Requires full Docker stack (Docker Compose up) |

## Test File Index

| File | Type | What It Tests |
|------|------|---------------|
| `test_auth.py` | unit | JWT token creation, decoding, expiry, tampering, guest/demo token separation |
| `test_auth_endpoints.py` | integration | Login, refresh, logout, /me endpoints with async HTTP client |
| `test_rbac.py` | unit | `require_admin` and `require_admin_or_manager` guards |
| `test_tenant_isolation.py` | integration | Service-layer tenant isolation for sessions, memories, prompts, skills, groups |
| `test_tenant_isolation_api.py` | integration | API-layer cross-tenant access prevention |
| `test_embed_isolation.py` | integration | Embed widget guest token tenant binding and config scoping |
| `test_oauth.py` | unit | Google and Microsoft OAuth code exchange and token refresh |
| `test_credential_security.py` | integration | Credential ownership enforcement and encryption-at-rest |
| `test_rate_limiter.py` | integration | Rate limiting on login and other endpoints |
| `test_abuse_scenarios.py` | integration | Forged tokens, SQL injection, XSS, token replay |

## Tenant Isolation Model

PH Agent Hub uses a row-level tenant isolation model:

1. **JWT Embedding**: Every JWT (user, guest, and demo tokens) carries a `tenant_id` claim
2. **Service-Layer Filtering**: All service functions accept a `tenant_id` parameter and filter queries accordingly
3. **API-Layer Enforcement**: Endpoint handlers verify that the requesting user's `tenant_id` matches the resource's `tenant_id` before allowing mutations
4. **Group Boundary Checks**: Cross-tenant group member addition and model/tool assignment are explicitly blocked with `ForbiddenError`

### Audit Trail for Authentication Events

Authentication-related operations are logged via the audit system with the following actions:

- `token.refreshed` — recorded when a refresh token is successfully exchanged for a new access token
- `token.revoked` — recorded when a refresh token is revoked during logout

Each audit entry includes the actor ID, actor role, IP address, and relevant context.

## Adding New Security Tests

1. Use `@pytest.mark.security` for auth/authz tests
2. Use `@pytest.mark.tenant_isolation` for cross-tenant boundary tests
3. Add new fixtures to `conftest.py` if multi-tenant setup is needed
4. Use `auth_headers(user)` fixture to generate valid JWT tokens
5. For async HTTP tests, use the `async_client` fixture with `httpx.AsyncClient`

## CI Integration

Security tests automatically run on every pull request via GitHub Actions. See `.github/workflows/ci.yml` for the `security-tests` job configuration.

## Known Gaps (Future Work)

- Password strength validation (no tests yet — feature not implemented)
- PKCE support for OAuth flows (no tests yet — feature not implemented)
- Security headers (X-Frame-Options, CSP) — no enforcement yet
- Distributed rate limiting (currently in-memory only)
