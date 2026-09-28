"""Services de planification : jalons et tâches (MVP-006, réutilisés en phase 6).

Ces services portent les règles d'écriture (permissions, validation, recalcul d'avancement,
journalisation) hors des vues : l'API interactive et la reprise hors ligne
(`POST /api/sync/batch/`) appliquent ainsi **exactement** les mêmes règles.
"""

from __future__ import annotations

from decimal import Decimal

from apps.core.activity import log_event
from apps.core.exceptions import KemtaAPIError
from apps.projects.access import has_project_capability
from apps.projects.models import Milestone, Task
from apps.projects.progress import recalculate_project_progress
from apps.projects.serializers import MilestoneSerializer, TaskSerializer
from apps.users.roles import Capability

# Champs qu'un membre sans `MANAGE_SCHEDULE` mais avec `UPDATE_TASK` peut modifier sur la tâche
# dont il est responsable : l'exécution terrain, pas la planification.
FIELD_UPDATE_FIELDS = frozenset(
    {"status", "progress", "actual_start_date", "actual_end_date", "description"}
)


def _permission_denied(message: str) -> KemtaAPIError:
    return KemtaAPIError("permission_denied", message, http_status=403)


def create_milestone(*, project, actor, data, request=None) -> tuple[Milestone, Decimal]:
    if not has_project_capability(actor, project, Capability.MANAGE_SCHEDULE):
        raise _permission_denied("Vous n'avez pas la permission de planifier ce projet.")

    serializer = MilestoneSerializer(data=data, context={"request": request, "project": project})
    serializer.is_valid(raise_exception=True)
    milestone = serializer.save(project=project, created_by=actor)
    progress = recalculate_project_progress(project)
    log_event(
        "MILESTONE_CREATED",
        actor=actor,
        entity_type="Milestone",
        entity_id=milestone.pk,
        organization=project.organization,
        project=project,
        metadata={
            "title": milestone.title,
            "status": milestone.status,
            "planned_date": str(milestone.planned_date) if milestone.planned_date else None,
            "weight": str(milestone.weight),
        },
        request=request,
    )
    return milestone, progress


def update_milestone(
    *, milestone: Milestone, actor, data, request=None
) -> tuple[Milestone, Decimal]:
    project = milestone.project
    if not has_project_capability(actor, project, Capability.MANAGE_SCHEDULE):
        raise _permission_denied("Vous n'avez pas la permission de modifier ce jalon.")

    before = {"status": milestone.status, "planned_date": str(milestone.planned_date or "")}
    serializer = MilestoneSerializer(
        milestone, data=data, partial=True, context={"request": request, "project": project}
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()
    progress = recalculate_project_progress(project)
    changed = {
        field: {"old": before[field], "new": str(getattr(milestone, field) or "")}
        for field in before
        if before[field] != str(getattr(milestone, field) or "")
    }
    log_event(
        "MILESTONE_UPDATED",
        actor=actor,
        entity_type="Milestone",
        entity_id=milestone.pk,
        organization=project.organization,
        project=project,
        metadata={"changed": changed, "project_progress": str(progress)},
        request=request,
    )
    return milestone, progress


def create_task(*, project, actor, data, request=None) -> tuple[Task, Decimal]:
    if not has_project_capability(actor, project, Capability.MANAGE_SCHEDULE):
        raise _permission_denied("Vous n'avez pas la permission de créer une tâche sur ce projet.")

    serializer = TaskSerializer(data=data, context={"request": request, "project": project})
    serializer.is_valid(raise_exception=True)
    task = serializer.save(project=project, created_by=actor)
    progress = recalculate_project_progress(project)
    log_event(
        "TASK_CREATED",
        actor=actor,
        entity_type="Task",
        entity_id=task.pk,
        organization=project.organization,
        project=project,
        metadata={
            "title": task.title,
            "status": task.status,
            "milestone": task.milestone_id,
            "assignee": task.assignee_id,
            "planned_end_date": str(task.planned_end_date) if task.planned_end_date else None,
            "project_progress": str(progress),
        },
        request=request,
    )
    return task, progress


def update_task(*, task: Task, actor, data, request=None) -> tuple[Task, Decimal]:
    """Mise à jour de tâche : planificateur, ou responsable désigné pour l'exécution seule."""
    fields = set(data.keys())
    is_assignee = task.assignee_id == actor.pk
    may_manage = has_project_capability(actor, task.project, Capability.MANAGE_SCHEDULE)
    may_update = has_project_capability(actor, task.project, Capability.UPDATE_TASK) and (
        is_assignee or task.assignee_id is None
    )
    if not (may_manage or (may_update and fields <= FIELD_UPDATE_FIELDS)):
        raise _permission_denied("Vous n'avez pas la permission de modifier cette tâche.")

    before = {
        "status": task.status,
        "progress": str(task.progress),
        "planned_end_date": str(task.planned_end_date or ""),
        "milestone": task.milestone_id,
    }
    serializer = TaskSerializer(
        task, data=data, partial=True, context={"request": request, "project": task.project}
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()
    progress = recalculate_project_progress(task.project)

    changed = {
        field: {"old": before[field], "new": str(getattr(task, field) or "")}
        for field in ("status", "progress", "planned_end_date")
        if before[field] != str(getattr(task, field) or "")
    }
    if before["milestone"] != task.milestone_id:
        changed["milestone"] = {"old": before["milestone"], "new": task.milestone_id}
    log_event(
        "TASK_STATUS_CHANGED" if "status" in changed else "TASK_UPDATED",
        actor=actor,
        entity_type="Task",
        entity_id=task.pk,
        organization=task.project.organization,
        project=task.project,
        metadata={
            "title": task.title,
            "changed": changed,
            "project_progress": str(progress),
        },
        request=request,
    )
    return task, progress


__all__ = [
    "FIELD_UPDATE_FIELDS",
    "create_milestone",
    "create_task",
    "update_milestone",
    "update_task",
]
