"""Error envelope (`Error` schema in the OpenAPI spec) and the exception handlers that emit it."""

from typing import Any, cast

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

REQUEST_ID_SCOPE_KEY = "nexus.request_id"

_log = structlog.get_logger()

_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}


class ApiError(Exception):
    """Raise anywhere in a request to return `{"error": {code, message, details?, request_id}}`."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def request_id_of(request: Request) -> str:
    return str(request.scope.get(REQUEST_ID_SCOPE_KEY, "req_unknown"))


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    error["request_id"] = request_id_of(request)
    return JSONResponse({"error": error}, status_code=status_code, headers=headers)


async def _api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(ApiError, exc)  # registered for ApiError only
    return error_response(
        request, exc.status_code, exc.code, exc.message, details=exc.details, headers=exc.headers
    )


# FastAPI raises this plain 400 when a body cannot be decoded at all (e.g. invalid UTF-8), while
# other malformed JSON gets a RequestValidationError. The contract says 422 for every bad body.
_BODY_PARSE_ERROR = "There was an error parsing the body"


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, exc)  # registered for this type only
    if exc.status_code == 400 and exc.detail == _BODY_PARSE_ERROR:
        return _body_unparseable(request)
    code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
    message = "Resource not found." if exc.status_code == 404 else str(exc.detail)
    headers = dict(exc.headers) if exc.headers else None
    return error_response(request, exc.status_code, code, message, headers=headers)


def _body_unparseable(request: Request) -> JSONResponse:
    field = {"loc": ["body"], "msg": "Request body could not be parsed.", "type": "body_invalid"}
    return error_response(
        request,
        422,
        "VALIDATION_FAILED",
        "Request validation failed.",
        details={"fields": [field]},
    )


async def _validation_handler(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, exc)  # registered for this type only
    # Pydantic's `input` and `ctx` can hold submitted values (passwords, invite codes): drop them.
    fields = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return error_response(
        request,
        422,
        "VALIDATION_FAILED",
        "Request validation failed.",
        details={"fields": fields},
    )


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request_id_of(request)
    _log.exception("unhandled_error", request_id=request_id, path=request.url.path)
    response = error_response(request, 500, "INTERNAL_ERROR", "Something went wrong.")
    response.headers["x-request-id"] = request_id
    return response


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
