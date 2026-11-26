# =============================================================================
# PH Agent Hub — Build identity (Issue #659)
# =============================================================================
# A session export must be able to answer "which build produced this?".
# ``version`` in the export JSON is the *schema* version, so the running
# application version and the commit are reported separately by these
# helpers.  Neither value is hard-coded at the call site:
#
#   * ``APP_VERSION`` is the single source of truth for the application
#     version — FastAPI's ``version`` and the export both read it, and the
#     release commit bumps it once;
#   * the commit comes from the build (``GIT_COMMIT`` et al. in an image) or,
#     in a development checkout, from git itself.  When neither is available
#     it is ``None`` rather than a guess.
# =============================================================================

import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

APP_VERSION = "3.15.5"
"""Version of the running application.  Bumped once per release."""

# backend/src/core/build_info.py → the repository's backend directory, which
# git accepts as ``-C`` even though the repository root is one level up.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

_COMMIT_ENV_VARS = ("GIT_COMMIT", "SOURCE_COMMIT", "APP_COMMIT", "BUILD_COMMIT")


def get_app_version() -> str:
    """Return the application version of the running build.

    ``APP_VERSION`` may be overridden at runtime (useful for an image built
    from a different tag), but the default is the source-of-truth constant.
    """
    return (os.environ.get("APP_VERSION") or "").strip() or APP_VERSION


@lru_cache(maxsize=1)
def get_commit() -> str | None:
    """Return the short commit sha of the running build, or ``None``.

    Resolution order: an explicit build env var (set by the image build or
    the deployment), then ``git rev-parse`` in a development checkout.  A
    missing commit is reported as ``None``; it is never invented.
    """
    for name in _COMMIT_ENV_VARS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value[:12]

    try:
        completed = subprocess.run(
            ["git", "-C", str(_BACKEND_ROOT), "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("Could not resolve git commit", exc_info=True)
        return None

    sha = completed.stdout.strip()
    if completed.returncode == 0 and sha:
        return sha
    return None
