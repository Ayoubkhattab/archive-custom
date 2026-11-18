"""
Consistent error responses for the policy-scoped APIs (images, pdfs, audit):

    401 {"code": "not_authenticated", "detail": ...}
    403 {"code": "forbidden",         "detail": <safe message>}
    404 {"code": "not_found",         "detail": "Not found."}
    413 {"code": "payload_too_large", ...}
    415 {"code": "unsupported_media_type", ...}
    416 {"code": "range_not_satisfiable", ...}
    422 {"code": "validation_error",  "detail": ..., "errors": {...}}
    429 {"code": "throttled",         "detail": ..., "retry_after": n}
    500 {"code": "server_error",      "detail": "Internal server error."}

The legacy endpoints keep DRF's default handler (400 for validation):
switching them to 422 would break the existing Angular forms and clients.
Stack traces are never returned; unexpected errors are logged server side.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("paperless.api")


class ApiError(exceptions.APIException):
    """Typed API error: ``raise ApiError(415, "unsupported_media_type", msg)``."""

    def __init__(self, status_code: int, code: str, detail: str):
        self.status_code = status_code
        super().__init__(detail=detail, code=code)
        self.error_code = code


def _body(code: str, detail, **extra) -> dict:
    return {"code": code, "detail": detail, **extra}


def scoped_exception_handler(exc, context):
    if isinstance(exc, ApiError):
        return Response(_body(exc.error_code, str(exc.detail)), status=exc.status_code)
    if isinstance(exc, exceptions.ValidationError):
        return Response(
            _body("validation_error", "Invalid input.", errors=exc.detail),
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
        response = drf_exception_handler(exc, context)  # keeps WWW-Authenticate
        response.data = _body("not_authenticated", "Authentication required.")
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return response
    if isinstance(exc, (exceptions.PermissionDenied, DjangoPermissionDenied)):
        detail = (
            getattr(exc, "detail", None)
            or "You do not have permission to perform this action."
        )
        return Response(
            _body("forbidden", str(detail)),
            status=status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, (exceptions.NotFound, Http404)):
        return Response(
            _body("not_found", "Not found."),
            status=status.HTTP_404_NOT_FOUND,
        )
    if isinstance(exc, exceptions.Throttled):
        response = Response(
            _body(
                "throttled",
                "Too many requests.",
                retry_after=exc.wait and int(exc.wait),
            ),
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
        if exc.wait:
            response["Retry-After"] = str(int(exc.wait))
        return response
    if isinstance(exc, exceptions.UnsupportedMediaType):
        return Response(
            _body("unsupported_media_type", str(exc.detail)),
            status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )
    if isinstance(exc, exceptions.APIException):
        response = drf_exception_handler(exc, context)
        codes = exc.get_codes()
        response.data = _body(
            codes if isinstance(codes, str) else "error",
            str(exc.detail) if isinstance(exc.detail, str) else "Request failed.",
        )
        return response

    logger.exception(
        "Unhandled API error in %s",
        context.get("view").__class__.__name__,
    )
    return Response(
        _body("server_error", "Internal server error."),
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
