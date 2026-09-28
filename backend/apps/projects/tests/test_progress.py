"""Avancement calculé côté serveur (MVP-006) — règles et non-régression.

Règles documentées dans `docs/data-model.md` §3 et `docs/flows/planning.md` :

* avancement d'une tâche = `progress`, forcé à 100 % si terminée, 0 % si annulée ;
* avancement d'un jalon = moyenne pondérée de ses tâches ; sans tâche, 100 % si terminé, 0 %
  sinon ;
* avancement du projet = moyenne pondérée des jalons non annulés (poids `weight`)
  + un groupe « tâches sans jalon » ;
* le client ne peut jamais écrire `Project.progress`.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.projects.models import (
    Milestone,
    MilestoneStatus,
    Project,
    Task,
    TaskStatus,
)
from apps.projects.progress import (
    compute_project_progress,
    milestone_progress,
    recalculate_project_progress,
    tasks_progress,
)
from apps.users.roles import Role

TODAY = timezone.localdate()


@pytest.fixture()
def author(project_context):
    return project_context["owner"]


def make_milestone(project, author, title, **kwargs):
    return Milestone.objects.create(project=project, title=title, created_by=author, **kwargs)


def make_task(project, author, title, **kwargs):
    return Task.objects.create(project=project, title=title, created_by=author, **kwargs)


@pytest.mark.django_db
def test_project_without_planning_is_at_zero(project):
    assert compute_project_progress(project) == Decimal("0.00")


@pytest.mark.django_db
def test_milestone_without_tasks_follows_its_status(project, author):
    planned = make_milestone(project, author, "Planifié", status=MilestoneStatus.PLANNED)
    assert milestone_progress(planned) == Decimal("0")

    done = make_milestone(
        project,
        author,
        "Terminé",
        status=MilestoneStatus.DONE,
        actual_date=TODAY,
    )
    assert milestone_progress(done) == Decimal("100")

    blocked = make_milestone(project, author, "Bloqué", status=MilestoneStatus.BLOCKED)
    assert milestone_progress(blocked) == Decimal("0")


@pytest.mark.django_db
def test_milestone_follows_a_weighted_average_of_its_tasks(project, author):
    milestone = make_milestone(project, author, "Fondations")
    make_task(
        project,
        author,
        "Terrassement",
        milestone=milestone,
        status=TaskStatus.DONE,
        actual_end_date=TODAY,
        weight=Decimal("1"),
    )
    make_task(
        project,
        author,
        "Semelles",
        milestone=milestone,
        status=TaskStatus.IN_PROGRESS,
        progress=Decimal("40"),
        weight=Decimal("3"),
    )

    # (100 × 1 + 40 × 3) / 4 = 55
    assert milestone_progress(milestone) == Decimal("55.00")


@pytest.mark.django_db
def test_cancelled_tasks_weigh_zero_and_are_kept_out_of_the_ratio(project, author):
    """Une tâche annulée ne pénalise pas l'avancement mais reste comptée (poids nul)."""
    milestone = make_milestone(project, author, "Jalon")
    make_task(
        project,
        author,
        "Faite",
        milestone=milestone,
        status=TaskStatus.DONE,
        actual_end_date=TODAY,
        weight=Decimal("1"),
    )
    make_task(
        project,
        author,
        "Annulée",
        milestone=milestone,
        status=TaskStatus.CANCELLED,
        weight=Decimal("5"),
    )

    assert tasks_progress(list(milestone.tasks.all())) == Decimal("100.00")


@pytest.mark.django_db
def test_project_progress_is_weighted_by_milestones(project, author):
    first = make_milestone(
        project,
        author,
        "Installation",
        status=MilestoneStatus.DONE,
        actual_date=TODAY,
        weight=Decimal("2"),
    )
    second = make_milestone(project, author, "Fondations", weight=Decimal("3"))
    make_task(
        project,
        author,
        "Semelles",
        milestone=second,
        status=TaskStatus.IN_PROGRESS,
        progress=Decimal("40"),
        weight=Decimal("3"),
    )
    make_task(
        project,
        author,
        "Terrassement",
        milestone=second,
        status=TaskStatus.DONE,
        actual_end_date=TODAY,
        weight=Decimal("1"),
    )

    # Jalon 1 : 100 (poids 2) — Jalon 2 : (100×1 + 40×3)/4 = 55 (poids 3)
    # → (100×2 + 55×3) / 5 = 73
    assert first.status == MilestoneStatus.DONE
    assert compute_project_progress(project) == Decimal("73.00")


@pytest.mark.django_db
def test_tasks_without_milestone_form_their_own_group(project, author):
    milestone = make_milestone(
        project,
        author,
        "Terminé",
        status=MilestoneStatus.DONE,
        actual_date=TODAY,
        weight=Decimal("2"),
    )
    make_task(
        project,
        author,
        "Clôture administrative",
        status=TaskStatus.IN_PROGRESS,
        progress=Decimal("10"),
        weight=Decimal("1"),
    )

    assert milestone.status == MilestoneStatus.DONE
    # (100×2 + 10×1) / 3 = 70
    assert compute_project_progress(project) == Decimal("70.00")


@pytest.mark.django_db
def test_cancelled_milestone_is_ignored(project, author):
    make_milestone(
        project,
        author,
        "Terminé",
        status=MilestoneStatus.DONE,
        actual_date=TODAY,
        weight=Decimal("1"),
    )
    make_milestone(
        project, author, "Annulé", status=MilestoneStatus.CANCELLED, weight=Decimal("100")
    )

    assert compute_project_progress(project) == Decimal("100.00")


@pytest.mark.django_db
def test_recalculate_persists_the_value_and_is_idempotent(project, author):
    milestone = make_milestone(project, author, "Jalon", weight=Decimal("1"))
    make_task(
        project,
        author,
        "Tâche",
        milestone=milestone,
        status=TaskStatus.IN_PROGRESS,
        progress=Decimal("30"),
    )

    value = recalculate_project_progress(project)
    project.refresh_from_db()

    assert value == Decimal("30.00")
    assert project.progress == Decimal("30.00")
    assert recalculate_project_progress(project) == value  # aucun effet de bord cumulé


@pytest.mark.django_db
def test_api_recalculates_progress_after_each_write(auth_client, project, project_context):
    """L'avancement suit les écritures d'API sans action du client."""
    owner = auth_client(project_context["owner"])
    milestone = owner.post(
        f"/api/projects/{project.id}/milestones/",
        {"title": "Gros œuvre", "weight": "1"},
        format="json",
    ).data
    task = owner.post(
        f"/api/projects/{project.id}/tasks/",
        {"title": "Coulage", "milestone": milestone["id"], "progress": 0},
        format="json",
    ).data

    project.refresh_from_db()
    assert project.progress == Decimal("0.00")

    owner.patch(
        f"/api/tasks/{task['id']}/",
        {"status": TaskStatus.DONE, "actual_end_date": str(TODAY)},
        format="json",
    )
    project.refresh_from_db()
    assert project.progress == Decimal("100.00")

    owner.patch(
        f"/api/milestones/{milestone['id']}/",
        {"status": MilestoneStatus.CANCELLED, "weight": "1"},
        format="json",
    )
    project.refresh_from_db()
    assert project.progress == Decimal("0.00")  # jalon annulé : ignoré


@pytest.mark.django_db
def test_client_cannot_write_project_progress(auth_client, project, project_context):
    response = auth_client(project_context["owner"]).patch(
        f"/api/projects/{project.id}/", {"progress": 99}, format="json"
    )
    project.refresh_from_db()
    assert response.status_code == 200
    assert project.progress == Decimal("0.00")  # champ en lecture seule


@pytest.mark.django_db
def test_schedule_endpoint_returns_the_plan_with_alerts(auth_client, project, project_context):
    """La vue planning agrège jalons, tâches, résumé et alertes en un appel."""
    owner = auth_client(project_context["owner"])
    milestone = owner.post(
        f"/api/projects/{project.id}/milestones/",
        {"title": "Fondations", "planned_date": str(TODAY - timedelta(days=5)), "weight": "1"},
        format="json",
    ).data
    owner.post(
        f"/api/projects/{project.id}/tasks/",
        {
            "title": "Semelles en retard",
            "milestone": milestone["id"],
            "planned_end_date": str(TODAY - timedelta(days=3)),
        },
        format="json",
    )
    owner.post(
        f"/api/projects/{project.id}/tasks/",
        {"title": "Tâche sans jalon", "progress": 50},
        format="json",
    )

    response = owner.get(f"/api/projects/{project.id}/schedule/")

    assert response.status_code == 200
    assert response.data["summary"] == {
        "milestones_total": 1,
        "milestones_done": 0,
        "tasks_total": 2,
        "tasks_done": 0,
        "tasks_late": 1,
        "milestones_late": 1,
        "names_late": ["Semelles en retard", "Fondations"],
    }
    assert [alert["type"] for alert in response.data["alerts"]] == ["task_late", "milestone_late"]
    assert response.data["alerts"][0]["days_late"] == 3
    assert response.data["orphan_tasks"][0]["title"] == "Tâche sans jalon"
    assert response.data["milestones"][0]["title"] == "Fondations"
    assert response.data["project"]["progress"] == 25.0  # (0 × 1 + 50 × 1) / 2


@pytest.mark.django_db
def test_delays_endpoint_lists_late_items_with_reasons(auth_client, project, project_context):
    owner = auth_client(project_context["owner"])
    late_task = Task.objects.create(
        project=project,
        title="Tâche dépassée",
        planned_end_date=TODAY - timedelta(days=2),
        created_by=project_context["owner"],
    )
    Task.objects.create(
        project=project,
        title="Tâche dans les temps",
        planned_end_date=TODAY + timedelta(days=2),
        created_by=project_context["owner"],
    )

    response = owner.get(f"/api/projects/{project.id}/delays/")

    assert response.status_code == 200
    assert response.data["reference_date"] == TODAY
    assert [item["id"] for item in response.data["tasks"]] == [late_task.id]
    assert response.data["tasks"][0]["days_late"] == 2
    assert response.data["milestones"] == []


@pytest.mark.django_db
def test_schedule_is_scoped_and_readable_by_any_member(auth_client, project, project_context):
    """Un investisseur lit le planning ; un étranger reçoit 404 (hors périmètre)."""
    assert (
        auth_client(project_context["investor"])
        .get(f"/api/projects/{project.id}/schedule/")
        .status_code
        == 200
    )
    assert (
        auth_client(project_context["stranger"])
        .get(f"/api/projects/{project.id}/schedule/")
        .status_code
        == 404
    )


@pytest.mark.django_db
def test_schedule_has_no_n_plus_one(auth_client, project, project_context):
    """Le planning se charge en un nombre constant de requêtes, même rempli."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    owner = project_context["owner"]
    for index in range(10):
        milestone = Milestone.objects.create(
            project=project, title=f"Jalon {index:02d}", created_by=owner
        )
        for sub in range(3):
            Task.objects.create(
                project=project,
                milestone=milestone,
                title=f"Tâche {index:02d}-{sub}",
                created_by=owner,
                assignee=owner,
            )

    with CaptureQueriesContext(connection) as queries:
        response = auth_client(owner).get(f"/api/projects/{project.id}/schedule/")

    assert response.status_code == 200
    assert response.data["summary"]["tasks_total"] == 30
    assert len(queries) <= 12, [query["sql"][:120] for query in queries.captured_queries]


@pytest.mark.django_db
def test_progress_tolerates_a_milestone_created_by_seed(project, make_user):
    """Les montants/poids arrivent parfois en `str` (seed, import) : jamais d'erreur 500."""
    author = make_user(Role.PROJECT_OWNER, phone_number="+237699444001")
    milestone = Milestone.objects.create(
        project=project, title="Jalon importé", weight="2.50", created_by=author
    )
    Task.objects.create(
        project=project,
        milestone=milestone,
        title="Tâche importée",
        progress="12.50",
        weight="1.50",
        created_by=author,
    )

    assert milestone.weight == Decimal("2.50")
    assert compute_project_progress(project) == Decimal("12.50")


@pytest.mark.django_db
def test_progress_uses_the_scale_of_large_projects(project, author):
    """Beaucoup de jalons/tâches : le calcul reste juste et rapide."""
    total_weight = Decimal("0")
    expected = Decimal("0")
    for index in range(20):
        milestone = make_milestone(project, author, f"Jalon {index:02d}", weight=Decimal("1"))
        progress = Decimal(index * 5)  # 0, 5, … 95
        make_task(
            project,
            author,
            f"Tâche {index:02d}",
            milestone=milestone,
            status=TaskStatus.IN_PROGRESS,
            progress=progress,
            weight=Decimal("1"),
        )
        expected += progress
        total_weight += Decimal("1")

    assert compute_project_progress(project) == (expected / total_weight).quantize(Decimal("0.01"))
    assert Project.objects.get(pk=project.pk).progress >= Decimal("0")


@pytest.mark.django_db
def test_schedule_exposes_tasks_within_each_milestone(auth_client, project, project_context):
    """La vue planning livre les tâches **imbriquées** par jalon (pas d'appel supplémentaire)."""
    owner = auth_client(project_context["owner"])
    milestone = owner.post(
        f"/api/projects/{project.id}/milestones/", {"title": "Second œuvre"}, format="json"
    ).data
    owner.post(
        f"/api/projects/{project.id}/tasks/",
        {"title": "Peinture", "milestone": milestone["id"], "progress": 20},
        format="json",
    )

    response = owner.get(f"/api/projects/{project.id}/schedule/")

    assert response.status_code == 200
    nested = response.data["milestones"][0]["tasks"]
    assert [task["title"] for task in nested] == ["Peinture"]
    assert nested[0]["status_label"] == "À faire"


@pytest.mark.django_db
def test_writes_return_the_recalculated_progress(auth_client, project, project_context):
    """Chaque écriture renvoie l'avancement recalculé : aucun client ne recalcule rien."""
    owner = auth_client(project_context["owner"])

    milestone = owner.post(
        f"/api/projects/{project.id}/milestones/", {"title": "Gros œuvre"}, format="json"
    )
    assert milestone.data["project_progress"] == 0.0

    task = owner.post(
        f"/api/projects/{project.id}/tasks/",
        {"title": "Coulage", "milestone": milestone.data["id"]},
        format="json",
    )
    assert task.data["project_progress"] == 0.0

    done = owner.patch(
        f"/api/tasks/{task.data['id']}/",
        {"status": TaskStatus.DONE, "actual_end_date": str(TODAY)},
        format="json",
    )
    assert done.data["project_progress"] == 100.0

    updated = owner.patch(
        f"/api/milestones/{milestone.data['id']}/",
        {"status": MilestoneStatus.BLOCKED},
        format="json",
    )
    assert updated.data["project_progress"] == 100.0  # un jalon bloqué garde ses tâches

    # Les suppressions (204) transmettent l'avancement dans un en-tête.
    deleted = owner.delete(f"/api/tasks/{task.data['id']}/")
    assert deleted.status_code == 204
    assert deleted["X-Project-Progress"] == "0.00"
