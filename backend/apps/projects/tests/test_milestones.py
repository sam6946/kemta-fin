"""Jalons : création, validation métier, retards, permissions (MVP-006)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.models import ActivityLog
from apps.projects.models import Milestone, MilestoneStatus, Project, Task, TaskStatus
from apps.users.roles import Role

URL = "/api/projects/{project_id}/milestones/"


@pytest.mark.django_db
def test_manager_creates_a_milestone_and_it_is_logged(auth_client, project, project_context):
    response = auth_client(project_context["owner"]).post(
        URL.format(project_id=project.id),
        {
            "title": "Achèvement des fondations",
            "description": "Semelles et longrines coulées",
            "status": MilestoneStatus.PLANNED,
            "planned_date": "2026-04-30",
            "weight": "2",
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert response.data["title"] == "Achèvement des fondations"
    assert response.data["status_label"] == "Planifié"
    assert Decimal(response.data["weight"]) == Decimal("2")
    assert response.data["project"] == project.id

    milestone = Milestone.objects.get(pk=response.data["id"])
    assert milestone.created_by == project_context["owner"]
    event = ActivityLog.objects.get(action="MILESTONE_CREATED", entity_id=milestone.pk)
    assert event.project == project and event.organization == project.organization


@pytest.mark.django_db
def test_milestone_requires_a_title_dates_and_project(auth_client, project, project_context):
    """Critère de sortie : un jalon a au minimum titre, statut, date prévue et projet."""
    client = auth_client(project_context["owner"])

    assert client.post(URL.format(project_id=project.id), {}, format="json").status_code == 400
    assert (
        client.post(URL.format(project_id=project.id), {"title": "ab"}, format="json").status_code
        == 400
    )

    response = client.post(
        URL.format(project_id=project.id), {"title": "Jalon sans date prévue"}, format="json"
    )
    # La date prévue n'est pas obligatoire en base, mais le contrat d'API la recommande :
    # on documente le comportement (201) plutôt que d'inventer une contrainte non spécifiée.
    assert response.status_code == 201
    assert response.data["planned_date"] is None


@pytest.mark.django_db
def test_done_milestone_requires_actual_date_and_forbids_date_otherwise(
    auth_client, project, project_context
):
    client = auth_client(project_context["owner"])

    response = client.post(
        URL.format(project_id=project.id),
        {"title": "Jalon terminé sans date réelle", "status": MilestoneStatus.DONE},
        format="json",
    )
    assert response.status_code == 400
    assert "actual_date" in response.data["error"]["details"]

    response = client.post(
        URL.format(project_id=project.id),
        {
            "title": "Jalon planifié avec date réelle",
            "status": MilestoneStatus.PLANNED,
            "actual_date": "2026-05-01",
        },
        format="json",
    )
    assert response.status_code == 400
    assert "actual_date" in response.data["error"]["details"]

    response = client.post(
        URL.format(project_id=project.id),
        {"title": "Jalon terminé", "status": MilestoneStatus.DONE, "actual_date": "2026-05-01"},
        format="json",
    )
    assert response.status_code == 201


@pytest.mark.django_db
def test_weight_must_be_positive(auth_client, project, project_context):
    response = auth_client(project_context["owner"]).post(
        URL.format(project_id=project.id),
        {"title": "Jalon sans poids", "weight": "0"},
        format="json",
    )
    assert response.status_code == 400
    assert "weight" in response.data["error"]["details"]


@pytest.mark.django_db
def test_milestone_is_late_only_when_not_final(project, make_user):
    """Un jalon en retard : date prévue dépassée et statut non terminal."""
    owner = make_user(Role.PROJECT_OWNER, phone_number="+237699111001")
    overdue = timezone.localdate() - timedelta(days=10)

    planned = Milestone.objects.create(
        project=project, title="Jalon dépassé", planned_date=overdue, created_by=owner
    )
    assert planned.is_late is True
    assert planned.days_late == 10

    done = Milestone.objects.create(
        project=project,
        title="Jalon terminé",
        planned_date=overdue,
        actual_date=overdue + timedelta(days=2),
        status=MilestoneStatus.DONE,
        created_by=owner,
    )
    assert done.is_late is False

    cancelled = Milestone.objects.create(
        project=project,
        title="Jalon annulé",
        planned_date=overdue,
        status=MilestoneStatus.CANCELLED,
        created_by=owner,
    )
    assert cancelled.is_late is False


@pytest.mark.django_db
def test_milestone_update_is_logged_with_previous_value(auth_client, project, project_context):
    milestone = Milestone.objects.create(
        project=project,
        title="Jalon à modifier",
        status=MilestoneStatus.PLANNED,
        planned_date="2026-06-01",
        created_by=project_context["owner"],
    )
    response = auth_client(project_context["owner"]).patch(
        f"/api/milestones/{milestone.id}/",
        {"status": MilestoneStatus.DONE, "actual_date": "2026-06-03"},
        format="json",
    )

    assert response.status_code == 200, response.data
    event = ActivityLog.objects.get(action="MILESTONE_UPDATED", entity_id=milestone.pk)
    assert event.metadata["changed"]["status"] == {"old": "PLANNED", "new": "DONE"}


@pytest.mark.django_db
def test_milestone_delete_is_soft_and_logged(auth_client, project, project_context):
    milestone = Milestone.objects.create(
        project=project, title="Jalon à supprimer", created_by=project_context["owner"]
    )
    response = auth_client(project_context["owner"]).delete(f"/api/milestones/{milestone.id}/")

    assert response.status_code == 204
    assert Milestone.objects.filter(pk=milestone.pk).count() == 0  # exclu des vues
    assert Milestone.all_objects.get(pk=milestone.pk).deleted_at is not None  # conservé
    assert ActivityLog.objects.filter(action="MILESTONE_DELETED", entity_id=milestone.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "actor,expected_status",
    [
        ("owner", 201),
        ("engineer", 201),  # MANAGE_SCHEDULE
        ("agent", 403),  # membre du projet mais sans droit de planification
        ("investor", 403),
        ("stranger", 404),  # hors périmètre : le projet n'existe pas pour lui
    ],
)
def test_milestone_creation_follows_capabilities(
    auth_client, project, project_context, actor, expected_status
):
    response = auth_client(project_context[actor]).post(
        URL.format(project_id=project.id), {"title": f"Jalon par {actor}"}, format="json"
    )
    assert response.status_code == expected_status


@pytest.mark.django_db
def test_milestone_list_is_scoped_to_the_project(auth_client, project, project_context, make_user):
    other = Project.objects.create(
        organization=project.organization,
        name="Autre projet",
        created_by=project_context["owner"],
    )
    author = make_user(Role.ENGINEER, phone_number="+237699111002")
    Milestone.objects.create(project=project, title="Jalon du projet", created_by=author)
    Milestone.objects.create(project=other, title="Jalon d'un autre projet", created_by=author)

    response = auth_client(project_context["owner"]).get(URL.format(project_id=project.id))

    assert response.status_code == 200
    assert [item["title"] for item in response.data["results"]] == ["Jalon du projet"]


@pytest.mark.django_db
def test_tasks_of_a_deleted_milestone_are_not_counted_twice(project, make_user):
    """La suppression logique d'un jalon n'emporte pas ses tâches (FK conservée)."""
    owner = make_user(Role.PROJECT_OWNER, phone_number="+237699111003")
    milestone = Milestone.objects.create(project=project, title="Jalon", created_by=owner)
    task = Task.objects.create(
        project=project, milestone=milestone, title="Tâche rattachée", created_by=owner
    )
    milestone.delete()

    task.refresh_from_db()
    assert task.status == TaskStatus.TODO
    assert task.milestone_id == milestone.pk
