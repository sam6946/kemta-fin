"""Endpoints agrégés : dashboard d'un projet et espace de travail (phase 8)."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.dashboard import cache as dashboard_cache
from apps.dashboard.services import build_project_dashboard, build_workspace
from apps.projects.access import accessible_projects


class ProjectDashboardView(APIView):
    """`GET /api/projects/{id}/dashboard/` — une requête HTTP pour tout l'écran principal.

    * projet hors périmètre → **404** (jamais 403 : on ne révèle pas son existence) ;
    * la réponse dépend du rôle de l'utilisateur sur le projet (finance, activité, preuves) ;
    * en-tête `X-Cache: HIT|MISS` et `X-Dashboard-Generated-At` pour mesurer l'efficacité ;
    * aucune boucle d'interrogation côté client : l'écran se rafraîchit à la demande, au
      retour dans l'onglet et après une action (voir `docs/flows/dashboard.md`).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        project = get_object_or_404(
            accessible_projects(request.user).select_related("organization"), pk=pk
        )
        cached = dashboard_cache.get_cached(project.pk, request.user.pk)
        if cached is not None:
            response = Response(cached)
            response["X-Cache"] = "HIT"
            return response

        payload = build_project_dashboard(request.user, project)
        dashboard_cache.set_cached(project.pk, request.user.pk, payload)
        response = Response(payload)
        response["X-Cache"] = "MISS"
        return response


class WorkspaceView(APIView):
    """`GET /api/workspace/` — vue transverse : chantiers, tâches assignées, files de décision."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        payload = build_workspace(request.user, accessible_projects(request.user))
        from apps.notifications.views import unread_count

        payload["unread_notifications"] = unread_count(request.user)
        payload["generated_at"] = timezone.now()
        return Response(payload)
