"""Healthcheck : vérifie l'application, PostgreSQL et Redis."""

from __future__ import annotations

import time

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.utils import OperationalError
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

CACHE_PROBE_KEY = "kemta:healthcheck"
CACHE_PROBE_TTL = 5


def check_database() -> tuple[bool, int]:
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except OperationalError:
        return False, _ms(started)
    return True, _ms(started)


def check_redis() -> tuple[bool, int]:
    started = time.perf_counter()
    try:
        cache.set(CACHE_PROBE_KEY, "1", CACHE_PROBE_TTL)
        ok = cache.get(CACHE_PROBE_KEY) == "1"
    except Exception:
        return False, _ms(started)
    return ok, _ms(started)


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


class HealthView(APIView):
    """`GET /api/health/` — n'expose aucun secret ni donnée métier."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request):
        db_ok, db_ms = check_database()
        cache_ok, cache_ms = check_redis()
        checks = {
            "application": "ok",
            "database": "ok" if db_ok else "down",
            "cache": "ok" if cache_ok else "down",
        }
        healthy = db_ok and cache_ok
        payload = {
            "status": "ok" if healthy else "degraded",
            "app": "ok",
            "database": checks["database"],
            "redis": checks["cache"],
            "version": settings.APP_VERSION,
            "environment": getattr(settings, "ENV", "local"),
            "checks_ms": {"database": db_ms, "redis": cache_ms},
        }
        return Response(
            payload, status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
        )
