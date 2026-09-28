"""Exploitation : métriques Prometheus et état des files (phase 10 / 11).

* `GET /api/ops/metrics/` — format Prometheus. Accessible avec le jeton `METRICS_TOKEN`
  (scraper) **ou** par un compte disposant de `view_operations` (administration) ;
* `GET /api/ops/status/` — synthèse JSON pour l'écran d'exploitation : base, cache/Redis,
  longueur des files Celery, exécutions de tâches, échecs récents, compteurs de métriques.

Aucune donnée métier (noms, montants, numéros) n'est exposée ici.
"""

from __future__ import annotations

import hmac
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db.models import Count
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import metrics
from apps.core.exceptions import KemtaAPIError
from apps.core.models import TaskRun
from apps.core.views import check_database, check_redis
from apps.users.authentication import KemtaJWTAuthentication
from apps.users.roles import Capability, user_has

logger = logging.getLogger("kemta.ops")

CELERY_QUEUES = ("celery",)


def _bearer(request) -> str:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _operations_user(request):
    """Utilisateur JWT autorisé à voir l'exploitation, ou `None`."""
    try:
        user = request.user
    except AuthenticationFailed:
        return None
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user if user_has(user, Capability.VIEW_OPERATIONS) else None


class _OpsBaseView(APIView):
    """Authentification manuelle : jeton de scraper **ou** JWT d'administration."""

    permission_classes = [AllowAny]
    authentication_classes = [KemtaJWTAuthentication]
    allow_scraper_token = False

    def perform_authentication(self, request):
        """Différée : le jeton de scraper n'est pas un JWT, il ne doit pas être décodé comme tel."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        token = _bearer(request)
        configured = settings.METRICS_TOKEN
        if (
            self.allow_scraper_token
            and configured
            and token
            and hmac.compare_digest(token.encode(), configured.encode())
        ):
            return
        if _operations_user(request) is None:
            # 404 plutôt que 403 : l'endpoint n'a pas à être découvrable sans droit.
            raise KemtaAPIError("not_found", "Ressource introuvable.", http_status=404)


class MetricsView(_OpsBaseView):
    """`GET /api/ops/metrics/` — exposition Prometheus."""

    allow_scraper_token = True

    def get(self, request):
        return HttpResponse(
            metrics.render_prometheus(), content_type="text/plain; version=0.0.4; charset=utf-8"
        )


def queue_lengths() -> dict[str, int | None]:
    """Nombre de tâches en attente par file Celery (Redis). `None` si indisponible."""
    lengths: dict[str, int | None] = dict.fromkeys(CELERY_QUEUES)
    if settings.CELERY_TASK_ALWAYS_EAGER:
        return lengths
    try:
        import redis

        client = redis.Redis.from_url(
            settings.CELERY_BROKER_URL, socket_connect_timeout=1, socket_timeout=1
        )
        for queue in CELERY_QUEUES:
            lengths[queue] = int(client.llen(queue))
    except Exception:
        logger.warning("Longueur des files Celery indisponible", exc_info=True)
    return lengths


def worker_count() -> int | None:
    """Workers Celery répondant à un ping (1 s max) ; `None` si le broker est injoignable."""
    if settings.CELERY_TASK_ALWAYS_EAGER:
        return None
    try:
        from config.celery import app

        return len(app.control.inspect(timeout=1).ping() or {})
    except Exception:
        return None


class StatusView(_OpsBaseView):
    """`GET /api/ops/status/` — état des composants, des files et des tâches."""

    def get(self, request):
        db_ok, db_ms = check_database()
        cache_ok, cache_ms = check_redis()
        since = timezone.now() - timedelta(hours=24)
        by_state = dict(
            TaskRun.objects.filter(started_at__gte=since)
            .values_list("state")
            .annotate(total=Count("id"))
        )
        failures = [
            {
                "task": run.name,
                "error": run.error,
                "retries": run.retries,
                "at": run.finished_at or run.started_at,
            }
            for run in TaskRun.objects.filter(
                state=TaskRun.State.FAILURE, started_at__gte=since
            ).order_by("-started_at")[:10]
        ]
        started = time.perf_counter()
        return Response(
            {
                "generated_at": timezone.now(),
                "version": settings.APP_VERSION,
                "components": {
                    "database": {"ok": db_ok, "ms": db_ms},
                    "cache": {"ok": cache_ok, "ms": cache_ms},
                },
                "celery": {
                    "eager": settings.CELERY_TASK_ALWAYS_EAGER,
                    "queues": queue_lengths(),
                    "workers_online": worker_count()
                    if request.query_params.get("workers")
                    else None,
                },
                "tasks_24h": {
                    "started": by_state.get(TaskRun.State.STARTED, 0),
                    "success": by_state.get(TaskRun.State.SUCCESS, 0),
                    "retry": by_state.get(TaskRun.State.RETRY, 0),
                    "failure": by_state.get(TaskRun.State.FAILURE, 0),
                    "recent_failures": failures,
                },
                "metrics": metrics.summary(),
                "probe_ms": int((time.perf_counter() - started) * 1000),
            }
        )
