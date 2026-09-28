from __future__ import annotations

from .logging_utils import new_request_id, set_request_id

HEADER = "X-Request-ID"


class RequestIDMiddleware:
    """Attribue un `request_id` à chaque requête pour corréler logs et réponses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get(HEADER) or new_request_id()
        request.id = request_id
        set_request_id(request_id)
        try:
            response = self.get_response(request)
        finally:
            set_request_id(None)
        response[HEADER] = request_id
        return response
