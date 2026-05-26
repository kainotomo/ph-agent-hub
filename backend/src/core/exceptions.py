# =============================================================================
# PH Agent Hub — Shared Exception Hierarchy
# =============================================================================
# All application-level exceptions inherit from AppException.
# FastAPI exception handlers are registered on the app in main.py.
# =============================================================================

from fastapi import Request
from fastapi.responses import JSONResponse


class AppException(Exception):
    """Base class for all application-level exceptions."""

    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code


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
    def __init__(self, message: str = "Conflict"):
        super().__init__(message, status_code=409)


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
        content={"detail": exc.message},
    )
