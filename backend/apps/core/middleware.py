from __future__ import annotations

import logging
import time

from django.conf import settings

from . import metrics
from .logging_utils import new_request_id, set_request_id

slow_logger = logging.getLogger("kemta.perf")

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


class MetricsMiddleware:
    """Mesure chaque requête : volume, durée, erreurs 5xx et refus de rate limiting.

    Le libellé de route est le **motif** de l'URL (`api/projects/<int:pk>/`), jamais l'URL
    réelle : la cardinalité reste bornée et aucun identifiant n'entre dans une métrique.
    Les requêtes lentes (> `SLOW_REQUEST_SECONDS`) sont aussi journalisées avec leur
    `request_id`, ce qui permet de retrouver la trace complète.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        response = self.get_response(request)
        elapsed = time.perf_counter() - started

        match = getattr(request, "resolver_match", None)
        route = match.route if match is not None and match.route else "unmatched"
        method = request.method or "GET"
        status_code = response.status_code

        metrics.incr("kemta_http_requests_total", method=method, route=route, status=status_code)
        metrics.observe("kemta_http_request_duration_seconds", elapsed, method=method, route=route)
        if status_code >= 500:
            metrics.incr("kemta_http_errors_total", route=route)
        if status_code == 429:
            metrics.incr("kemta_rate_limited_total", route=route)

        slow_limit = getattr(settings, "SLOW_REQUEST_SECONDS", 1.5)
        if elapsed > slow_limit:
            slow_logger.warning(
                "Requête lente : %s %s en %.0f ms (statut %s)",
                method,
                route,
                elapsed * 1000,
                status_code,
            )
        return response


class SecurityHeadersMiddleware:
    """En-têtes de sécurité complémentaires (revue de sécurité, phase 11).

    * `Permissions-Policy` : l'application n'a besoin que de la caméra et de la géolocalisation ;
    * `Cache-Control: no-store` par défaut sur l'API : les réponses JSON sont privées et
      aucun cache partagé ne doit les conserver (les médias posent leur propre en-tête).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault(
            "Permissions-Policy", "camera=(self), geolocation=(self), microphone=(), payment=()"
        )
        if request.path.startswith("/api/") and "Cache-Control" not in response:
            response["Cache-Control"] = "no-store"
        return response
