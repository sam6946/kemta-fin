"""Concurrence (MVP-010) — deux paiements simultanés ne peuvent pas dépasser le dû.

Trois niveaux de preuve :

1. **Verrou** : chaque écriture financière verrouille la ligne du projet
   (`select_for_update()`), ce qui sérialise les opérations d'un même chantier ;
2. **Invariant revérifié après écriture** : même si une valeur périmée avait été lue
   (écriture concurrente glissée entre la lecture et l'écriture), le total payé d'une
   dépense ne peut jamais dépasser son montant — la transaction est annulée entièrement ;
3. **Simultanéité réelle** : deux threads paient en même temps la totalité d'une dépense ;
   exactement un réussit (test exécuté là où le verrouillage de lignes existe — PostgreSQL ;
   ignoré sur SQLite, qui ne l'implémente pas).
"""

from __future__ import annotations

import threading
from decimal import Decimal
from unittest import mock

import pytest
from django.db import connection, connections
from django.utils import timezone

from apps.core.exceptions import KemtaAPIError
from apps.finance.models import (
    Expense,
    ExpenseStatus,
    FinancialTransaction,
    Payment,
    TransactionType,
)
from apps.finance.services import budget_summary, ledger_totals, register_payment
from apps.finance.tests.conftest import EXPENSE_URL


def _submit(auth_client, project_context, expense) -> None:
    response = auth_client(project_context["finance"]).post(
        f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json"
    )
    assert response.status_code == 200, response.data


def _approve(auth_client, project_context, transition, expense) -> None:
    _submit(auth_client, project_context, expense)
    assert transition(project_context["owner"], expense, "APPROVE").status_code == 200


@pytest.mark.django_db
def test_project_row_is_locked_during_a_payment(
    auth_client, finance_context, project_context, expense, transition
):
    """L'écriture verrouille le projet : `SELECT … FOR UPDATE` doit apparaître dans le SQL."""
    if not connection.features.has_select_for_update:
        pytest.skip("Verrouillage de lignes non implémenté par ce moteur (SQLite).")

    _approve(auth_client, project_context, transition, expense)
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured:
        register_payment(
            expense=expense,
            actor=project_context["owner"],
            data={"amount": 100_000, "paid_on": timezone.localdate()},
        )
    assert any("FOR UPDATE" in query["sql"].upper() for query in captured.captured_queries)


@pytest.mark.django_db
def test_stale_outstanding_never_produces_an_incoherent_balance(
    auth_client, finance_context, project_context, expense, transition, pay
):
    """Course entre deux requêtes : la seconde écriture doit être annulée, pas acceptée.

    On simule le pire enchaînement : la requête courante a lu un reste dû **périmé**
    (l'argent venait d'être payé par une autre requête). Le contrôle fait *après* l'insertion
    doit détecter l'incohérence et annuler l'opération en bloc.
    """
    project = finance_context["project"]
    _approve(auth_client, project_context, transition, expense)
    # L'autre requête a soldé la dépense entre-temps.
    assert pay(project_context["owner"], expense, 1_200_000).status_code == 201

    from apps.finance.services import payment_totals as real_totals

    calls = {"count": 0}

    def stale_then_real(target):
        calls["count"] += 1
        # 1er appel (contrôle « avant ») : valeur périmée ; ensuite : la vérité du grand livre.
        if calls["count"] == 1:
            return {"paid": Decimal("0"), "outstanding": Decimal("1200000")}
        return real_totals(target)

    with (
        mock.patch("apps.finance.services.payment_totals", side_effect=stale_then_real),
        pytest.raises(KemtaAPIError) as exc,
    ):
        register_payment(
            expense=Expense.objects.get(pk=expense.pk),
            actor=project_context["owner"],
            data={"amount": 1_200_000, "paid_on": timezone.localdate()},
        )
    assert exc.value.code == "payment_exceeds_outstanding"

    # Rien n'a fuité : un seul paiement (celui de l'autre requête), deux écritures, solde cohérent.
    assert Payment.objects.count() == 1
    assert FinancialTransaction.objects.filter(project=project).count() == 2
    assert ledger_totals(project)["paid"] == Decimal("1200000")
    expense.refresh_from_db()
    assert expense.status == ExpenseStatus.PAID


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
def test_simultaneous_payments_never_overshoot(
    auth_client, finance_context, project_context, expense, transition
):
    """Deux threads paient 1 200 000 FCFA au même instant : un seul passe."""
    if not connection.features.has_select_for_update:
        pytest.skip(
            "Verrouillage de lignes non implémenté par ce moteur (SQLite) : "
            "ce test s'exécute sur PostgreSQL."
        )

    _approve(auth_client, project_context, transition, expense)
    barrier = threading.Barrier(2, timeout=10)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            barrier.wait()
            register_payment(
                expense=Expense.objects.get(pk=expense.pk),
                actor=project_context["owner"],
                data={"amount": 1_200_000, "paid_on": timezone.localdate()},
            )
            with lock:
                outcomes.append("ok")
        except Exception as exc:
            with lock:
                outcomes.append(type(exc).__name__)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == ["KemtaAPIError", "ok"], outcomes
    assert Payment.objects.count() == 1
    assert ledger_totals(expense.project)["paid"] == Decimal("1200000")
    expense.refresh_from_db()
    assert expense.status == ExpenseStatus.PAID


@pytest.mark.django_db
def test_successive_payment_attempts_are_judged_on_the_committed_ledger(
    auth_client, finance_context, project_context, expense, transition, pay
):
    """Aucune valeur n'est mémorisée entre deux requêtes : chaque tentative relit le grand livre."""
    _approve(auth_client, project_context, transition, expense)
    assert pay(project_context["owner"], expense, 700_000).status_code == 201
    assert pay(project_context["owner"], expense, 600_000).status_code == 422
    assert pay(project_context["owner"], expense, 500_000).status_code == 201

    assert ledger_totals(expense.project)["paid"] == Decimal("1200000")
    assert Payment.objects.count() == 2
    expense.refresh_from_db()
    assert expense.status == ExpenseStatus.PAID


@pytest.mark.django_db
def test_expense_creation_is_a_single_controlled_write(
    auth_client, finance_context, project_context
):
    """Créer une dépense ne touche pas au grand livre : le budget n'est engagé qu'à l'approbation."""
    response = auth_client(project_context["finance"]).post(
        EXPENSE_URL.format(project=finance_context["project"].pk),
        {"title": "Repérage topographique", "amount": 90_000, "incurred_on": "2026-02-04"},
        format="json",
    )
    assert response.status_code == 201
    assert FinancialTransaction.objects.count() == 0
    assert ledger_totals(finance_context["project"])["committed"] == 0


@pytest.mark.django_db
def test_a_replayed_approval_engages_the_budget_exactly_once(
    auth_client, finance_context, project_context, expense, transition
):
    """Double clic, rejeu réseau, deux onglets : la même approbation n'engage qu'une fois.

    L'opération étant transactionnelle, le second envoi trouve la dépense déjà engagée : il est
    refusé (`409 invalid_transition`) **sans** écrire une seconde fois au grand livre — le solde
    reste identique à celui d'une approbation unique.
    """
    project = finance_context["project"]
    owner = project_context["owner"]
    submitter = project_context["finance"]
    assert transition(submitter, expense, "SUBMIT").status_code == 200

    assert transition(owner, expense, "APPROVE").status_code == 200
    first = ledger_totals(project)

    replayed = transition(owner, expense, "APPROVE")
    assert replayed.status_code == 409
    assert replayed.data["error"]["code"] == "invalid_transition"

    assert (
        FinancialTransaction.objects.filter(project=project, type=TransactionType.EXPENSE).count()
        == 1
    )
    assert ledger_totals(project) == first
    assert budget_summary(project)["balance"] == project.budget_total - first["committed"]
    expense.refresh_from_db()
    assert expense.status == ExpenseStatus.APPROVED


@pytest.mark.django_db
def test_a_refused_engagement_leaves_no_trace_on_the_budget(
    auth_client, finance_context, project_context, expense, transition
):
    """Un refus (budget dépassé, sans motif) ne doit ni engager, ni desserrer le budget.

    La première dépense est dans l'enveloppe : elle engage 4 000 000 FCFA. La seconde la ferait
    sortir du budget du chantier (11 000 000 pour 10 000 000 prévus) : elle est refusée et ne
    laisse **aucune** trace — ni engagement, ni écriture, ni modification du solde.
    """
    project = finance_context["project"]
    owner = project_context["owner"]
    submitter = project_context["finance"]
    assert transition(submitter, expense, "SUBMIT").status_code == 200
    assert budget_summary(project)["committed"] == 0

    outcomes = []
    for index, amount in enumerate((4_000_000, 7_000_000)):
        created = auth_client(submitter).post(
            EXPENSE_URL.format(project=project.pk),
            {
                "title": f"Dépense hors enveloppe {index}",
                "amount": amount,
                "incurred_on": "2026-02-12",
            },
            format="json",
        )
        assert created.status_code == 201, created.data
        target = Expense.objects.get(pk=created.data["id"])
        assert transition(submitter, target, "SUBMIT").status_code == 200
        refused = transition(owner, target, "APPROVE")
        outcomes.append(refused.status_code)
        target.refresh_from_db()
        if refused.status_code == 422:
            assert refused.data["error"]["code"] == "budget_exceeded"
            assert target.status == ExpenseStatus.SUBMITTED
        else:
            assert refused.status_code == 200, refused.data
            assert target.status == ExpenseStatus.APPROVED

    assert outcomes == [200, 422]

    after = budget_summary(project)
    assert after["committed"] == 4_000_000
    assert after["balance"] == 6_000_000
    assert after["consumption_rate"] == Decimal("40.00")
    assert ledger_totals(project)["committed"] == Decimal("4000000")
    assert FinancialTransaction.objects.filter(project=project).count() == 1
    assert Expense.objects.filter(project=project, status=ExpenseStatus.APPROVED).count() == 1
    assert Expense.objects.filter(project=project, status=ExpenseStatus.SUBMITTED).count() == 2
