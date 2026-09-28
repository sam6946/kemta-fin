"""Tâches Celery des notifications (phase 10)."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("kemta.tasks")

# Backoff exponentiel : 10 s, 20 s, 40 s, 80 s, 160 s puis abandon (échec journalisé).
RETRY_BASE_SECONDS = 10


@shared_task(bind=True, max_retries=5, acks_late=True)
def dispatch_domain_event(self, event: dict) -> dict:
    """Crée les notifications d'un événement métier — **tâche critique**, avec retry."""
    from apps.notifications.services import dispatch

    try:
        return dispatch(event)
    except Exception as exc:
        logger.warning(
            "Événement %s : échec (tentative %s/%s)",
            event.get("event_type"),
            self.request.retries + 1,
            self.max_retries + 1,
        )
        raise self.retry(exc=exc, countdown=RETRY_BASE_SECONDS * 2**self.request.retries) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def detect_project_delays(self) -> dict:
    """Détecte les projets actifs en retard et émet `ProjectDelayed` (une fois par jour et projet).

    Règle déterministe, identique à l'écran de planification : une tâche ou un jalon non
    terminal dont la date prévue est dépassée. La clé de déduplication `delay:{projet}:{date}`
    rend la tâche idempotente : la relancer le même jour ne produit aucune notification.
    """
    from django.db.models import Count

    from apps.core.activity import log_event
    from apps.core.events import DomainEvent, emit
    from apps.core.models import ActivityLog
    from apps.projects.models import (
        FINAL_STATUSES,
        FINAL_TASK_STATUSES,
        Milestone,
        Project,
        ProjectStatus,
        Task,
    )

    today = timezone.localdate()
    late_tasks = dict(
        Task.objects.filter(project__status=ProjectStatus.ACTIVE, planned_end_date__lt=today)
        .exclude(status__in=FINAL_TASK_STATUSES)
        .values_list("project_id")
        .annotate(total=Count("id"))
    )
    late_milestones = dict(
        Milestone.objects.filter(project__status=ProjectStatus.ACTIVE, planned_date__lt=today)
        .exclude(status__in=FINAL_STATUSES)
        .values_list("project_id")
        .annotate(total=Count("id"))
    )
    project_ids = set(late_tasks) | set(late_milestones)
    projects = Project.objects.filter(pk__in=project_ids).select_related("organization")

    emitted = 0
    for project in projects:
        payload = {
            "tasks_late": late_tasks.get(project.pk, 0),
            "milestones_late": late_milestones.get(project.pk, 0),
            "reference_date": today.isoformat(),
        }
        emit(
            DomainEvent.PROJECT_DELAYED,
            project=project,
            entity_type="Project",
            entity_id=project.pk,
            payload=payload,
            dedupe_key=f"delay:{project.pk}:{today.isoformat()}",
        )
        already_logged = ActivityLog.objects.filter(
            action="PROJECT_DELAY_DETECTED", project=project, created_at__date=today
        ).exists()
        if not already_logged:
            log_event(
                "PROJECT_DELAY_DETECTED",
                entity_type="Project",
                entity_id=project.pk,
                organization=project.organization,
                project=project,
                metadata=payload,
            )
        emitted += 1
    logger.info("Retards détectés sur %s projet(s)", emitted)
    return {"projects": emitted, "reference_date": today.isoformat()}


@shared_task
def purge_old_notifications(days: int | None = None) -> int:
    """Supprime les notifications **lues** depuis plus de `NOTIFICATION_RETENTION_DAYS` jours."""
    from apps.notifications.models import Notification

    retention = days if days is not None else settings.NOTIFICATION_RETENTION_DAYS
    deleted, _ = Notification.objects.filter(
        read_at__isnull=False, read_at__lt=timezone.now() - timedelta(days=retention)
    ).delete()
    logger.info("Notifications lues purgées : %s", deleted)
    return deleted
