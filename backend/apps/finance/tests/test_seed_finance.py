"""Seed financier (phase 7) — cohérence des chiffres de démonstration.

Le seed passe par les services : ces tests vérifient donc que le jeu de démonstration
**raconte la vérité comptable** — total engagé = somme du grand livre, solde = budget − engagé,
aucune dépense approuvée sans écriture, statuts et rejets cohérents.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from apps.core.models import ActivityLog
from apps.finance.models import (
    BudgetLine,
    Expense,
    ExpenseStatus,
    FinancialTransaction,
    Payment,
    TransactionType,
)
from apps.finance.services import budget_summary, ledger_totals
from apps.projects.models import Project, ProjectMember


@pytest.fixture()
def seeded(db):
    call_command("seed_dev", stdout=StringIO())


@pytest.mark.django_db
def test_seed_creates_a_coherent_budget(seeded):
    assert BudgetLine.objects.count() == 18
    for project in Project.objects.all():
        allocated = sum(line.planned_amount for line in project.budget_lines.all())
        # Les postes ne dépassent jamais le budget global, et l'écart reste visible.
        assert allocated <= project.budget_total, project.code
        summary = budget_summary(project)
        assert summary["allocated"] == allocated
        assert summary["unallocated"] == project.budget_total - allocated
        for row in summary["lines"]:
            assert row["planned"] >= 0


@pytest.mark.django_db
def test_seed_totals_match_the_ledger(seeded):
    """Aucun total « saisi » : tout est dérivé des écritures financières."""
    for project in Project.objects.all():
        totals = ledger_totals(project)
        summary = budget_summary(project)
        assert summary["committed"] == totals["committed"], project.code
        assert summary["paid"] == totals["paid"], project.code
        assert summary["balance"] == project.budget_total - totals["committed"]
        assert summary["outstanding"] == totals["committed"] - totals["paid"]

        approved = Expense.objects.filter(project=project, status=ExpenseStatus.APPROVED)
        paid = Expense.objects.filter(project=project, status=ExpenseStatus.PAID)
        expected = sum((expense.amount for expense in list(approved) + list(paid)), start=0)
        assert totals["committed"] == expected, project.code


@pytest.mark.django_db
def test_seed_covers_every_expense_status(seeded):
    assert Expense.objects.count() == 11
    assert set(Expense.objects.values_list("status", flat=True)) == {
        ExpenseStatus.DRAFT,
        ExpenseStatus.SUBMITTED,
        ExpenseStatus.APPROVED,
        ExpenseStatus.PAID,
        ExpenseStatus.REJECTED,
        ExpenseStatus.CANCELLED,
    }
    # Une dépense soldée l'est vraiment : la somme des paiements vivants égale le montant.
    paid_expense = Expense.objects.get(status=ExpenseStatus.PAID)
    assert (
        sum(payment.amount for payment in paid_expense.payments.filter(cancelled_at__isnull=True))
        == paid_expense.amount
    )
    # La démonstration contient une contre-écriture (paiement annulé) et une dépense annulée.
    assert Payment.objects.filter(cancelled_at__isnull=False).count() == 1
    assert FinancialTransaction.objects.filter(type=TransactionType.CANCELLATION).count() == 2
    cancelled = Expense.objects.get(status=ExpenseStatus.CANCELLED)
    assert cancelled.cancelled_at is not None
    assert FinancialTransaction.objects.filter(expense=cancelled).count() == 2


@pytest.mark.django_db
def test_seed_shows_a_budget_warning_and_an_overrun(seeded):
    """La démo doit permettre de voir l'alerte de seuil et un poste dépassé."""
    project = Project.objects.get(code="AKW-T2")
    summary = budget_summary(project)
    assert summary["threshold"] == "WARNING"
    codes = {alert["code"] for alert in summary["alerts"]}
    assert "BUDGET_THRESHOLD_REACHED" in codes
    assert "BUDGET_LINE_EXCEEDED" in codes
    assert (
        ActivityLog.objects.filter(action=ActivityLog.Action.BUDGET_THRESHOLD_REACHED).count() == 1
    )
    # Le dépassement de poste a été assumé explicitement (motif journalisé).
    overrides = [
        log.metadata["over_budget_override"]
        for log in ActivityLog.objects.filter(
            action=ActivityLog.Action.EXPENSE_APPROVED, project=project
        )
        if "over_budget_override" in log.metadata
    ]
    assert overrides and overrides[0].startswith("Avenant n°1")


@pytest.mark.django_db
def test_seed_actors_are_authorised_and_separated(seeded):
    """Créer et approuver ne sont jamais le fait de la même personne.

    Le créateur est un membre actif du projet ; sur les projets de démonstration qui n'ont pas
    encore de responsable financier (brouillons), c'est l'administrateur plateforme qui agit —
    il n'est alors pas membre, et cela reste explicitement autorisé côté produit.
    """
    from apps.users.roles import Role

    for expense in Expense.objects.select_related("created_by", "approved_by", "project"):
        creator = expense.created_by
        if creator.role != Role.PLATFORM_ADMIN:
            assert ProjectMember.objects.filter(
                project=expense.project, user=creator, is_active=True
            ).exists(), expense.title
        if expense.approved_by is not None:
            assert expense.approved_by_id != expense.created_by_id, expense.title
            approver = expense.approved_by
            if approver.role != Role.PLATFORM_ADMIN:
                assert ProjectMember.objects.filter(
                    project=expense.project, user=approver, is_active=True
                ).exists(), expense.title


@pytest.mark.django_db
def test_seed_finance_is_idempotent(seeded):
    call_command("seed_dev", stdout=StringIO())
    assert BudgetLine.objects.count() == 18
    assert Expense.objects.count() == 11
    assert Payment.objects.count() == 5
    assert FinancialTransaction.objects.count() == 13


@pytest.mark.django_db
def test_seed_can_skip_finance():
    call_command("seed_dev", "--skip-finance", stdout=StringIO())
    assert Project.objects.count() == 4
    assert BudgetLine.objects.count() == 0
    assert Expense.objects.count() == 0
    assert FinancialTransaction.objects.count() == 0


@pytest.mark.django_db
def test_seeded_expenses_are_visible_through_the_api(seeded, auth_client):
    """Le parcours de démonstration fonctionne : la synthèse API reflète le seed."""
    from apps.users.models import User

    finance = User.objects.get(phone="+237690000008")
    project = Project.objects.get(code="RBS-T1")
    response = auth_client(finance).get(f"/api/projects/{project.pk}/finance/")
    assert response.status_code == 200, response.data
    assert response.data["budget"]["committed"] == 10_000_000
    assert response.data["budget"]["paid"] == 6_800_000

    expenses = auth_client(finance).get(f"/api/projects/{project.pk}/expenses/")
    assert expenses.status_code == 200
    assert expenses.data["count"] == 6
    assert expenses.data["summary"]["committed"] == 10_000_000

    ledger = auth_client(finance).get(f"/api/projects/{project.pk}/transactions/")
    assert ledger.status_code == 200
    assert ledger.data["totals"]["committed"] == 10_000_000
    assert ledger.data["totals"]["paid"] == 6_800_000


@pytest.mark.django_db
def test_seed_payments_never_exceed_their_expense(seeded):
    """Un état de démonstration crédible : jamais plus payé que dû, et `PAID` = soldé."""
    for expense in Expense.objects.all():
        paid = sum(payment.amount for payment in expense.payments.all() if not payment.is_cancelled)
        assert paid <= expense.amount, expense.title
        if expense.status == ExpenseStatus.PAID:
            assert paid == expense.amount, expense.title
        elif expense.status in {
            ExpenseStatus.DRAFT,
            ExpenseStatus.SUBMITTED,
            ExpenseStatus.REJECTED,
            ExpenseStatus.CANCELLED,
        }:
            assert paid == 0, expense.title


@pytest.mark.django_db
def test_seed_never_engages_more_than_the_budget_without_an_explicit_reason(seeded):
    """Tout dépassement du seed a été assumé : le motif est journalisé (ADR-011)."""
    overrides = [
        log.metadata["over_budget_override"]
        for log in ActivityLog.objects.filter(action=ActivityLog.Action.EXPENSE_APPROVED)
        if "over_budget_override" in (log.metadata or {})
    ]
    for project in Project.objects.all():
        summary = budget_summary(project)
        assert summary["allocated"] <= summary["planned"], project.code
        assert summary["balance"] >= 0, project.code
        committed = summary["committed"]
        assert committed == ledger_totals(project)["committed"], project.code
        breached = [row for row in summary["lines"] if row["committed"] > row["planned"]]
        if breached:
            assert overrides, project.code
    assert len(overrides) >= 1
