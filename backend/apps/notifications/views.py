"""API des notifications in-app (MVP-014).

Aucune donnée d'un autre utilisateur n'est jamais accessible : tout est filtré sur
`request.user`. Une notification dont le projet n'est plus accessible à son destinataire
(retiré de l'équipe) est masquée, pour ne pas révéler l'existence du projet.
"""

from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import DefaultPagination
from apps.notifications.models import Notification
from apps.projects.access import accessible_projects


def visible_notifications(user):
    return Notification.objects.filter(user=user).filter(
        Q(project__isnull=True) | Q(project__in=accessible_projects(user))
    )


def serialize(notification: Notification) -> dict:
    return {
        "id": notification.pk,
        "event_type": notification.event_type,
        "title": notification.title,
        "body": notification.body,
        "count": notification.count,
        "project": notification.project_id,
        "is_read": notification.read_at is not None,
        "read_at": notification.read_at,
        "last_event_at": notification.last_event_at,
        "created_at": notification.created_at,
        "items": (notification.data or {}).get("items", []),
    }


def unread_count(user) -> int:
    return visible_notifications(user).filter(read_at__isnull=True).count()


class NotificationListView(APIView):
    """`GET /api/notifications/` — liste paginée (`?unread=1` pour les non lues)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = visible_notifications(request.user).select_related("project")
        if request.query_params.get("unread") in {"1", "true", "True"}:
            queryset = queryset.filter(read_at__isnull=True)
        event_type = request.query_params.get("event_type")
        if event_type:
            queryset = queryset.filter(event_type=event_type)

        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request)
        payload = paginator.get_paginated_response([serialize(n) for n in page]).data
        payload["unread_count"] = unread_count(request.user)
        return Response(payload)


class NotificationUnreadCountView(APIView):
    """`GET /api/notifications/unread-count/` — pastille de la cloche (une requête légère)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"unread_count": unread_count(request.user)})


class NotificationReadView(APIView):
    """`POST /api/notifications/{id}/read/` — marque une notification comme lue."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        notification = get_object_or_404(visible_notifications(request.user), pk=pk)
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at", "updated_at"])
        return Response(serialize(notification))


class NotificationReadAllView(APIView):
    """`POST /api/notifications/read-all/` — tout marquer comme lu."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        now = timezone.now()
        updated = Notification.objects.filter(user=request.user, read_at__isnull=True).update(
            read_at=now, updated_at=now
        )
        return Response({"marked": updated, "unread_count": unread_count(request.user)})
