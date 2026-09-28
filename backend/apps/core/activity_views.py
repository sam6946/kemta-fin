"""Consultation du journal d'activité (phase 9, MVP-012).

Le journal est **en lecture seule** : aucun verbe d'écriture n'est exposé (405). Les accès
sont bornés par rôle :

* journal d'un projet → capacité `view_activity` sur ce projet (403 sinon, 404 hors périmètre) ;
* journal global (authentification, sessions, toutes organisations) → `view_auth_logs`
  (administration plateforme) ;
* « mon activité » → chaque utilisateur voit ses propres événements de sécurité.

L'adresse IP et l'appareil ne sont exposés qu'à l'administration (`view_auth_logs`) : ils sont
conservés pour l'enquête sur incident, pas pour être affichés à l'équipe projet.
"""

from __future__ import annotations

from datetime import date, datetime, time

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import KemtaAPIError
from apps.core.models import ActivityLog
from apps.core.pagination import DefaultPagination
from apps.projects.access import accessible_projects, has_project_capability
from apps.users.roles import Capability, user_has

# Familles d'actions : le frontend propose des filtres lisibles, pas 70 codes techniques.
ACTION_GROUPS: dict[str, tuple[str, ...]] = {
    "auth": (
        "USER_REGISTERED",
        "OTP_SENT",
        "OTP_VERIFIED",
        "OTP_FAILED",
        "OTP_RESEND",
        "LOGIN_SUCCESS",
        "LOGIN_FAILED",
        "ACCOUNT_LOCKED",
        "LOGOUT",
        "TOKEN_REFRESHED",
        "TOKEN_REFRESH_REJECTED",
        "PASSWORD_RESET_REQUESTED",
        "PASSWORD_RESET_FAILED",
        "PASSWORD_RESET_CONFIRMED",
        "PASSWORD_RESET_DENIED",
        "PASSWORD_CHANGED",
        "EMAIL_ADDED",
        "EMAIL_VERIFIED",
    ),
    "project": (
        "ORG_CREATED",
        "ORG_UPDATED",
        "PROJECT_CREATED",
        "PROJECT_UPDATED",
        "PROJECT_ARCHIVED",
    ),
    "members": ("MEMBER_ADDED", "MEMBER_ROLE_CHANGED", "MEMBER_REMOVED"),
    "planning": (
        "MILESTONE_CREATED",
        "MILESTONE_UPDATED",
        "MILESTONE_DELETED",
        "TASK_CREATED",
        "TASK_UPDATED",
        "TASK_STATUS_CHANGED",
        "TASK_DELETED",
    ),
    "evidences": (
        "EVIDENCE_CAPTURED",
        "EVIDENCE_VALIDATED",
        "EVIDENCE_REJECTED",
        "EVIDENCE_FLAGGED",
        "EVIDENCE_REOPENED",
    ),
    "finance": (
        "BUDGET_LINE_CREATED",
        "BUDGET_LINE_UPDATED",
        "BUDGET_LINE_DELETED",
        "EXPENSE_CREATED",
        "EXPENSE_UPDATED",
        "EXPENSE_SUBMITTED",
        "EXPENSE_APPROVED",
        "EXPENSE_REJECTED",
        "EXPENSE_CANCELLED",
        "EXPENSE_RECEIPT_ATTACHED",
        "PAYMENT_RECORDED",
        "PAYMENT_CANCELLED",
        "ADJUSTMENT_RECORDED",
        "BUDGET_THRESHOLD_REACHED",
        "BUDGET_EXCEEDED",
    ),
    "system": ("PROJECT_DELAY_DETECTED", "TASK_FAILED"),
}
GROUP_LABELS = {
    "auth": "Authentification",
    "project": "Projet et organisation",
    "members": "Membres et rôles",
    "planning": "Planning",
    "evidences": "Preuves terrain",
    "finance": "Finances",
    "system": "Système",
}
PROJECT_GROUPS = ("project", "members", "planning", "evidences", "finance", "system")
MAX_PAGE_SIZE = 100


def group_of(action: str) -> str:
    for group, actions in ACTION_GROUPS.items():
        if action in actions:
            return group
    return "other"


def _actor(event: ActivityLog) -> dict | None:
    actor = event.actor
    if actor is None:
        return None
    return {
        "id": actor.pk,
        "name": f"{actor.first_name} {actor.last_name}".strip(),
        "role": actor.role,
    }


def serialize_event(event: ActivityLog, *, include_network: bool) -> dict:
    data = {
        "id": event.pk,
        "action": event.action,
        "action_label": event.get_action_display(),
        "group": group_of(event.action),
        "actor": _actor(event),
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "project": event.project_id,
        "metadata": event.metadata,
        "created_at": event.created_at,
    }
    if include_network:
        data["ip_address"] = event.ip_address
        data["user_agent"] = event.user_agent
    return data


def _parse_day(value: str, name: str) -> date:
    parsed = parse_date(value)
    if parsed is None:
        raise KemtaAPIError(
            "invalid_parameter", f"« {name} » doit être une date AAAA-MM-JJ.", details={name: value}
        )
    return parsed


def apply_filters(queryset, params, *, allowed_groups):
    """Filtres communs : `group`, `action`, `entity_type`, `actor`, `since`, `until`."""
    group = params.get("group")
    if group:
        if group not in allowed_groups:
            raise KemtaAPIError(
                "invalid_parameter", "Famille d'événements inconnue.", details={"group": group}
            )
        queryset = queryset.filter(action__in=ACTION_GROUPS[group])
    elif allowed_groups is not None:
        allowed_actions = [action for g in allowed_groups for action in ACTION_GROUPS[g]]
        queryset = queryset.filter(action__in=allowed_actions)

    action = params.get("action")
    if action:
        values = [value.upper() for value in action.split(",") if value]
        unknown = [value for value in values if value not in ActivityLog.Action.values]
        if unknown:
            raise KemtaAPIError(
                "invalid_parameter", "Action inconnue.", details={"unknown": unknown}
            )
        queryset = queryset.filter(action__in=values)

    if params.get("entity_type"):
        queryset = queryset.filter(entity_type=params["entity_type"])
    if params.get("entity_id"):
        queryset = queryset.filter(entity_id=params["entity_id"])
    if params.get("actor"):
        queryset = queryset.filter(actor_id=params["actor"])

    tz = timezone.get_current_timezone()
    if params.get("since"):
        since = _parse_day(params["since"], "since")
        queryset = queryset.filter(
            created_at__gte=timezone.make_aware(datetime.combine(since, time.min), tz)
        )
    if params.get("until"):
        until = _parse_day(params["until"], "until")
        queryset = queryset.filter(
            created_at__lte=timezone.make_aware(datetime.combine(until, time.max), tz)
        )
    return queryset


class _Pagination(DefaultPagination):
    max_page_size = MAX_PAGE_SIZE


def _paginate(request, queryset, *, include_network: bool):
    paginator = _Pagination()
    page = paginator.paginate_queryset(queryset.order_by("-created_at", "-id"), request)
    return paginator.get_paginated_response(
        [serialize_event(event, include_network=include_network) for event in page]
    )


class ProjectActivityView(APIView):
    """`GET /api/projects/{id}/activity/` — journal paginé d'un projet (lecture seule)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        project = get_object_or_404(accessible_projects(request.user), pk=pk)
        if not has_project_capability(request.user, project, Capability.VIEW_ACTIVITY):
            raise KemtaAPIError(
                "permission_denied",
                "Votre rôle ne permet pas de consulter le journal d'activité de ce projet.",
                http_status=403,
            )
        queryset = ActivityLog.objects.filter(project=project).select_related("actor")
        queryset = apply_filters(queryset, request.query_params, allowed_groups=PROJECT_GROUPS)
        response = _paginate(
            request, queryset, include_network=user_has(request.user, Capability.VIEW_AUTH_LOGS)
        )
        return response


class GlobalActivityView(APIView):
    """`GET /api/activity/` — journal global, **administration plateforme uniquement**."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not user_has(request.user, Capability.VIEW_AUTH_LOGS):
            raise KemtaAPIError(
                "permission_denied",
                "Le journal global est réservé à l'administration de la plateforme.",
                http_status=403,
            )
        queryset = ActivityLog.objects.select_related("actor")
        if request.query_params.get("project"):
            queryset = queryset.filter(project_id=request.query_params["project"])
        queryset = apply_filters(
            queryset, request.query_params, allowed_groups=tuple(ACTION_GROUPS)
        )
        return _paginate(request, queryset, include_network=True)


class MyActivityView(APIView):
    """`GET /api/activity/mine/` — mes événements de sécurité (connexions, mot de passe)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = ActivityLog.objects.filter(
            actor=request.user, action__in=ACTION_GROUPS["auth"]
        ).select_related("actor")
        queryset = apply_filters(queryset, request.query_params, allowed_groups=("auth",))
        return _paginate(request, queryset, include_network=True)


class ActivityMetaView(APIView):
    """`GET /api/activity/meta/` — familles d'événements pour construire les filtres."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        admin = user_has(request.user, Capability.VIEW_AUTH_LOGS)
        groups = tuple(ACTION_GROUPS) if admin else PROJECT_GROUPS
        return Response(
            {
                "groups": [
                    {
                        "code": group,
                        "label": GROUP_LABELS[group],
                        "actions": [
                            {"code": action, "label": ActivityLog.Action(action).label}
                            for action in ACTION_GROUPS[group]
                        ],
                    }
                    for group in groups
                ]
            }
        )
