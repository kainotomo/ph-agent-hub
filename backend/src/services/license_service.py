# =============================================================================
# PH Agent Hub — License Service (Issue #243)
# =============================================================================
# Ed25519 public-key license verification.
# The private key is held by the vendor; the public key is embedded here.
# License tokens are base64-encoded JSON payloads signed with Ed25519.
#
# Payload format:
#   {
#     "v": 1,
#     "sub": "licensee-name",
#     "max_tenants": -1,        # -1 = unlimited
#     "exp": "2027-06-01T00:00:00Z",
#     "iat": "2026-06-01T00:00:00Z"
#   }
# =============================================================================

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from .settings_service import get_setting

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LICENSE_SETTING_KEY = "license_key"
UNLIMITED_TENANTS = -1


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class LicenseStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    EXPIRED = "expired"
    NOT_SET = "not_set"


@dataclass
class LicenseInfo:
    """Parsed and verified license information."""

    licensee: str
    max_tenants: int  # -1 = unlimited
    expires_at: datetime
    issued_at: datetime
    raw_token: str = field(repr=False)


# ---------------------------------------------------------------------------
# Public-key loading
# ---------------------------------------------------------------------------


def _load_public_key() -> Ed25519PublicKey | None:
    """Load the Ed25519 public key from config.

    Returns None if no public key is configured (graceful degradation —
    license verification always fails, gating falls back to free tier).
    """
    key_pem = settings.LICENSE_PUBLIC_KEY.strip()
    if not key_pem:
        logger.warning(
            "LICENSE_PUBLIC_KEY is not configured — license verification disabled"
        )
        return None

    try:
        # Support both raw base64 and PEM-wrapped keys
        if "-----BEGIN PUBLIC KEY-----" in key_pem:
            return serialization.load_pem_public_key(key_pem.encode("utf-8"))
        else:
            raw = base64.b64decode(key_pem)
            return Ed25519PublicKey.from_public_bytes(raw)
    except Exception:
        logger.exception("Failed to load LICENSE_PUBLIC_KEY")
        return None


# Cache the public key at module load time
_PUBLIC_KEY: Ed25519PublicKey | None = None


def _get_public_key() -> Ed25519PublicKey | None:
    """Lazy-load and cache the public key."""
    global _PUBLIC_KEY
    if _PUBLIC_KEY is None and settings.LICENSE_PUBLIC_KEY:
        _PUBLIC_KEY = _load_public_key()
    return _PUBLIC_KEY


# ---------------------------------------------------------------------------
# Core verification
# ---------------------------------------------------------------------------


def verify_license_key(raw_key: str) -> LicenseInfo | None:
    """Verify a license key string and return parsed LicenseInfo, or None.

    The key format is:  base64(signature) . base64(payload_json)

    Returns None if:
    - The key is malformed
    - The signature is invalid
    - The license has expired
    - The public key is not configured
    """
    public_key = _get_public_key()
    if public_key is None:
        logger.warning("Cannot verify license — no public key configured")
        return None

    raw_key = raw_key.strip()

    # Split signature and payload
    parts = raw_key.split(".", 1)
    if len(parts) != 2:
        logger.warning("License key is malformed (missing dot separator)")
        return None

    sig_b64, payload_b64 = parts

    try:
        signature = base64.urlsafe_b64decode(sig_b64 + "==")
        payload_json = base64.urlsafe_b64decode(payload_b64 + "==")
    except Exception:
        logger.warning("License key contains invalid base64")
        return None

    # Verify Ed25519 signature
    try:
        public_key.verify(signature, payload_json)
    except InvalidSignature:
        logger.warning("License key signature verification failed")
        return None
    except Exception:
        logger.exception("Unexpected error during signature verification")
        return None

    # Parse payload
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        logger.warning("License key payload is not valid JSON")
        return None

    # Validate payload fields
    version = payload.get("v")
    if version != 1:
        logger.warning("License key has unsupported version: %s", version)
        return None

    licensee = payload.get("sub")
    max_tenants = payload.get("max_tenants")
    exp_str = payload.get("exp")
    iat_str = payload.get("iat")

    if not licensee or max_tenants is None or not exp_str or not iat_str:
        logger.warning("License key payload is missing required fields")
        return None

    try:
        expires_at = datetime.fromisoformat(exp_str)
        issued_at = datetime.fromisoformat(iat_str)
    except ValueError:
        logger.warning("License key has invalid date format")
        return None

    # Ensure timezone-aware comparison
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if now > expires_at:
        logger.info(
            "License for '%s' expired on %s", licensee, expires_at.isoformat()
        )
        return None

    return LicenseInfo(
        licensee=licensee,
        max_tenants=int(max_tenants),
        expires_at=expires_at,
        issued_at=issued_at,
        raw_token=raw_key,
    )


# ---------------------------------------------------------------------------
# Service functions (require DB)
# ---------------------------------------------------------------------------


async def get_license_status(db: AsyncSession) -> tuple[LicenseStatus, LicenseInfo | None]:
    """Read the stored license key and determine its status.

    Returns (status, info) where status is one of:
    - NOT_SET: no license key stored
    - VALID: license is present and valid
    - EXPIRED: license was valid but has expired
    - INVALID: license key is present but signature or format is invalid
    """
    raw_key = await get_setting(db, LICENSE_SETTING_KEY)

    if not raw_key or not raw_key.strip():
        return LicenseStatus.NOT_SET, None

    info = verify_license_key(raw_key)

    if info is None:
        # Could be invalid signature, malformed, or expired.
        # We distinguish expired by attempting a lenient parse.
        if _is_expired(raw_key):
            return LicenseStatus.EXPIRED, None
        return LicenseStatus.INVALID, None

    return LicenseStatus.VALID, info


async def get_effective_tenant_limit(db: AsyncSession) -> int:
    """Return the maximum number of tenants allowed.

    If a valid license is present with max_tenants = -1, returns a very
    large sentinel (1_000_000) to represent unlimited.
    Otherwise, returns MAX_FREE_TENANTS (default: 3).
    """
    status, info = await get_license_status(db)

    if status == LicenseStatus.VALID and info is not None:
        if info.max_tenants == UNLIMITED_TENANTS:
            return 1_000_000  # effectively unlimited
        return info.max_tenants

    return settings.MAX_FREE_TENANTS


async def is_tenant_accessible(
    db: AsyncSession, tenant_ordinal: int
) -> bool:
    """Check whether a tenant at the given ordinal position (1-indexed) is
    within the current effective limit."""
    limit = await get_effective_tenant_limit(db)
    return tenant_ordinal <= limit


async def can_create_tenant(db: AsyncSession, current_count: int) -> tuple[bool, str]:
    """Check if a new tenant can be created.

    Returns (allowed, message) where message is the reason when blocked.
    """
    limit = await get_effective_tenant_limit(db)

    if current_count >= limit:
        status, _ = await get_license_status(db)
        if status == LicenseStatus.VALID:
            return False, (
                f"Tenant limit reached ({limit}). "
                "Contact support to increase your license limit."
            )
        else:
            return False, (
                f"Free tier is limited to {settings.MAX_FREE_TENANTS} tenants. "
                "Upgrade to Pro for unlimited tenants."
            )

    return True, ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_expired(raw_key: str) -> bool:
    """Lenient check — try to decode payload without verifying signature,
    to determine if an otherwise-valid token has simply expired."""
    try:
        parts = raw_key.strip().split(".", 1)
        if len(parts) != 2:
            return False
        payload_json = base64.urlsafe_b64decode(parts[1] + "==")
        payload = json.loads(payload_json)
        exp_str = payload.get("exp")
        if not exp_str:
            return False
        expires_at = datetime.fromisoformat(exp_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expires_at
    except Exception:
        return False
