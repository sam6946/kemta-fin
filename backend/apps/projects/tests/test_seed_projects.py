"""Le seed de développement couvre le périmètre des phases 3 et 4 (données réalistes en FCFA)."""

from io import StringIO

import pytest
from django.core.management import call_command

from apps.organizations.models import Organization
from apps.projects.models import Milestone, Project, ProjectMember, Task
from apps.projects.progress import compute_project_progress
from apps.users.models import User


@pytest.mark.django_db
def test_seed_creates_organizations_projects_and_members():
    call_command("seed_dev", stdout=StringIO())

    assert Organization.objects.count() == 3
    assert Project.objects.count() == 4
    assert ProjectMember.objects.count() >= 15

    project = Project.objects.get(code="RBS-T1")
    assert project.organization.name == "KEMTA Promotion Douala"
    assert project.currency == "XAF"
    assert int(project.budget_total) == 85_000_000
    assert project.status == "ACTIVE"

    # Chaque projet a au moins un responsable et un agent de terrain.
    roles = set(project.members.values_list("role", flat=True))
    assert {"PROJECT_OWNER", "ENGINEER", "FIELD_AGENT", "VALIDATOR", "INVESTOR"} <= roles

    # Les budgets sont tous des entiers FCFA cohérents avec le contexte camerounais.
    for budget in Project.objects.values_list("budget_total", flat=True):
        assert budget == budget.to_integral_value()
        assert 1_000_000 <= budget <= 1_000_000_000


@pytest.mark.django_db
def test_seed_creates_a_consistent_planning():
    """Jalons et tâches de démonstration : statuts, dates et responsables cohérents."""
    call_command("seed_dev", stdout=StringIO())

    assert Milestone.objects.count() == 11
    assert Task.objects.count() == 19

    project = Project.objects.get(code="RBS-T1")
    milestone = project.milestones.get(title="Fondations et soubassement")
    # Un jalon terminé porte toujours sa date réelle (règle serveur).
    assert milestone.actual_date is not None
    assert milestone.tasks.count() == 2
    assert not milestone.is_late  # terminé : un dépassement n'est plus un retard

    # Les responsables désignés sont membres actifs du projet (contrat d'API).
    for task in Task.objects.select_related("assignee", "project"):
        if task.assignee is not None:
            assert ProjectMember.objects.filter(
                project=task.project, user=task.assignee, is_active=True
            ).exists()

    # Une tâche en retard est présente pour la démonstration, jamais une tâche terminale.
    late = [task for task in Task.objects.all() if task.is_late]
    assert late, "le jeu de démonstration doit contenir au moins une tâche en retard"
    assert all(task.status not in {"DONE", "CANCELLED"} for task in late)

    # Dépendance de démonstration : les essais béton suivent la structure.
    essais = Task.objects.get(title="Essais béton 28 jours")
    assert [task.title for task in essais.depends_on.all()] == ["Structure niveau 1"]


@pytest.mark.django_db
def test_seed_progress_matches_the_documented_computation():
    """L'avancement persisté correspond au calcul serveur (pas de valeur figée dans le seed)."""
    call_command("seed_dev", stdout=StringIO())

    for project in Project.objects.all():
        assert project.progress == compute_project_progress(project)
        assert 0 <= project.progress <= 100

    # Le projet en cours a un avancement réaliste (ni 0 % ni 100 %).
    assert 0 < Project.objects.get(code="RBS-T1").progress < 100


@pytest.mark.django_db
def test_seed_is_idempotent_for_projects():
    call_command("seed_dev", stdout=StringIO())
    call_command("seed_dev", stdout=StringIO())
    assert Organization.objects.count() == 3
    assert Project.objects.count() == 4
    assert Milestone.objects.count() == 11
    assert Task.objects.count() == 19
    assert Task.objects.filter(depends_on__isnull=False).count() == 1


@pytest.mark.django_db
def test_seed_skip_projects_creates_users_only():
    call_command("seed_dev", "--skip-projects", stdout=StringIO())
    assert User.objects.count() == 9
    assert Organization.objects.count() == 0
    assert Project.objects.count() == 0
    assert Milestone.objects.count() == 0
    assert Task.objects.count() == 0


@pytest.mark.django_db
def test_seed_creates_field_evidence_with_statuses():
    """La démo contient des preuves dans chaque statut, avec miniatures et sans binaire committé."""
    call_command("seed_dev", stdout=StringIO())

    from apps.evidences.models import Evidence, EvidenceStatus

    assert Evidence.objects.count() == 7
    statuses = set(Evidence.objects.values_list("status", flat=True))
    assert statuses == {
        EvidenceStatus.PENDING,
        EvidenceStatus.VALIDATED,
        EvidenceStatus.REJECTED,
        EvidenceStatus.FLAGGED,
    }

    for evidence in Evidence.objects.select_related("project", "author"):
        # Auteur membre du projet, empreinte valide, miniature prête pour la galerie.
        assert ProjectMember.objects.filter(
            project=evidence.project, user=evidence.author, is_active=True
        ).exists()
        assert len(evidence.hash_sha256) == 64
        assert evidence.thumbnail and evidence.list_version
        assert evidence.file.size > 0
        assert evidence.description

    # Les empreintes sont uniques par projet (contrainte de déduplication).
    seen = set()
    for evidence in Evidence.objects.all():
        key = (evidence.project_id, evidence.hash_sha256)
        assert key not in seen
        seen.add(key)


@pytest.mark.django_db
def test_seed_evidences_are_idempotent():
    call_command("seed_dev", stdout=StringIO())
    call_command("seed_dev", stdout=StringIO())

    from apps.evidences.models import Evidence

    assert Evidence.objects.count() == 7
