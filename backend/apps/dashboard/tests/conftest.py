"""Jeu de données du dashboard : un chantier avec planning, preuves, budget et dépenses."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.evidences.models import Evidence, EvidenceStatus
from apps.projects.models import Milestone, MilestoneStatus, Task, TaskStatus

URL = "/api/projects/{project}/dashboard/"


def add_evidence(project, author, *, status=EvidenceStatus.PENDING, hours_old=1, index=0):
    """Crée une preuve directement en base (sans fichier réel : le dashboard ne l'ouvre pas)."""
    return Evidence.objects.create(
        project=project,
        author=author,
        captured_at=timezone.now() - timedelta(hours=hours_old),
        hash_sha256=f"{project.pk:04d}{author.pk:04d}{index:04d}{hours_old:04d}".ljust(64, "a"),
        idempotency_key=f"dash-{project.pk}-{author.pk}-{index}-{hours_old}-{status}",
        file=f"evidences/{project.pk}/x{index}.jpg",
        status=status,
        description=f"Photo {index}",
    )


@pytest.fixture()
def today():
    return timezone.localdate()


@pytest.fixture()
def site(project, project_context, today):
    """Planning à trois jalons (un terminé, un en retard, un à venir) + tâches."""
    owner = project_context["owner"]
    done = Milestone.objects.create(
        project=project,
        title="Fondations",
        status=MilestoneStatus.DONE,
        planned_date=today - timedelta(days=40),
        actual_date=today - timedelta(days=38),
        order=1,
        created_by=owner,
    )
    late = Milestone.objects.create(
        project=project,
        title="Élévation R+1",
        status=MilestoneStatus.IN_PROGRESS,
        planned_date=today - timedelta(days=9),
        order=2,
        created_by=owner,
    )
    upcoming = Milestone.objects.create(
        project=project,
        title="Toiture",
        status=MilestoneStatus.PLANNED,
        planned_date=today + timedelta(days=30),
        order=3,
        created_by=owner,
    )
    Task.objects.create(
        project=project,
        milestone=done,
        title="Coulage des semelles",
        status=TaskStatus.DONE,
        progress=100,
        planned_end_date=today - timedelta(days=45),
        actual_end_date=today - timedelta(days=44),
        created_by=owner,
    )
    late_task = Task.objects.create(
        project=project,
        milestone=late,
        title="Coffrage dalle R+1",
        status=TaskStatus.IN_PROGRESS,
        progress=40,
        planned_end_date=today - timedelta(days=20),
        assignee=project_context["engineer"],
        created_by=owner,
    )
    Task.objects.create(
        project=project,
        milestone=upcoming,
        title="Charpente",
        status=TaskStatus.TODO,
        progress=0,
        planned_end_date=today + timedelta(days=50),
        created_by=owner,
    )
    return {"done": done, "late": late, "upcoming": upcoming, "late_task": late_task}


@pytest.fixture()
def money(project, project_context):
    """Budget de 50 M FCFA, un poste, deux dépenses approuvées/payées, une en attente."""
    from apps.finance import services

    owner = project_context["owner"]
    finance = project_context["finance"]
    line = services.create_budget_line(
        project=project,
        actor=owner,
        data={"label": "Gros œuvre", "category": "MATERIALS", "planned_amount": 30_000_000},
    )
    created = []
    for index, amount in enumerate((20_000_000, 5_000_000, 1_500_000)):
        expense = services.create_expense(
            project=project,
            actor=finance,
            data={
                "title": f"Dépense {index}",
                "amount": amount,
                "incurred_on": "2026-02-10",
                "budget_line": line.pk,
            },
        )
        created.append(expense)
    services.transition_expense(expense=created[0], actor=finance, action="SUBMIT")
    services.transition_expense(expense=created[0], actor=owner, action="APPROVE")
    services.transition_expense(expense=created[1], actor=finance, action="SUBMIT")
    services.transition_expense(expense=created[1], actor=owner, action="APPROVE")
    services.transition_expense(expense=created[2], actor=finance, action="SUBMIT")
    return {"line": line, "expenses": created}
