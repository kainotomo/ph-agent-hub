# =============================================================================
# PH Agent Hub — Shared Exception Hierarchy
# =============================================================================
# All application-level exceptions inherit from AppException.
# FastAPI exception handlers are registered on the app in main.py.
# =============================================================================

from fastapi import Request
from fastapi.responses import JSONResponse


class AppException(Exception):
    """Base class for all application-level exceptions.

    ``extra`` is merged into the JSON response body next to ``detail``.
    It lets clients react to a machine-readable code (for example
    ``session_busy``) without parsing prose.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        extra: dict | None = None,
    ):
        self.message = message
        self.status_code = status_code
        self.extra = extra or {}


class NotFoundError(AppException):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, status_code=404)


class ForbiddenError(AppException):
    def __init__(self, message: str = "Forbidden"):
        super().__init__(message, status_code=403)


class UnauthorizedError(AppException):
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message, status_code=401)


class ValidationError(AppException):
    def __init__(self, message: str = "Validation error"):
        super().__init__(message, status_code=422)


class ConflictError(AppException):
    def __init__(self, message: str = "Conflict", extra: dict | None = None):
        super().__init__(message, status_code=409, extra=extra)


class SessionBusyError(ConflictError):
    """A second run was attempted while one is already active (Issue #604).

    The response body carries ``code="session_busy"`` plus the active
    ``run_id`` and ``session_id`` so the client can attach to the current
    run instead of starting a duplicate one.
    """

    def __init__(
        self,
        session_id: str,
        run_id: str | None = None,
        kind: str | None = None,
    ):
        extra = {
            "code": "session_busy",
            "session_id": session_id,
            "run_id": run_id,
        }
        if kind:
            extra["kind"] = kind
        super().__init__(
            "This conversation is already running. "
            "Wait for it to finish or stop the current run.",
            extra=extra,
        )


class ServiceUnavailableError(AppException):
    def __init__(self, message: str = "Service unavailable"):
        super().__init__(message, status_code=503)


class InsufficientBalanceError(AppException):
    def __init__(self, message: str = "Insufficient balance. Please contact your administrator to top up."):
        super().__init__(message, status_code=402)


# ---------------------------------------------------------------------------
# FastAPI exception handlers
# ---------------------------------------------------------------------------

async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, **getattr(exc, "extra", {})},
    )
