"""One error envelope for every failure, so clients (and the web console) handle errors in one place:

    {"error": {"code": "not_found", "message": "unknown case", "request_id": "…", "details": …}}

`code` is stable and machine-readable; `message` is for humans; `request_id` matches the x-request-id
response header and the server logs.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

_DEFAULT_CODES = {400: "bad_request", 401: "unauthenticated", 403: "forbidden", 404: "not_found",
                  405: "method_not_allowed", 409: "conflict", 413: "too_large", 415: "unsupported_media_type",
                  422: "validation_error", 429: "rate_limited", 503: "unavailable"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None, headers: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"unknown {what}")


def error_body(request: Request, code: str, message: str, details=None) -> dict:
    err = {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None)}
    if details is not None:
        err["details"] = details
    return {"error": err}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, e: ApiError):
        return JSONResponse(error_body(request, e.code, e.message, e.details), e.status, headers=e.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, e: StarletteHTTPException):
        code = _DEFAULT_CODES.get(e.status_code, "error")
        return JSONResponse(error_body(request, code, str(e.detail)), e.status_code, headers=getattr(e, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, e: RequestValidationError):
        details = [{"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")} for err in e.errors()]
        return JSONResponse(error_body(request, "validation_error", "request failed validation", details), 422)
