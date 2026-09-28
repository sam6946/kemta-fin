"""Tâches : création, validations, dépendances, retards et permissions (MVP-006)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.models import ActivityLog
from apps.projects.models import Milestone, Task, TaskStatus
from apps.users.roles import Role

URL = "/api/projects/{project_id}/tasks/"


@pytest.mark.django_db
def test_manager_creates_a_task_attached_to_a_milestone(auth_client, project, project_context):
    milestone = Milestone.objects.create(
        project=project, title="Fondations", created_by=project_context["owner"]
    )

    response = auth_client(project_context["owner"]).post(
        URL.format(project_id=project.id),
        {
            "title": "Coulage des semelles",
            "milestone": milestone.id,
            "status": TaskStatus.IN_PROGRESS,
            "planned_start_date": "2026-03-01",
            "planned_end_date": "2026-03-20",
            "actual_start_date": "2026-03-02",
            "progress": 25,
            "weight": 3,
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert response.data["milestone_title"] == "Fondations"
    assert response.data["status_label"] == "En cours"
    assert Decimal(response.data["progress"]) == Decimal("25")


@pytest.mark.django_db
def test_task_rejects_inconsistent_dates(auth_client, project, project_context):
    response = auth_client(project_context["owner"]).post(
        URL.format(project_id=project.id),
        {
            "title": "Dates inversées",
            "planned_start_date": "2026-05-10",
            "planned_end_date": "2026-05-01",
        },
        format="json",
    )

    assert response.status_code == 400
    assert "planned_end_date" in response.data["error"]["details"]


@pytest.mark.django_db
def test_task_progress_must_be_a_percentage(auth_client, project, project_context):
    client = auth_client(project_context["owner"])

    too_much = client.post(
        URL.format(project_id=project.id),
        {"title": "Avancement excessif", "progress": 120},
        format="json",
    )
    assert too_much.status_code == 400
    assert "progress" in too_much.data["error"]["details"]

    negative = client.post(
        URL.format(project_id=project.id),
        {"title": "Avancement négatif", "progress": -5},
        format="json",
    )
    assert negative.status_code == 400


@pytest.mark.django_db
def test_done_task_requires_actual_end_date_and_forces_progress(
    auth_client, project, project_context
):
    client = auth_client(project_context["owner"])

    missing_date = client.post(
        URL.format(project_id=project.id),
        {"title": "Terminée sans date", "status": TaskStatus.DONE},
        format="json",
    )
    assert missing_date.status_code == 400
    assert "actual_end_date" in missing_date.data["error"]["details"]

    completed = client.post(
        URL.format(project_id=project.id),
        {
            "title": "Terminée",
            "status": TaskStatus.DONE,
            "actual_end_date": "2026-04-02",
            "progress": 40,  # ignoré : une tâche terminée est à 100 %
        },
        format="json",
    )
    assert completed.status_code == 201, completed.data
    assert Decimal(completed.data["progress"]) == Decimal("100")


@pytest.mark.django_db
def test_task_cannot_depend_on_itself_or_create_a_cycle(auth_client, project, project_context):
    client = auth_client(project_context["owner"])
    first = client.post(URL.format(project_id=project.id), {"title": "Tâche A"}, format="json").data
    second = client.post(
        URL.format(project_id=project.id),
        {"title": "Tâche B", "depends_on": [first["id"]]},
        format="json",
    )
    assert second.status_code == 201, second.data

    # A dépend de B, qui dépend de A → cycle refusé.
    cycle = client.patch(
        f"/api/tasks/{first['id']}/", {"depends_on": [second.data["id"]]}, format="json"
    )
    assert cycle.status_code == 409
    assert cycle.data["error"]["code"] == "dependency_cycle"

    itself = client.patch(
        f"/api/tasks/{first['id']}/", {"depends_on": [first["id"]]}, format="json"
    )
    assert itself.status_code == 409
    assert itself.data["error"]["code"] == "dependency_cycle"


@pytest.mark.django_db
def test_task_dependency_must_belong_to_the_same_project(
    auth_client, project, project_context, make_user
):
    """Une dépendance d'un autre projet est refusée (le queryset est restreint au projet)."""
    from apps.projects.models import Project

    other_project = Project.objects.create(
        organization=project.organization, name="Projet voisin", created_by=project_context["owner"]
    )
    foreign = Task.objects.create(
        project=other_project,
        title="Tâche d'un autre projet",
        created_by=make_user(Role.ENGINEER, phone_number="+237699222001"),
    )

    response = auth_client(project_context["owner"]).post(
        URL.format(project_id=project.id),
        {"title": "Tâche dépendante", "depends_on": [foreign.pk]},
        format="json",
    )
    assert response.status_code == 400
    assert "depends_on" in response.data["error"]["details"]


@pytest.mark.django_db
def test_milestone_must_belong_to_the_same_project(
    auth_client, project, project_context, make_user
):
    from apps.projects.models import Project

    other_project = Project.objects.create(
        organization=project.organization, name="Projet voisin", created_by=project_context["owner"]
    )
    foreign = Milestone.objects.create(
        project=other_project,
        title="Jalon étranger",
        created_by=make_user(Role.ENGINEER, phone_number="+237699222002"),
    )

    response = auth_client(project_context["owner"]).post(
        URL.format(project_id=project.id),
        {"title": "Tâche mal rattachée", "milestone": foreign.pk},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_late_task_detection_and_filter(auth_client, project, project_context):
    overdue = timezone.localdate() - timedelta(days=7)
    late = Task.objects.create(
        project=project,
        title="Tâche en retard",
        planned_end_date=overdue,
        created_by=project_context["owner"],
    )
    Task.objects.create(
        project=project,
        title="Tâche à l'heure",
        planned_end_date=timezone.localdate() + timedelta(days=3),
        created_by=project_context["owner"],
    )
    Task.objects.create(
        project=project,
        title="Tâche terminée (plus en retard)",
        planned_end_date=overdue,
        actual_end_date=overdue,
        status=TaskStatus.DONE,
        created_by=project_context["owner"],
    )

    assert late.is_late is True and late.days_late == 7

    response = auth_client(project_context["owner"]).get(
        URL.format(project_id=project.id) + "?late=1"
    )
    assert response.status_code == 200
    assert [task["title"] for task in response.data["results"]] == ["Tâche en retard"]

    assert late.is_late and Task.objects.get(pk=late.pk).days_late == 7


@pytest.mark.django_db
def test_task_status_change_is_logged_with_previous_value(auth_client, project, project_context):
    task = Task.objects.create(
        project=project, title="Tâche à démarrer", created_by=project_context["owner"]
    )

    response = auth_client(project_context["owner"]).patch(
        f"/api/tasks/{task.id}/", {"status": TaskStatus.IN_PROGRESS, "progress": 30}, format="json"
    )

    assert response.status_code == 200, response.data
    event = ActivityLog.objects.get(action="TASK_STATUS_CHANGED", entity_id=task.pk)
    assert event.metadata["changed"]["status"] == {"old": "TODO", "new": "IN_PROGRESS"}
    assert event.project == project


@pytest.mark.django_db
def test_assignee_with_update_task_can_report_progress_only(
    auth_client, project, project_context, make_user
):
    """Le responsable désigné met à jour l'exécution, pas la planification."""
    from apps.projects.models import ProjectMember

    # CONTRACTOR porte `UPDATE_TASK` (l'agent terrain, lui, ne porte que la capture de preuves).
    contractor = make_user(Role.CONTRACTOR, phone_number="+237699333002")
    ProjectMember.objects.create(project=project, user=contractor, role=Role.CONTRACTOR)
    task = Task.objects.create(
        project=project,
        title="Tâche de terrain",
        assignee=contractor,
        planned_end_date=timezone.localdate() + timedelta(days=5),
        created_by=project_context["owner"],
    )

    client = auth_client(contractor)
    ok = client.patch(f"/api/tasks/{task.id}/", {"progress": 60}, format="json")
    assert ok.status_code == 200, ok.data

    refused = client.patch(
        f"/api/tasks/{task.id}/", {"planned_end_date": "2027-01-01"}, format="json"
    )
    assert refused.status_code == 403
    assert refused.data["error"]["code"] == "permission_denied"

    # Un membre sans UPDATE_TASK ne peut plus modifier la tâche.
    ProjectMember.objects.filter(project=project, user=contractor).update(role=Role.INVESTOR)
    denied = client.patch(f"/api/tasks/{task.id}/", {"progress": 80}, format="json")
    assert denied.status_code == 403

    # Un autre membre avec UPDATE_TASK mais non désigné ne modifie pas la tâche d'autrui.
    other = make_user(Role.CONTRACTOR, phone_number="+237699333003")
    ProjectMember.objects.create(project=project, user=other, role=Role.CONTRACTOR)
    outsider = auth_client(other).patch(f"/api/tasks/{task.id}/", {"progress": 90}, format="json")
    assert outsider.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize(
    "actor,expected_status",
    [
        ("owner", 201),
        ("engineer", 201),  # MANAGE_SCHEDULE
        ("contractor", 403),  # UPDATE_TASK sans MANAGE_SCHEDULE : ne crée pas de tâche
        ("agent", 403),
        ("stranger", 404),
    ],
)
def test_task_creation_follows_capabilities(
    auth_client, project, project_context, actor, expected_status, make_user
):
    from apps.projects.models import ProjectMember

    if actor == "contractor":
        # Rôle non présent dans le contexte partagé : on l'ajoute au projet.
        contractor = make_user(Role.CONTRACTOR, phone_number="+237699333001")
        ProjectMember.objects.create(project=project, user=contractor, role=Role.CONTRACTOR)
        project_context = {**project_context, "contractor": contractor}

    response = auth_client(project_context[actor]).post(
        URL.format(project_id=project.id), {"title": f"Tâche par {actor}"}, format="json"
    )
    assert response.status_code == expected_status


@pytest.mark.django_db
def test_task_delete_is_soft_and_logged(auth_client, project, project_context):
    task = Task.objects.create(
        project=project, title="Tâche à supprimer", created_by=project_context["owner"]
    )
    response = auth_client(project_context["owner"]).delete(f"/api/tasks/{task.id}/")

    assert response.status_code == 204
    assert Task.objects.filter(pk=task.pk).count() == 0
    assert Task.all_objects.get(pk=task.pk).deleted_at is not None
    assert ActivityLog.objects.filter(action="TASK_DELETED", entity_id=task.pk).exists()


@pytest.mark.django_db
def test_task_list_filters_and_ordering(auth_client, project, project_context):
    milestone = Milestone.objects.create(
        project=project, title="Jalon", created_by=project_context["owner"]
    )
    Task.objects.create(
        project=project, milestone=milestone, title="Alpha", created_by=project_context["owner"]
    )
    Task.objects.create(
        project=project,
        title="Beta",
        status=TaskStatus.DONE,
        actual_end_date=timezone.localdate(),
        created_by=project_context["owner"],
    )
    client = auth_client(project_context["owner"])

    by_status = client.get(URL.format(project_id=project.id) + "?status=DONE")
    assert [task["title"] for task in by_status.data["results"]] == ["Beta"]

    by_milestone = client.get(URL.format(project_id=project.id) + f"?milestone={milestone.id}")
    assert [task["title"] for task in by_milestone.data["results"]] == ["Alpha"]

    bad_ordering = client.get(URL.format(project_id=project.id) + "?ordering=title;DROP TABLE")
    assert bad_ordering.status_code == 400
    assert bad_ordering.data["error"]["code"] == "invalid_ordering"

    bad_status = client.get(URL.format(project_id=project.id) + "?status=INEXISTANT")
    assert bad_status.status_code == 400
    assert bad_status.data["error"]["code"] == "invalid_status"
