"""Métadonnées partagées avec le frontend (source unique des rôles)."""

from django.core.cache import cache
from django.urls import path
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.projects.models import MilestoneStatus, ProjectStatus, TaskStatus
from apps.users.roles import roles_payload

ROLES_CACHE_KEY = "kemta:meta:roles"
ROLES_CACHE_TTL = 3600


class RolesMetaView(APIView):
    """`GET /api/meta/roles/` — les 9 rôles et leurs capacités."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request):
        payload = cache.get(ROLES_CACHE_KEY)
        if payload is None:
            payload = roles_payload()
            cache.set(ROLES_CACHE_KEY, payload, ROLES_CACHE_TTL)
        return Response({"results": payload})


def _choices(enum) -> list[dict[str, str]]:
    return [{"value": value, "label": label} for value, label in enum.choices]


class StatusMetaView(APIView):
    """`GET /api/meta/status/` — statuts et libellés officiels, par domaine.

    Le frontend n'écrit jamais ses propres listes de statuts : il affiche celles-ci, ce qui
    garantit qu'un statut ajouté côté backend apparaît immédiatement dans les écrans.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "project": _choices(ProjectStatus),
                "milestone": _choices(MilestoneStatus),
                "task": _choices(TaskStatus),
            }
        )


urlpatterns = [
    path("roles/", RolesMetaView.as_view(), name="meta-roles"),
    path("status/", StatusMetaView.as_view(), name="meta-status"),
]
