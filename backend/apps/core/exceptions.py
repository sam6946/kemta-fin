"""Enveloppe d'erreur uniforme : `{ "error": { code, message, details, request_id } }`."""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler

from .logging_utils import get_request_id

logger = logging.getLogger("kemta.api")

CODE_BY_STATUS = {
    status.HTTP_400_BAD_REQUEST: "validation_error",
    status.HTTP_401_UNAUTHORIZED: "not_authenticated",
    status.HTTP_403_FORBIDDEN: "permission_denied",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "file_too_large",
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "unsupported_media_type",
    status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
}


def kemta_exception_handler(exc, context):
    if isinstance(exc, KemtaAPIError):
        return Response(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "request_id": get_request_id(),
                }
            },
            status=exc.status_code,
        )

    response = exception_handler(exc, context)

    if response is None:
        # Erreur non gérée : on journalise avec le request_id, on ne renvoie rien de sensible.
        logger.exception("Erreur non gérée sur %s", context.get("request"))
        return Response(
            {
                "error": {
                    "code": "server_error",
                    "message": "Une erreur inattendue est survenue.",
                    "details": {},
                    "request_id": get_request_id(),
                }
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    data = response.data
    code = CODE_BY_STATUS.get(response.status_code, "error")
    message = "Une erreur est survenue."
    details: dict = {}

    if isinstance(data, dict):
        if "detail" in data:
            message = str(data["detail"])
        elif "error_code" in data:
            code = data.pop("error_code")
            message = data.pop("message", message)
            details = data
        else:
            details = {k: v for k, v in data.items() if k != "error_code"}
    elif isinstance(data, list):
        details = {"errors": data}

    response.data = {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": get_request_id(),
        }
    }
    return response


class KemtaAPIError(APIException):
    """Erreur métier levée par les services.

    `code` est un identifiant stable consommé par le frontend (i18n des messages) :
    les services ne produisent jamais de message destiné à être analysé côté client.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Une erreur métier est survenue."

    def __init__(
        self,
        code: str,
        message: str = "",
        http_status: int | None = None,
        details: dict | None = None,
    ):
        self.code = code
        self.message = message or code
        self.details = details or {}
        if http_status is not None:
            self.status_code = http_status
        super().__init__(detail=self.message)
