"""Endpoints de planification : jalons, tâches, planning et retards (MVP-006).

Règles appliquées :

* la **lecture** suit le périmètre projet (`accessible_projects`) → 404 hors périmètre ;
* l'**écriture** exige la capacité `MANAGE_SCHEDULE` sur le projet (403 sinon) ;
* la mise à jour d'une tâche (`statut`, avancement, dates réelles) est aussi ouverte au
  responsable désigné ou à un membre disposant de `UPDATE_TASK` (`PATCH /api/tasks/{id}/`) ;
* l'avancement du projet n'est **jamais** accepté depuis le client : il est recalculé
  après chaque écriture (`apps/projects/progress.py`).
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.activity import log_event
from apps.core.exceptions import KemtaAPIError
from apps.projects.access import accessible_projects, has_project_capability
from apps.projects.models import (
    FINAL_STATUSES,
    FINAL_TASK_STATUSES,
    OPEN_TASK_STATUSES,
    Milestone,
    MilestoneStatus,
    Task,
    TaskStatus,
)
from apps.projects.progress import compute_project_progress, recalculate_project_progress
from apps.projects.serializers import (
    MilestoneSerializer,
    MilestoneWithTasksSerializer,
    TaskSerializer,
)
from apps.projects.services import (
    create_milestone,
    create_task,
    update_milestone,
    update_task,
)
from apps.users.roles import Capability

TASK_ORDERING_FIELDS = {
    "planned_start_date",
    "-planned_start_date",
    "planned_end_date",
    "-planned_end_date",
    "created_at",
    "-created_at",
    "status",
    "-status",
    "title",
    "-title",
    "progress",
    "-progress",
}


def accessible_tasks(user):
    return Task.objects.filter(project__in=accessible_projects(user))


def accessible_milestones(user):
    return Milestone.objects.filter(project__in=accessible_projects(user))


class PlanningBaseView(APIView):
    """Lecture pour tout membre du projet, écriture soumise à `MANAGE_SCHEDULE`."""

    permission_classes = [IsAuthenticated]

    def get_project(self, request, pk):
        return get_object_or_404(accessible_projects(request.user), pk=pk)

    def require_schedule(self, request, project, message: str) -> None:
        if not has_project_capability(request.user, project, Capability.MANAGE_SCHEDULE):
            raise KemtaAPIError("permission_denied", message, http_status=403)


class MilestoneListCreateView(PlanningBaseView):
    """`GET` / `POST /api/projects/{id}/milestones/`."""

    def get(self, request, pk):
        project = self.get_project(request, pk)
        queryset = (
            Milestone.objects.filter(project=project)
            .prefetch_related("tasks")
            .order_by("order", "planned_date", "created_at")
        )
        serializer = MilestoneSerializer(queryset, many=True, context={"request": request})
        return Response({"count": len(serializer.data), "results": serializer.data})

    @transaction.atomic
    def post(self, request, pk):
        project = self.get_project(request, pk)
        milestone, progress = create_milestone(
            project=project, actor=request.user, data=request.data, request=request
        )
        data = MilestoneSerializer(milestone, context={"request": request}).data
        data["project_progress"] = float(progress)
        return Response(data, status=status.HTTP_201_CREATED)


class MilestoneDetailView(PlanningBaseView):
    """`GET` / `PATCH` / `DELETE /api/milestones/{id}/`."""

    def get_milestone(self, request, pk) -> Milestone:
        return get_object_or_404(accessible_milestones(request.user), pk=pk)

    def get(self, request, pk):
        milestone = self.get_milestone(request, pk)
        return Response(MilestoneSerializer(milestone, context={"request": request}).data)

    @transaction.atomic
    def patch(self, request, pk):
        milestone = self.get_milestone(request, pk)
        milestone, progress = update_milestone(
            milestone=milestone, actor=request.user, data=request.data, request=request
        )
        data = MilestoneSerializer(milestone, context={"request": request}).data
        data["project_progress"] = float(progress)
        return Response(data)

    @transaction.atomic
    def delete(self, request, pk):
        milestone = self.get_milestone(request, pk)
        self.require_schedule(
            request, milestone.project, "Vous n'avez pas la permission de supprimer ce jalon."
        )
        project = milestone.project
        title = milestone.title
        milestone.delete()  # suppression logique : l'historique reste lisible
        progress = recalculate_project_progress(project)
        log_event(
            "MILESTONE_DELETED",
            actor=request.user,
            entity_type="Milestone",
            entity_id=pk,
            organization=project.organization,
            project=project,
            metadata={"title": title, "project_progress": str(progress)},
            request=request,
        )
        # 204 : pas de corps, l'avancement recalculé voyage dans un en-tête.
        return Response(
            status=status.HTTP_204_NO_CONTENT, headers={"X-Project-Progress": str(progress)}
        )


class TaskListCreateView(PlanningBaseView):
    """`GET` / `POST /api/projects/{id}/tasks/` (filtres statut, jalon, responsable, retard)."""

    def get(self, request, pk):
        project = self.get_project(request, pk)
        queryset = (
            Task.objects.filter(project=project)
            .select_related("milestone", "assignee")
            .prefetch_related("depends_on")
        )

        status_filter = request.query_params.get("status")
        if status_filter:
            values = [value.upper() for value in status_filter.split(",") if value]
            unknown = [value for value in values if value not in TaskStatus.values]
            if unknown:
                raise KemtaAPIError(
                    "invalid_status", "Statut inconnu.", details={"unknown": unknown}
                )
            queryset = queryset.filter(status__in=values)

        milestone = request.query_params.get("milestone")
        if milestone:
            queryset = queryset.filter(milestone_id=milestone)

        assignee = request.query_params.get("assignee")
        if assignee:
            queryset = queryset.filter(assignee_id=assignee)

        if request.query_params.get("late") in {"1", "true", "True"}:
            queryset = queryset.filter(
                planned_end_date__lt=timezone.localdate(), status__in=OPEN_TASK_STATUSES
            )

        ordering = request.query_params.get("ordering", "planned_start_date")
        if ordering not in TASK_ORDERING_FIELDS:
            raise KemtaAPIError(
                "invalid_ordering",
                "Tri non supporté.",
                details={"supported": sorted(TASK_ORDERING_FIELDS)},
            )
        queryset = queryset.order_by(ordering, "created_at")

        serializer = TaskSerializer(
            queryset, many=True, context={"request": request, "project": project}
        )
        data = serializer.data
        return Response({"count": len(data), "results": data})

    @transaction.atomic
    def post(self, request, pk):
        project = self.get_project(request, pk)
        task, progress = create_task(
            project=project, actor=request.user, data=request.data, request=request
        )
        data = TaskSerializer(task, context={"request": request, "project": project}).data
        data["project_progress"] = float(progress)
        return Response(data, status=status.HTTP_201_CREATED)


class TaskDetailView(APIView):
    """`GET` / `PATCH` / `DELETE /api/tasks/{id}/`."""

    permission_classes = [IsAuthenticated]

    def get_task(self, request, pk) -> Task:
        return get_object_or_404(
            accessible_tasks(request.user).select_related("milestone", "assignee"), pk=pk
        )

    def get(self, request, pk):
        task = self.get_task(request, pk)
        serializer = TaskSerializer(task, context={"request": request, "project": task.project})
        return Response(serializer.data)

    @transaction.atomic
    def patch(self, request, pk):
        task = self.get_task(request, pk)
        task, project_progress = update_task(
            task=task, actor=request.user, data=request.data, request=request
        )
        data = TaskSerializer(task, context={"request": request, "project": task.project}).data
        data["project_progress"] = float(project_progress)
        return Response(data)

    @transaction.atomic
    def delete(self, request, pk):
        task = self.get_task(request, pk)
        if not has_project_capability(request.user, task.project, Capability.MANAGE_SCHEDULE):
            raise KemtaAPIError(
                "permission_denied",
                "Vous n'avez pas la permission de supprimer cette tâche.",
                http_status=403,
            )
        project = task.project
        title = task.title
        task.delete()
        progress = recalculate_project_progress(project)
        log_event(
            "TASK_DELETED",
            actor=request.user,
            entity_type="Task",
            entity_id=pk,
            organization=project.organization,
            project=project,
            metadata={"title": title, "project_progress": str(progress)},
            request=request,
        )
        return Response(
            status=status.HTTP_204_NO_CONTENT, headers={"X-Project-Progress": str(progress)}
        )


class ProjectScheduleView(PlanningBaseView):
    """`GET /api/projects/{id}/schedule/` — vue planning agrégeée (jalons + tâches + alertes).

    Une seule requête par niveau : le client n'enchaîne pas d'appels pour dessiner le
    planning, ce qui le rend utilisable sur mobile même avec une connexion faible.
    """

    def get(self, request, pk):
        project = self.get_project(request, pk)
        milestones = list(
            Milestone.objects.filter(project=project)
            # Les tâches, leurs responsables et leurs dépendances arrivent dans la même
            # passe : le planning ne fait aucune requête par ligne.
            .prefetch_related(
                Prefetch(
                    "tasks",
                    queryset=Task.objects.select_related("assignee").prefetch_related("depends_on"),
                )
            )
            .order_by("order", "planned_date", "created_at")
        )
        tasks = [task for milestone in milestones for task in milestone.tasks.all()]
        orphan_tasks = list(
            Task.objects.filter(project=project, milestone__isnull=True)
            .select_related("assignee")
            .prefetch_related("depends_on")
            .order_by("planned_start_date", "created_at")
        )
        ordered_tasks = [*tasks, *orphan_tasks]
        late_tasks = [task for task in ordered_tasks if task.is_late]
        late_milestones = [milestone for milestone in milestones if milestone.is_late]

        return Response(
            {
                "project": {
                    "id": project.id,
                    "name": project.name,
                    "status": project.status,
                    "progress": float(
                        compute_project_progress(
                            project, milestones=milestones, tasks=ordered_tasks
                        )
                    ),
                    "planned_start_date": project.planned_start_date,
                    "planned_end_date": project.planned_end_date,
                },
                "milestones": MilestoneWithTasksSerializer(
                    milestones,
                    many=True,
                    context={"request": request, "project": project},
                ).data,
                "orphan_tasks": TaskSerializer(
                    orphan_tasks, many=True, context={"request": request, "project": project}
                ).data,
                "summary": {
                    "milestones_total": len(milestones),
                    "milestones_done": len(
                        [m for m in milestones if m.status == MilestoneStatus.DONE]
                    ),
                    "tasks_total": len(ordered_tasks),
                    "tasks_done": len([t for t in ordered_tasks if t.status == TaskStatus.DONE]),
                    "tasks_late": len(late_tasks),
                    "milestones_late": len(late_milestones),
                    "names_late": [task.title for task in late_tasks[:5]]
                    + [milestone.title for milestone in late_milestones[:5]],
                },
                "alerts": [
                    {
                        "type": "task_late",
                        "id": task.id,
                        "title": task.title,
                        "days_late": task.days_late,
                        "planned_end_date": task.planned_end_date,
                    }
                    for task in late_tasks
                ]
                + [
                    {
                        "type": "milestone_late",
                        "id": milestone.id,
                        "title": milestone.title,
                        "days_late": milestone.days_late,
                        "planned_date": milestone.planned_date,
                    }
                    for milestone in late_milestones
                ],
            }
        )


class ProjectDelaysView(PlanningBaseView):
    """`GET /api/projects/{id}/delays/` — retards déterministes, expliqués."""

    def get(self, request, pk):
        project = self.get_project(request, pk)
        today = timezone.localdate()
        late_tasks = list(
            Task.objects.filter(project=project, planned_end_date__lt=today)
            .exclude(status__in=FINAL_TASK_STATUSES)
            .select_related("milestone", "assignee")
            .order_by("planned_end_date")
        )
        late_milestones = list(
            Milestone.objects.filter(project=project, planned_date__lt=today)
            .exclude(status__in=FINAL_STATUSES)
            .order_by("planned_date")
        )
        return Response(
            {
                "project": project.id,
                "reference_date": today,
                "tasks": [
                    {
                        "id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "planned_end_date": task.planned_end_date,
                        "days_late": task.days_late,
                        "milestone": task.milestone_id,
                    }
                    for task in late_tasks
                ],
                "milestones": [
                    {
                        "id": milestone.id,
                        "title": milestone.title,
                        "status": milestone.status,
                        "planned_date": milestone.planned_date,
                        "days_late": milestone.days_late,
                    }
                    for milestone in late_milestones
                ],
                "progress": float(compute_project_progress(project)),
            }
        )
