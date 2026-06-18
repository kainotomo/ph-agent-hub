# =============================================================================
# PH Agent Hub — License Service Tests
# =============================================================================
#
# Since the conftest autouse fixture ``_disable_license_gate`` patches
# ``get_effective_tenant_limit`` for ALL tests, we undo that patch here so
# license service tests exercise the real logic.
# =============================================================================

import base64
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.license_service import (
    LicenseInfo,
    LicenseStatus,
    _is_expired,
    can_create_tenant,
    get_effective_tenant_limit,
    get_license_status,
    is_tenant_accessible,
    verify_license_key,
)
from src.db.orm.app_settings import AppSetting
from src.services.settings_service import get_setting


# ---------------------------------------------------------------------------
# Helper: set a setting without calling commit (avoids polluting subsequent
# tests since db_session.begin() is already the active transaction).
# ---------------------------------------------------------------------------


async def _set_setting_no_commit(db: AsyncSession, key: str, value: str) -> None:
    """Upsert a setting row using flush() only — no commit()."""
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == key)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.flush()


# ---------------------------------------------------------------------------
# Override conftest's autouse license gate patch
# ---------------------------------------------------------------------------
# The conftest's ``_disable_license_gate`` patches
# ``get_effective_tenant_limit`` to return 1_000_000 for ALL tests.  We
# override it here (same fixture name, same autouse scope) so license
# service tests run with the real function.
#
# Also reset the module-level _PUBLIC_KEY cache before each test so that
# monkeypatching LICENSE_PUBLIC_KEY takes effect regardless of test order.
# ---------------------------------------------------------------------------

import src.services.license_service as _ls


@pytest.fixture(autouse=True)
def _disable_license_gate():
    """Override conftest fixture — do NOT patch the license gate."""
    # Reset the public key cache so monkeypatching LICENSE_PUBLIC_KEY works
    _ls._PUBLIC_KEY = None
    pass


# ---------------------------------------------------------------------------
# Ed25519 test key pair helpers
# ---------------------------------------------------------------------------

def _generate_key_pair():
    """Generate an Ed25519 key pair for signing test license tokens.

    Returns (private_key_pem, public_key_pem).
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    return private_pem, public_pem


_KEY_PAIRS: dict[str, tuple] = {}


def _get_or_create_key_pair(name: str = "default"):
    """Get or create a cached Ed25519 key pair."""
    if name not in _KEY_PAIRS:
        _KEY_PAIRS[name] = _generate_key_pair()
    return _KEY_PAIRS[name]


def _sign_license_token(
    private_key_pem: str,
    payload: dict,
) -> str:
    """Sign a payload dict and return a license token string.

    Token format: base64(signature).base64(payload_json)
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )

    payload_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(payload_json)

    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_json).rstrip(b"=").decode("utf-8")

    return f"{sig_b64}.{payload_b64}"


def _make_valid_payload(**overrides) -> dict:
    """Create a valid license payload with optional overrides."""
    now = datetime.now(timezone.utc)
    payload = {
        "v": 1,
        "sub": "test-licensee",
        "max_tenants": 10,
        "exp": (now + timedelta(days=365)).isoformat(),
        "iat": now.isoformat(),
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Unit tests — verify_license_key
# ---------------------------------------------------------------------------

class TestVerifyLicenseKey:
    """Unit tests for verify_license_key — no DB needed."""

    @pytest.fixture(autouse=True)
    def _setup_key(self, monkeypatch: pytest.MonkeyPatch):
        """Set LICENSE_PUBLIC_KEY to a test key by default."""
        _, public_pem = _get_or_create_key_pair()
        monkeypatch.setattr(
            "src.services.license_service.settings.LICENSE_PUBLIC_KEY",
            public_pem,
        )
        yield

    def _token(self, payload_override: dict | None = None) -> str:
        """Helper: sign a valid token with optional payload overrides."""
        private_pem, _ = _get_or_create_key_pair()
        payload = _make_valid_payload(**(payload_override or {}))
        return _sign_license_token(private_pem, payload)

    def test_valid_license(self):
        """Valid key returns LicenseInfo with correct fields."""
        info = verify_license_key(self._token())
        assert info is not None
        assert info.licensee == "test-licensee"
        assert info.max_tenants == 10
        assert info.raw_token is not None

    def test_unlimited_tenants(self):
        """max_tenants=-1 is parsed correctly."""
        info = verify_license_key(self._token({"max_tenants": -1}))
        assert info is not None
        assert info.max_tenants == -1

    def test_expired_key(self):
        """Token with exp in the past returns None."""
        past = datetime.now(timezone.utc) - timedelta(days=1)
        info = verify_license_key(
            self._token({"exp": past.isoformat()})
        )
        assert info is None

    def test_malformed_no_dot(self):
        """Key missing the dot separator returns None."""
        info = verify_license_key("no-dot-here")
        assert info is None

    def test_invalid_base64_signature(self):
        """Non-base64 signature part returns None."""
        private_pem, _ = _get_or_create_key_pair()
        payload = _make_valid_payload()
        payload_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_json).rstrip(b"=").decode("utf-8")
        info = verify_license_key(f"!!!invalid!!!.{payload_b64}")
        assert info is None

    def test_invalid_signature(self):
        """Tampered payload fails signature verification."""
        private_pem, _ = _get_or_create_key_pair()
        payload = _make_valid_payload()
        payload_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        sig = base64.urlsafe_b64encode(
            private_pem.encode("utf-8")  # garbage signature
        ).rstrip(b"=").decode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_json).rstrip(b"=").decode("utf-8")
        info = verify_license_key(f"{sig}.{payload_b64}")
        assert info is None

    def test_payload_not_json(self):
        """Non-JSON payload returns None."""
        private_pem, _ = _get_or_create_key_pair()
        payload_bytes = b"not-json"
        signature = _load_private_key().sign(payload_bytes)
        sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("utf-8")
        info = verify_license_key(f"{sig_b64}.{payload_b64}")
        assert info is None

    def test_unsupported_version(self):
        """Version != 1 returns None."""
        info = verify_license_key(self._token({"v": 2}))
        assert info is None

    def test_missing_required_fields(self):
        """Missing sub returns None."""
        info = verify_license_key(
            self._token({"sub": None})
        )
        assert info is None

    def test_invalid_date_format(self):
        """Invalid exp date format returns None."""
        info = verify_license_key(
            self._token({"exp": "not-a-date"})
        )
        assert info is None

    def test_no_public_key_configured(self, monkeypatch):
        """LICENSE_PUBLIC_KEY='' returns None."""
        monkeypatch.setattr(
            "src.services.license_service.settings.LICENSE_PUBLIC_KEY",
            "",
        )
        info = verify_license_key(self._token())
        assert info is None

    def test_whitespace_in_key(self):
        """Whitespace/newlines in key are stripped before verification."""
        token = self._token()
        # Add internal whitespace
        dirty_token = f"  {token[:20]} \n {token[20:]}  "
        info = verify_license_key(dirty_token)
        assert info is not None

    def test_future_iat_no_enforcement(self):
        """Future-dated iat should still be valid."""
        future = datetime.now(timezone.utc) + timedelta(days=30)
        info = verify_license_key(
            self._token({"iat": future.isoformat()})
        )
        assert info is not None


def _load_private_key():
    """Load the cached private key for ad-hoc signing in tests."""
    private_pem, _ = _get_or_create_key_pair()
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_private_key(
        private_pem.encode("utf-8"), password=None
    )


class TestIsExpired:
    """Tests for _is_expired helper."""

    def test_expired_token(self):
        """Token with past exp returns True."""
        _, public_pem = _get_or_create_key_pair()
        private_pem, _ = _get_or_create_key_pair("expired_test")
        payload = _make_valid_payload(
            exp=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        )
        token = _sign_license_token(private_pem, payload)
        assert _is_expired(token) is True

    def test_valid_token(self):
        """Token with future exp returns False."""
        _, public_pem = _get_or_create_key_pair()
        private_pem, _ = _get_or_create_key_pair("valid_exp_test")
        payload = _make_valid_payload()  # 365 days in the future
        token = _sign_license_token(private_pem, payload)
        assert _is_expired(token) is False

    def test_malformed_token(self):
        """Malformed token returns False (exception swallowed)."""
        assert _is_expired("malformed") is False

    def test_no_exp_field(self):
        """Token missing exp field returns False."""
        _, public_pem = _get_or_create_key_pair()
        private_pem, _ = _get_or_create_key_pair("no_exp_test")
        payload = _make_valid_payload(exp=None)
        del payload["exp"]
        token = _sign_license_token(private_pem, payload)
        assert _is_expired(token) is False


# ---------------------------------------------------------------------------
# Integration tests — require DB
# ---------------------------------------------------------------------------


class TestGetLicenseStatus:
    """Tests for get_license_status — reads license key from DB."""

    @pytest.fixture(autouse=True)
    def _setup_key(self, monkeypatch: pytest.MonkeyPatch):
        """Set LICENSE_PUBLIC_KEY."""
        _, public_pem = _get_or_create_key_pair()
        monkeypatch.setattr(
            "src.services.license_service.settings.LICENSE_PUBLIC_KEY",
            public_pem,
        )
        yield

    def _valid_token(self) -> str:
        private_pem, _ = _get_or_create_key_pair()
        return _sign_license_token(private_pem, _make_valid_payload())

    async def test_not_set(self, db_session: AsyncSession):
        """No license key stored returns (NOT_SET, None)."""
        # Ensure no key exists (cleanup from previous test runs)
        from sqlalchemy import delete as sa_delete
        await db_session.execute(
            sa_delete(AppSetting).where(AppSetting.key == "license_key")
        )
        await db_session.flush()

        status, info = await get_license_status(db_session)
        assert status == LicenseStatus.NOT_SET
        assert info is None

    async def test_valid_key(self, db_session: AsyncSession):
        """Valid stored key returns (VALID, LicenseInfo)."""
        token = self._valid_token()
        await _set_setting_no_commit(db_session, "license_key", token)

        status, info = await get_license_status(db_session)
        assert status == LicenseStatus.VALID
        assert info is not None
        assert info.licensee == "test-licensee"
        assert info.max_tenants == 10

    async def test_expired_key(self, db_session: AsyncSession):
        """Expired stored key returns (EXPIRED, None)."""
        private_pem, _ = _get_or_create_key_pair()
        payload = _make_valid_payload(
            exp=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        )
        token = _sign_license_token(private_pem, payload)
        await _set_setting_no_commit(db_session, "license_key", token)

        status, info = await get_license_status(db_session)
        assert status == LicenseStatus.EXPIRED
        assert info is None

    async def test_invalid_signature(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ):
        """Key with bad signature returns (INVALID, None)."""
        # Generate two different key pairs
        private_pem1, _ = _get_or_create_key_pair()  # key1
        private_pem2, public_pem2 = _generate_key_pair()  # key2

        # Set public key to key2
        monkeypatch.setattr(
            _ls.settings, "LICENSE_PUBLIC_KEY", public_pem2,
        )

        # Sign token with key1's private key — key2's public key won't verify it
        token = _sign_license_token(private_pem1, _make_valid_payload())
        await _set_setting_no_commit(db_session, "license_key", token)

        status, info = await get_license_status(db_session)
        assert status == LicenseStatus.INVALID
        assert info is None


class TestGetEffectiveTenantLimit:
    """Tests for get_effective_tenant_limit."""

    @pytest.fixture(autouse=True)
    def _setup_key(self, monkeypatch: pytest.MonkeyPatch):
        _, public_pem = _get_or_create_key_pair()
        monkeypatch.setattr(
            "src.services.license_service.settings.LICENSE_PUBLIC_KEY",
            public_pem,
        )
        monkeypatch.setattr(
            "src.services.license_service.settings.MAX_FREE_TENANTS",
            3,
        )
        yield

    async def test_valid_license_finite(
        self, db_session: AsyncSession
    ):
        """Valid license with max_tenants=5 returns 5."""
        private_pem, _ = _get_or_create_key_pair()
        token = _sign_license_token(private_pem, _make_valid_payload(max_tenants=5))
        await _set_setting_no_commit(db_session, "license_key", token)
        assert await get_effective_tenant_limit(db_session) == 5

    async def test_valid_license_unlimited(
        self, db_session: AsyncSession
    ):
        """Valid license with max_tenants=-1 returns 1_000_000."""
        private_pem, _ = _get_or_create_key_pair()
        token = _sign_license_token(private_pem, _make_valid_payload(max_tenants=-1))
        await _set_setting_no_commit(db_session, "license_key", token)
        assert await get_effective_tenant_limit(db_session) == 1_000_000

    async def test_no_license(self, db_session: AsyncSession):
        """No license returns MAX_FREE_TENANTS (3)."""
        assert await get_effective_tenant_limit(db_session) == 3


class TestIsTenantAccessible:
    """Tests for is_tenant_accessible."""

    @pytest.fixture(autouse=True)
    def _setup_key(self, monkeypatch: pytest.MonkeyPatch):
        _, public_pem = _get_or_create_key_pair()
        monkeypatch.setattr(
            "src.services.license_service.settings.LICENSE_PUBLIC_KEY",
            public_pem,
        )
        yield

    async def test_ordinal_within_limit(self, db_session: AsyncSession):
        """Ordinal 1 with limit 10 returns True."""
        private_pem, _ = _get_or_create_key_pair()
        token = _sign_license_token(private_pem, _make_valid_payload(max_tenants=10))
        await _set_setting_no_commit(db_session, "license_key", token)
        assert await is_tenant_accessible(db_session, 1) is True

    async def test_ordinal_exceeds_limit(self, db_session: AsyncSession):
        """Ordinal 11 with limit 10 returns False."""
        private_pem, _ = _get_or_create_key_pair()
        token = _sign_license_token(private_pem, _make_valid_payload(max_tenants=10))
        await _set_setting_no_commit(db_session, "license_key", token)
        assert await is_tenant_accessible(db_session, 11) is False


class TestCanCreateTenant:
    """Tests for can_create_tenant."""

    @pytest.fixture(autouse=True)
    def _setup_key(self, monkeypatch: pytest.MonkeyPatch):
        _, public_pem = _get_or_create_key_pair()
        monkeypatch.setattr(
            "src.services.license_service.settings.LICENSE_PUBLIC_KEY",
            public_pem,
        )
        monkeypatch.setattr(
            "src.services.license_service.settings.MAX_FREE_TENANTS",
            3,
        )
        yield

    async def test_below_limit(self, db_session: AsyncSession):
        """current_count < limit returns (True, '')."""
        private_pem, _ = _get_or_create_key_pair()
        token = _sign_license_token(private_pem, _make_valid_payload(max_tenants=10))
        await _set_setting_no_commit(db_session, "license_key", token)
        allowed, msg = await can_create_tenant(db_session, 5)
        assert allowed is True
        assert msg == ""

    async def test_at_limit_with_license(self, db_session: AsyncSession):
        """current_count >= limit with valid license returns user-facing message."""
        private_pem, _ = _get_or_create_key_pair()
        token = _sign_license_token(private_pem, _make_valid_payload(max_tenants=10))
        await _set_setting_no_commit(db_session, "license_key", token)
        allowed, msg = await can_create_tenant(db_session, 10)
        assert allowed is False
        assert "Tenant limit reached" in msg

    async def test_at_limit_without_license(self, db_session: AsyncSession):
        """current_count >= limit without license returns free-tier message."""
        allowed, msg = await can_create_tenant(db_session, 3)
        assert allowed is False
        assert "Free tier is limited" in msg
