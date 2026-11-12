"""Errors: `AppError` and the handlers that turn every failure into the envelope of section 4.

The client only ever sees `{"error": {"code", "message"}}` with an English sentence. Text from a
provider, a stack trace or a validation detail is logged here and goes no further.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

GENERIC_MESSAGE = "Something went wrong. Try again in a moment."

_STATUS_DEFAULTS: dict[int, tuple[str, str]] = {
    400: ("bad_request", "The request is not valid."),
    401: ("unauthorized", "Sign in to continue."),
    403: ("forbidden", "You do not have access to this."),
    404: ("not_found", "We could not find what you were looking for."),
    405: ("method_not_allowed", "This action is not available."),
    409: ("conflict", "The state has changed. Reload the page."),
    413: ("too_large", "The upload is too large."),
    415: ("unsupported_media_type", "This file type is not supported."),
    422: ("invalid_request", "The request is not valid."),
    429: ("too_many_requests", "Too many requests. Try again in a moment."),
    502: ("upstream_failed", "The background service did not answer. Try again in a moment."),
    503: ("unavailable", "The service is not available right now."),
    504: ("upstream_timeout", "The background service did not answer in time. Try again."),
}


class AppError(Exception):
    """An error meant for the user: a snake_case code, an English sentence and an HTTP status.

    `headers` are added to the response (`Retry-After` on a 429, for example).
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.headers = dict(headers) if headers else None

    def body(self) -> dict[str, dict[str, str]]:
        return error_body(self.code, self.message)


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def error_response(
    code: str, message: str, status: int, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(error_body(code, message), status_code=status, headers=headers)


def status_error(status: int) -> tuple[str, str]:
    """The default code and English sentence for an HTTP status."""
    if status in _STATUS_DEFAULTS:
        return _STATUS_DEFAULTS[status]
    return ("internal_error", GENERIC_MESSAGE) if status >= 500 else _STATUS_DEFAULTS[400]


async def _app_error(request: Request, exc: AppError) -> JSONResponse:
    if exc.status >= 500:
        logger.error(
            "%s %s -> %s %s", request.method, request.url.path, exc.status, exc.code, exc_info=exc
        )
    return error_response(exc.code, exc.message, exc.status, headers=exc.headers)


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # `detail` may hold text written for developers or passed on from a provider: drop it.
    code, message = status_error(exc.status_code)
    if exc.status_code >= 500:
        logger.error("%s %s -> %s", request.method, request.url.path, exc.status_code)
    return error_response(code, message, exc.status_code, headers=exc.headers)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    places = sorted(
        {".".join(str(part) for part in error.get("loc", ())) for error in exc.errors()}
    )
    logger.info("%s %s -> 422 at %s", request.method, request.url.path, ", ".join(places))
    code, message = status_error(422)
    return error_response(code, message, 422)


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "%s %s failed with %s", request.method, request.url.path, type(exc).__name__, exc_info=exc
    )
    return error_response("internal_error", GENERIC_MESSAGE, 500)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unexpected_error)
