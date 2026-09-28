"""Volume de données pour les **tests de charge** (développement / préproduction uniquement).

Ajoute au premier projet de `seed_dev` des jalons, tâches, preuves et dépenses en grand nombre
afin de mesurer les endpoints sur un chantier réaliste (pas sur un projet vide). Les preuves
n'ont pas de fichier réel : elles servent à mesurer la base, pas le disque.

    python manage.py seed_dev && python manage.py seed_loadtest --evidences 2000
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.evidences.models import Evidence, EvidenceStatus
from apps.finance.models import BudgetLine, Expense
from apps.projects.models import Milestone, Project, Task, TaskStatus
from apps.users.models import User


class Command(BaseCommand):
    help = "Ajoute un volume réaliste de données au premier projet de démonstration."

    def add_arguments(self, parser):
        parser.add_argument("--evidences", type=int, default=1000)
        parser.add_argument("--milestones", type=int, default=40)
        parser.add_argument("--tasks", type=int, default=400)
        parser.add_argument("--expenses", type=int, default=300)
        parser.add_argument("--force", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if settings.IS_PRODUCTION and not options["force"]:
            raise CommandError("Refusé en production (données factices de charge).")
        project = Project.objects.order_by("id").first()
        if project is None:
            raise CommandError("Aucun projet : exécutez d'abord `seed_dev`.")
        authors = list(User.objects.filter(role__in=["FIELD_AGENT", "ENGINEER"]).order_by("id"))
        owner = User.objects.filter(role="PROJECT_OWNER").first()
        if not authors or owner is None:
            raise CommandError("Comptes de démonstration absents : exécutez `seed_dev`.")
        today = timezone.localdate()
        now = timezone.now()

        milestones = [
            Milestone(
                project=project,
                title=f"Jalon de charge {index}",
                planned_date=today + timedelta(days=index * 5 - 60),
                order=1000 + index,
                created_by=owner,
            )
            for index in range(options["milestones"])
        ]
        Milestone.objects.bulk_create(milestones)
        milestone_ids = list(
            Milestone.objects.filter(project=project, order__gte=1000).values_list("id", flat=True)
        )
        Task.objects.bulk_create(
            Task(
                project=project,
                milestone_id=milestone_ids[index % len(milestone_ids)],
                title=f"Tâche de charge {index}",
                status=TaskStatus.TODO if index % 3 else TaskStatus.IN_PROGRESS,
                planned_end_date=today + timedelta(days=index % 90 - 30),
                assignee=authors[index % len(authors)],
                created_by=owner,
            )
            for index in range(options["tasks"])
        )
        statuses = [EvidenceStatus.PENDING, EvidenceStatus.VALIDATED, EvidenceStatus.REJECTED]
        Evidence.objects.bulk_create(
            Evidence(
                project=project,
                author=authors[index % len(authors)],
                captured_at=now - timedelta(minutes=index * 7),
                hash_sha256=f"load{index:08d}".ljust(64, "0"),
                idempotency_key=f"load-{index}",
                file=f"evidences/load/{index}.jpg",
                status=statuses[index % 3],
                description=f"Preuve de charge {index}",
            )
            for index in range(options["evidences"])
        )
        line = BudgetLine.objects.filter(project=project).first()
        Expense.objects.bulk_create(
            Expense(
                project=project,
                budget_line=line,
                title=f"Dépense de charge {index}",
                amount=10_000 + index,
                incurred_on=today - timedelta(days=index % 120),
                status="SUBMITTED" if index % 4 == 0 else "APPROVED",
                created_by=owner,
            )
            for index in range(options["expenses"])
        )
        from apps.dashboard.cache import bump_project_version

        bump_project_version(project.pk)
        self.stdout.write(
            self.style.SUCCESS(
                f"Projet #{project.pk} : +{options['milestones']} jalons, +{options['tasks']} tâches, "
                f"+{options['evidences']} preuves, +{options['expenses']} dépenses."
            )
        )
