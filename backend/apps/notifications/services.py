"""Destinataires, rédaction et regroupement des notifications (phase 10)."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.core import metrics
from apps.core.events import DomainEvent
from apps.notifications.models import Notification
from apps.organizations.models import OrganizationMember
from apps.projects.models import Project, ProjectMember
from apps.users.models import User
from apps.users.roles import Role

logger = logging.getLogger("kemta.notifications")

MAX_TRACKED_ITEMS = 10

ALL_MEMBERS = "all_members"
FINANCE_DECIDERS = frozenset({Role.PROJECT_OWNER, Role.FINANCE})
PROJECT_MANAGERS = frozenset({Role.PROJECT_OWNER, Role.ENGINEER})

# Rôles de projet destinataires de chaque événement (le propriétaire d'organisation est
# toujours destinataire : il pilote le périmètre).
RECIPIENT_ROLES: dict[str, frozenset | str] = {
    DomainEvent.MILESTONE_VALIDATED: ALL_MEMBERS,
    DomainEvent.EXPENSE_SUBMITTED: FINANCE_DECIDERS,
    DomainEvent.EVIDENCE_REJECTED: PROJECT_MANAGERS,
    DomainEvent.BUDGET_THRESHOLD_REACHED: frozenset({*FINANCE_DECIDERS, Role.INVESTOR}),
    DomainEvent.PROJECT_DELAYED: PROJECT_MANAGERS,
}


def recipients_for(
    event_type: str, project: Project, *, actor_id: int | None, extra_ids: list[int]
) -> list[int]:
    """Identifiants des destinataires — un seul jeu de requêtes, quelle que soit l'équipe."""
    roles = RECIPIENT_ROLES[event_type]
    members = ProjectMember.objects.filter(project=project, is_active=True)
    if roles != ALL_MEMBERS:
        members = members.filter(role__in=roles)
    ids = set(members.values_list("user_id", flat=True))

    # Le pilotage de l'organisation est toujours informé.
    ids.add(project.organization.owner_id)
    ids.update(
        OrganizationMember.objects.filter(
            organization_id=project.organization_id, is_active=True, role=Role.ORG_OWNER
        ).values_list("user_id", flat=True)
    )
    ids.update(extra_ids)
    ids.discard(actor_id)
    ids.discard(None)
    return sorted(User.objects.filter(pk__in=ids, is_active=True).values_list("pk", flat=True))


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _fcfa(value) -> str:
    try:
        return f"{int(Decimal(str(value))):,}".replace(",", " ") + " FCFA"
    except Exception:
        return ""


def group_key_for(event_type: str, project: Project, payload: dict) -> str:
    key = f"{event_type}:{project.pk}"
    if event_type == DomainEvent.BUDGET_THRESHOLD_REACHED:
        key += f":{payload.get('threshold_percent', '')}"
    return key


def render(event_type: str, project: Project, payload: dict, count: int) -> tuple[str, str]:
    """Titre et détail, adaptés au nombre d'événements regroupés."""
    name = project.name
    if event_type == DomainEvent.MILESTONE_VALIDATED:
        title = (
            f"Jalon validé : {payload.get('title', '')}"
            if count == 1
            else f"{count} jalons validés"
        )
        return title[:200], f"Projet {name}. Dernier jalon : {payload.get('title', '')}"[:500]
    if event_type == DomainEvent.EXPENSE_SUBMITTED:
        title = (
            f"Dépense à approuver : {payload.get('title', '')}"
            if count == 1
            else f"{count} dépenses à approuver"
        )
        detail = f"Projet {name}. Dernière : {payload.get('title', '')}"
        if payload.get("amount") is not None:
            detail += f" ({_fcfa(payload['amount'])})"
        return title[:200], detail[:500]
    if event_type == DomainEvent.EVIDENCE_REJECTED:
        title = "Preuve rejetée" if count == 1 else f"{count} preuves rejetées"
        detail = f"Projet {name}."
        if payload.get("comment"):
            detail += f" Motif : {payload['comment']}"
        return title, detail[:500]
    if event_type == DomainEvent.BUDGET_THRESHOLD_REACHED:
        threshold = payload.get("threshold_percent")
        title = (
            f"Budget dépassé sur {name}"
            if str(threshold) == "100"
            else f"Budget consommé à {threshold} % sur {name}"
        )
        detail = ""
        if payload.get("committed") is not None:
            detail = f"Engagé : {_fcfa(payload['committed'])} sur {_fcfa(payload.get('planned'))}."
        return title[:200], detail
    if event_type == DomainEvent.PROJECT_DELAYED:
        parts = []
        if payload.get("tasks_late"):
            parts.append(_plural(payload["tasks_late"], "tâche en retard", "tâches en retard"))
        if payload.get("milestones_late"):
            parts.append(_plural(payload["milestones_late"], "jalon en retard", "jalons en retard"))
        return f"Retard sur {name}"[:200], ", ".join(parts)[:500]
    return event_type, ""


def _apply(user_id: int, project: Project, event: dict, group_key: str) -> str:
    """Crée ou regroupe la notification d'un destinataire ; renvoie `created`/`grouped`/`skipped`."""
    event_type = event["event_type"]
    payload = event.get("payload") or {}
    dedupe_key = event.get("dedupe_key") or ""
    now = timezone.now()
    item = {"entity_type": event.get("entity_type", ""), "entity_id": event.get("entity_id", "")}

    if dedupe_key and Notification.objects.filter(user_id=user_id, dedupe_key=dedupe_key).exists():
        return "skipped"

    with transaction.atomic():
        open_notification = (
            Notification.objects.select_for_update()
            .filter(user_id=user_id, group_key=group_key, read_at__isnull=True)
            .first()
        )
        if open_notification is not None:
            count = open_notification.count + 1
            items = ([*open_notification.data.get("items", []), item])[-MAX_TRACKED_ITEMS:]
            title, body = render(event_type, project, payload, count)
            Notification.objects.filter(pk=open_notification.pk).update(
                count=F("count") + 1,
                title=title,
                body=body,
                data={"latest": payload, "items": items},
                last_event_at=now,
                updated_at=now,
            )
            return "grouped"

        title, body = render(event_type, project, payload, 1)
        try:
            with transaction.atomic():
                Notification.objects.create(
                    user_id=user_id,
                    project=project,
                    event_type=event_type,
                    group_key=group_key,
                    dedupe_key=dedupe_key,
                    title=title,
                    body=body,
                    data={"latest": payload, "items": [item]},
                    last_event_at=now,
                )
        except IntegrityError:
            # Un événement concurrent vient de créer la notification ouverte : on l'incrémente.
            Notification.objects.filter(
                user_id=user_id, group_key=group_key, read_at__isnull=True
            ).update(count=F("count") + 1, last_event_at=now, updated_at=now)
            return "grouped"
        return "created"


def dispatch(event: dict) -> dict:
    """Transforme un événement métier en notifications. Retourne un bilan pour les logs/tests."""
    project = (
        Project.all_objects.select_related("organization").filter(pk=event["project_id"]).first()
    )
    if project is None or project.deleted_at is not None:
        logger.info("Événement %s ignoré : projet indisponible", event["event_type"])
        return {"created": 0, "grouped": 0, "skipped": 0, "recipients": 0}

    payload = event.get("payload") or {}
    group_key = group_key_for(event["event_type"], project, payload)
    recipients = recipients_for(
        event["event_type"],
        project,
        actor_id=event.get("actor_id"),
        extra_ids=event.get("recipient_ids") or [],
    )
    outcome = {"created": 0, "grouped": 0, "skipped": 0, "recipients": len(recipients)}
    with transaction.atomic():
        # Tout ou rien : un échec en cours de route annule le lot, le retry repart de zéro
        # sans double comptage.
        for user_id in recipients:
            outcome[_apply(user_id, project, event, group_key)] += 1
    if outcome["created"] or outcome["grouped"]:
        metrics.incr(
            "kemta_notifications_total",
            outcome["created"] + outcome["grouped"],
            event=event["event_type"],
        )
    return outcome
