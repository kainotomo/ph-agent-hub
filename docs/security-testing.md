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
| `@pytest.mark.regression` | Tests tied to known bug patterns and fixed issues |
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
| `test_oauth_state.py` | unit | OAuth state nonce store: store/retrieve, one-time use, TTL expiry, concurrent retrieval |
| `test_credential_security.py` | integration | Credential ownership enforcement and encryption-at-rest |
| `test_rate_limiter.py` | integration | Rate limiting on login and other endpoints |
| `test_abuse_scenarios.py` | integration | Forged tokens, SQL injection, XSS, token replay |
| `test_regression.py` | integration | Regression tests for fixed tenant-isolation gaps and known bug patterns |
| `test_concurrency.py` | integration | Stream cancellation, temp session races, rate limiter under concurrent load |

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

## Resolved Gaps

| Gap | Fix | Validated By |
|-----|-----|-------------|
| OAuth state integrity: state was plaintext, unsigned, non-expiring, replayable | Replaced with Redis-backed nonce store — tamper-evident, 10-min TTL, single-use (Issue #345) | `test_oauth_state.py`, `test_credentials_api.py::TestOAuthStateIntegrity`, `test_abuse_scenarios.py::TestOAuthStateAbuse` |
| Upload filename sanitization: original filenames used directly in S3 keys and Content-Disposition headers | Added `_sanitize_storage_filename()` for NFKD→ASCII normalization, path traversal prevention, and unsafe-char removal; added `_encode_content_disposition_filename()` for RFC 5987 header encoding (Issue #352) | `test_filename_sanitization.py` (19 unit tests), `test_upload_flow.py` (regression — 11 existing tests pass) |
| `create_credential` tool lookup missing tenant filter | Added `ToolORM.tenant_id == current_user.tenant_id` to the tool existence query (Issue #353) | `test_credentials_api.py::TestCreateCredential::test_create_credential_cross_tenant_tool_rejected` |

### Previously Identified Gaps — Now Fixed

The following tenant-isolation gaps were identified in an earlier audit and have been fixed and tested:

| Gap | Fix | Validated By |
|-----|-----|-------------|
| Prompt `list_prompts()` tenant filter optional | Made `tenant_id` mandatory | `test_regression.py::TestPromptTenantIsolation` |
| UserToolCredential missing `tenant_id` column | Added column + migration `e9f8d7c6b5a4` | `test_regression.py::TestCredentialTenantIsolation` |
| Temp session upload 403 guard documented but not enforced | Added `ForbiddenError` in `create_upload()` | `test_regression.py::TestTempSessionUploadGuard` |

Additional gaps that were already fixed before this audit:
- Prompt API update/delete tenant_id check (present in code)
- Skill API update/delete tenant_id check (present in code)
- Group `add_member()` tenant boundary check (present in code)
- Group `assign_model_to_group()` / `assign_tool_to_group()` tenant check (present in code)

See [testing-guide.md](testing-guide.md) for complete test suite documentation.
