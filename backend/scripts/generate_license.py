#!/usr/bin/env python3
# =============================================================================
# PH Agent Hub — License Key Generator (Issue #243)
# =============================================================================
# Generates Ed25519-signed license tokens for Pro customers.
#
# Usage:
#   python scripts/generate_license.py --licensee "Acme Corp" --max-tenants -1 --expires 2027-12-31
#   python scripts/generate_license.py --licensee "Startup Inc" --max-tenants 10 --expires 2026-06-01
#
# Requirements:
#   pip install cryptography
#
# The private key must be stored securely (NOT in the repository).
# By default this script looks for it in the LICENSE_PRIVATE_KEY env var,
# or you can pass it via --private-key-file.
# =============================================================================

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def generate_keypair() -> tuple[Ed25519PrivateKey, bytes]:
    """Generate a new Ed25519 keypair.

    Returns (private_key, public_key_bytes).
    The public_key_bytes (32 bytes) should be base64-encoded and set as
    LICENSE_PUBLIC_KEY in the deployment environment.
    The private key must be stored securely and NEVER committed to the repo.
    """
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    return private_key, public_bytes


def sign_license(
    private_key: Ed25519PrivateKey,
    licensee: str,
    max_tenants: int,
    expires_at: datetime,
    issued_at: datetime | None = None,
) -> str:
    """Sign a license payload and return the license key string.

    Format: base64url(signature) . base64url(payload_json)
    """
    if issued_at is None:
        issued_at = datetime.now(timezone.utc)

    payload = {
        "v": 1,
        "sub": licensee,
        "max_tenants": max_tenants,  # -1 = unlimited
        "exp": expires_at.isoformat(),
        "iat": issued_at.isoformat(),
    }

    payload_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(payload_json)

    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    payload_b64 = base64.urlsafe_b64encode(payload_json).rstrip(b"=").decode("ascii")

    return f"{sig_b64}.{payload_b64}"


def main():
    parser = argparse.ArgumentParser(
        description="Generate a PH Agent Hub Pro license key",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate a license for unlimited tenants, expiring end of 2027
  python generate_license.py --licensee "Acme Corp" --max-tenants -1 --expires 2027-12-31

  # Generate a keypair (first time setup)
  python generate_license.py --generate-keypair

  # Use a specific private key file
  python generate_license.py --private-key-file /secure/license_private.pem \\
      --licensee "Startup Inc" --max-tenants 10 --expires 2026-06-01
        """,
    )
    parser.add_argument(
        "--licensee", "-l",
        help="Name of the licensee (company or individual)",
    )
    parser.add_argument(
        "--max-tenants", "-m",
        type=int,
        default=-1,
        help="Maximum tenants allowed (-1 = unlimited, default: -1)",
    )
    parser.add_argument(
        "--expires", "-e",
        help="Expiration date (ISO format, e.g. 2027-12-31 or 2027-12-31T00:00:00Z)",
    )
    parser.add_argument(
        "--issued-at",
        help="Issue date (ISO format, defaults to now)",
    )
    parser.add_argument(
        "--private-key-file", "-k",
        help="Path to the Ed25519 private key PEM file",
    )
    parser.add_argument(
        "--generate-keypair", "-g",
        action="store_true",
        help="Generate a new Ed25519 keypair and print public/private keys",
    )
    parser.add_argument(
        "--output-public-key",
        help="Also write the public key (base64) to this file (used with --generate-keypair)",
    )

    args = parser.parse_args()

    # --- Mode 1: Generate keypair ---
    if args.generate_keypair:
        private_key, public_bytes = generate_keypair()
        private_pem = private_key.private_bytes_raw()

        pub_b64 = base64.b64encode(public_bytes).decode("ascii")
        priv_b64 = base64.b64encode(private_pem).decode("ascii")

        print("=" * 65)
        print("  NEW ED25519 KEYPAIR")
        print("=" * 65)
        print()
        print("Public key (set as LICENSE_PUBLIC_KEY in your .env):")
        print(f"  {pub_b64}")
        print()
        print("Private key (STORE SECURELY — never commit to git!):")
        print(f"  {priv_b64}")
        print()
        print("=" * 65)
        print("Add this to your infrastructure/env:")
        print(f'LICENSE_PUBLIC_KEY={pub_b64}')
        print("=" * 65)

        if args.output_public_key:
            with open(args.output_public_key, "w") as f:
                f.write(pub_b64)
            print(f"Public key also written to: {args.output_public_key}")

        return

    # --- Mode 2: Generate license ---
    if not args.licensee:
        parser.error("--licensee is required when generating a license")
    if not args.expires:
        parser.error("--expires is required when generating a license")

    # Parse expiration
    try:
        expires_at = datetime.fromisoformat(args.expires)
    except ValueError:
        # Try appending time
        try:
            expires_at = datetime.fromisoformat(args.expires + "T23:59:59")
        except ValueError:
            parser.error(f"Cannot parse date: {args.expires}")
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    # Parse issued-at
    issued_at = None
    if args.issued_at:
        try:
            issued_at = datetime.fromisoformat(args.issued_at)
        except ValueError:
            parser.error(f"Cannot parse date: {args.issued_at}")
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)

    # Load private key
    if args.private_key_file:
        with open(args.private_key_file, "r") as f:
            key_data = f.read().strip()
        raw = base64.b64decode(key_data)
        private_key = Ed25519PrivateKey.from_private_bytes(raw)
    else:
        priv_b64 = os.environ.get("LICENSE_PRIVATE_KEY", "")
        if not priv_b64:
            print(
                "ERROR: No private key provided.\n"
                "  Set the LICENSE_PRIVATE_KEY environment variable, or\n"
                "  use --private-key-file to specify a file.\n"
                "  Use --generate-keypair to create a new keypair first.",
                file=sys.stderr,
            )
            sys.exit(1)
        raw = base64.b64decode(priv_b64)
        private_key = Ed25519PrivateKey.from_private_bytes(raw)

    # Sign
    license_key = sign_license(
        private_key=private_key,
        licensee=args.licensee,
        max_tenants=args.max_tenants,
        expires_at=expires_at,
        issued_at=issued_at,
    )

    print("=" * 65)
    print("  LICENSE KEY")
    print("=" * 65)
    print()
    print(f"  Licensee:    {args.licensee}")
    print(f"  Max tenants: {'Unlimited' if args.max_tenants == -1 else args.max_tenants}")
    print(f"  Expires:     {expires_at.isoformat()}")
    if issued_at:
        print(f"  Issued:      {issued_at.isoformat()}")
    print()
    print("  License key (copy the entire line below):")
    print(f"  {license_key}")
    print()
    print("=" * 65)
    print("The customer should paste this key into Admin → Settings → License Key.")
    print("=" * 65)


if __name__ == "__main__":
    main()
