"""Espace de travail : chantiers, tâches assignées, files de décision (ingénieur / PME / pilotage)."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.projects.models import Project, ProjectMember
from apps.users.roles import Role

from .conftest import add_evidence

URL = "/api/workspace/"


@pytest.mark.django_db
def test_engineer_workspace_lists_projects_tasks_and_late_counters(
    auth_client, project, project_context, site
):
    data = auth_client(project_context["engineer"]).get(URL).data

    assert data["profile"] == "engineer"
    assert [card["id"] for card in data["projects"]] == [project.pk]
    card = data["projects"][0]
    assert card["tasks_late"] == 1 and card["milestones_late"] == 1
    assert card["role"] == Role.ENGINEER
    # Sa tâche assignée, avec le retard calculé par le serveur.
    assert [task["title"] for task in data["my_tasks"]] == ["Coffrage dalle R+1"]
    assert data["my_tasks"][0]["days_late"] == 20
    assert data["totals"]["my_open_tasks"] == 1


@pytest.mark.django_db
def test_validator_sees_evidences_to_validate_but_not_their_own(
    auth_client, project, project_context, site
):
    add_evidence(project, project_context["agent"], index=1)
    add_evidence(project, project_context["agent"], index=2)
    add_evidence(project, project_context["validator"], index=3)  # la sienne : exclue

    data = auth_client(project_context["validator"]).get(URL).data

    assert data["to_validate"]["count"] == 2
    assert len(data["to_validate"]["items"]) == 2
    assert data["projects"][0]["evidences_to_validate"] == 2


@pytest.mark.django_db
def test_owner_sees_expenses_to_approve(auth_client, project, project_context, money):
    data = auth_client(project_context["owner"]).get(URL).data

    assert data["profile"] == "manager"
    assert data["to_approve"]["count"] == 1
    assert data["to_approve"]["items"][0]["amount"] == 1_500_000


@pytest.mark.django_db
def test_agent_workspace_has_no_decision_queues(auth_client, project, project_context, money):
    add_evidence(project, project_context["engineer"], index=1)
    data = auth_client(project_context["agent"]).get(URL).data

    assert data["profile"] == "field"
    assert data["to_validate"]["count"] == 0 and data["to_approve"]["count"] == 0


@pytest.mark.django_db
def test_investor_profile(auth_client, project, project_context):
    assert auth_client(project_context["investor"]).get(URL).data["profile"] == "investor"


@pytest.mark.django_db
def test_stranger_sees_nothing_and_no_foreign_data(auth_client, project, project_context, site):
    data = auth_client(project_context["stranger"]).get(URL).data

    assert data["projects"] == [] and data["my_tasks"] == []
    assert data["profile"] == "none"
    assert data["totals"]["projects"] == 0


@pytest.mark.django_db
def test_workspace_query_count_does_not_depend_on_number_of_projects(
    auth_client, project, project_context, organization, site
):
    client = auth_client(project_context["engineer"])

    def measure():
        with CaptureQueriesContext(connection) as queries:
            assert client.get(URL).status_code == 200
        return len(queries)

    small = measure()
    for index in range(15):
        extra = Project.objects.create(
            organization=organization,
            name=f"Chantier {index}",
            code=f"C-{index}",
            budget_total=1_000_000,
            status="ACTIVE",
            created_by=project_context["owner"],
        )
        ProjectMember.objects.create(
            project=extra, user=project_context["engineer"], role=Role.ENGINEER
        )
    large = measure()

    assert large == small, f"{small} → {large} requêtes"


@pytest.mark.django_db
def test_projects_needing_attention_come_first(
    auth_client, project, project_context, organization, site
):
    calm = Project.objects.create(
        organization=organization,
        name="AAA calme",
        code="CALM",
        budget_total=1,
        status="ACTIVE",
        created_by=project_context["owner"],
    )
    ProjectMember.objects.create(project=calm, user=project_context["engineer"], role=Role.ENGINEER)

    cards = auth_client(project_context["engineer"]).get(URL).data["projects"]

    assert cards[0]["name"] == project.name  # en retard : avant « AAA calme »
    assert cards[-1]["name"] == "AAA calme"
