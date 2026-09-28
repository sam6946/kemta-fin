"""Phase 11 — aucune collection sans borne, aucune requête N+1 sur les listes principales.

Chaque liste est mesurée avec peu puis beaucoup de lignes : le nombre de requêtes SQL doit
rester **identique**. Si un développeur ajoute un accès à une relation dans un serializer sans
`select_related`/`prefetch_related`, ce test échoue.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.dashboard.tests.conftest import add_evidence
from apps.evidences.models import EvidenceStatus
from apps.finance import services
from apps.finance.models import BudgetLine
from apps.projects.models import Milestone, Task, TaskStatus


def measure(client, url, **params):
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url, params)
    assert response.status_code == 200, response.data
    return len(queries), response.data


@pytest.fixture()
def seed_evidences(project, project_context):
    def _seed(count, start=0):
        for index in range(start, start + count):
            add_evidence(
                project,
                project_context["agent"] if index % 2 else project_context["engineer"],
                index=index,
                hours_old=index % 90 + 1,
                status=[EvidenceStatus.PENDING, EvidenceStatus.VALIDATED, EvidenceStatus.REJECTED][
                    index % 3
                ],
            )

    return _seed


@pytest.mark.django_db
def test_project_evidence_list_is_paginated_without_n_plus_one(
    auth_client, project, project_context, seed_evidences
):
    client = auth_client(project_context["owner"])
    url = f"/api/projects/{project.pk}/evidences/"
    seed_evidences(3)
    few, _ = measure(client, url)
    seed_evidences(60, start=3)
    many, data = measure(client, url, page_size=100)

    assert data["count"] == 63 and len(data["results"]) == 63
    assert many == few, f"N+1 : {few} → {many} requêtes"
    assert len(client.get(url).data["results"]) == 20
    # Les listes portent des vignettes, pas les fichiers originaux.
    assert all(
        "/thumbnail/" in item["thumbnail_url"] or "/file/" in item["thumbnail_url"]
        for item in data["results"]
    )


@pytest.mark.django_db
def test_pending_evidence_queue_is_paginated_without_n_plus_one(
    auth_client, project, project_context, seed_evidences
):
    client = auth_client(project_context["validator"])
    seed_evidences(3)
    few, _ = measure(client, "/api/evidences/pending/")
    seed_evidences(150, start=3)
    many, data = measure(client, "/api/evidences/pending/")

    assert many == few, f"N+1 : {few} → {many} requêtes"
    assert data["count"] >= 50 and "next" in data and "results" in data
    assert len(data["results"]) <= 100


@pytest.mark.django_db
def test_milestones_and_tasks_are_paginated(auth_client, project, project_context):
    owner = project_context["owner"]
    client = auth_client(owner)
    today = timezone.localdate()

    def seed(count, start):
        for index in range(start, start + count):
            milestone = Milestone.objects.create(
                project=project,
                title=f"Jalon {index}",
                planned_date=today + timedelta(days=index),
                order=index,
                created_by=owner,
            )
            Task.objects.create(
                project=project,
                milestone=milestone,
                title=f"Tâche {index}",
                status=TaskStatus.TODO,
                assignee=project_context["engineer"],
                planned_end_date=today + timedelta(days=index),
                created_by=owner,
            )

    seed(2, 0)
    m_few, _ = measure(client, f"/api/projects/{project.pk}/milestones/")
    t_few, _ = measure(client, f"/api/projects/{project.pk}/tasks/")
    seed(120, 2)
    m_many, m_data = measure(client, f"/api/projects/{project.pk}/milestones/")
    t_many, t_data = measure(client, f"/api/projects/{project.pk}/tasks/")

    assert m_data["count"] == 122 and len(m_data["results"]) == 100 and m_data["next"]
    assert t_data["count"] == 122 and len(t_data["results"]) == 100 and t_data["next"]
    assert (m_many, t_many) == (m_few, t_few)
    page_two = client.get(m_data["next"].split("testserver")[-1]).data
    assert len(page_two["results"]) == 22


@pytest.mark.django_db
def test_budget_lines_and_expenses_are_paginated_without_n_plus_one(
    auth_client, project, project_context
):
    owner, finance = project_context["owner"], project_context["finance"]
    client = auth_client(owner)

    def seed(count, start):
        for index in range(start, start + count):
            line = services.create_budget_line(
                project=project,
                actor=owner,
                data={"label": f"Poste {index}", "category": "MATERIALS", "planned_amount": 1000},
            )
            services.create_expense(
                project=project,
                actor=finance,
                data={
                    "title": f"Dépense {index}",
                    "amount": 100,
                    "incurred_on": "2026-02-10",
                    "budget_line": line.pk,
                },
            )

    seed(2, 0)
    bl_few, _ = measure(client, f"/api/projects/{project.pk}/budget-lines/")
    ex_few, _ = measure(client, f"/api/projects/{project.pk}/expenses/")
    seed(110, 2)
    bl_many, bl_data = measure(client, f"/api/projects/{project.pk}/budget-lines/")
    ex_many, ex_data = measure(client, f"/api/projects/{project.pk}/expenses/")

    assert BudgetLine.objects.filter(project=project).count() == 112
    assert bl_data["count"] == 112 and len(bl_data["results"]) == 100
    assert "summary" in bl_data and "categories" in bl_data  # contrat conservé avec la pagination
    assert ex_data["count"] == 112 and len(ex_data["results"]) <= 100
    assert (bl_many, ex_many) == (bl_few, ex_few)


@pytest.mark.django_db
def test_page_size_can_never_exceed_the_cap(auth_client, project, project_context):
    client = auth_client(project_context["owner"])
    assert (
        len(
            client.get(f"/api/projects/{project.pk}/activity/", {"page_size": 5000}).data["results"]
        )
        <= 100
    )
    assert client.get("/api/notifications/", {"page_size": 5000}).data["count"] == 0
