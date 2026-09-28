"""Atomicité des opérations financières (MVP-010) — tout ou rien.

Critère de sortie phase 7 : « `transaction.atomic()` sur toute opération financière critique ».
Ces tests provoquent une panne **au dernier moment** (la journalisation) et vérifient qu'aucun
écrit partiel ne subsiste : pas d'écriture au grand livre, pas de statut modifié, pas de
paiement orphelin, pas de justificatif remplacé, pas de ligne budgétaire fantôme.

La journalisation est déclenchée volontairement en fin de service : si une seule partie de
l'opération devait survivre, c'est exactement ce qui se produirait ici.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.utils import timezone

from apps.core.exceptions import KemtaAPIError
from apps.core.models import ActivityLog
from apps.finance.models import (
    BudgetLine,
    Expense,
    ExpenseStatus,
    FinancialTransaction,
    Payment,
)
from apps.finance.services import (
    attach_receipt,
    cancel_payment,
    create_budget_line,
    record_adjustment,
    register_payment,
    transition_expense,
    update_budget_line,
    update_expense,
)
from apps.finance.tests.conftest import BUDGET_URL, EXPENSE_URL

JOURNAL_DOWN = mock.patch(
    "apps.finance.services.log_event", side_effect=RuntimeError("journal indisponible")
)


def _submit(auth_client, project_context, expense) -> None:
    response = auth_client(project_context["finance"]).post(
        f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json"
    )
    assert response.status_code == 200, response.data


@pytest.mark.django_db
def test_approval_is_rolled_back_if_the_journal_fails(
    auth_client, finance_context, project_context, expense
):
    _submit(auth_client, project_context, expense)

    with JOURNAL_DOWN, pytest.raises(RuntimeError):
        transition_expense(expense=expense, actor=project_context["owner"], action="APPROVE")

    expense.refresh_from_db()
    assert expense.status == ExpenseStatus.SUBMITTED  # le statut n'a pas bougé
    assert expense.approved_by_id is None
    assert not FinancialTransaction.objects.exists()
    assert not ActivityLog.objects.filter(action=ActivityLog.Action.EXPENSE_APPROVED).exists()


@pytest.mark.django_db
def test_payment_is_rolled_back_if_the_journal_fails(
    auth_client, finance_context, project_context, expense, transition
):
    _submit(auth_client, project_context, expense)
    assert transition(project_context["owner"], expense, "APPROVE").status_code == 200

    with JOURNAL_DOWN, pytest.raises(RuntimeError):
        register_payment(
            expense=expense,
            actor=project_context["owner"],
            data={"amount": 500_000, "paid_on": timezone.localdate()},
        )

    assert not Payment.objects.exists()
    assert FinancialTransaction.objects.count() == 1  # seul l'engagement reste
    expense.refresh_from_db()
    assert expense.status == ExpenseStatus.APPROVED
    assert not ActivityLog.objects.filter(action=ActivityLog.Action.PAYMENT_RECORDED).exists()


@pytest.mark.django_db
def test_payment_cancellation_is_rolled_back_if_the_journal_fails(
    auth_client, finance_context, project_context, expense, transition, pay
):
    _submit(auth_client, project_context, expense)
    assert transition(project_context["owner"], expense, "APPROVE").status_code == 200
    payment_id = pay(project_context["owner"], expense, 1_200_000).data["payment"]["id"]
    payment = Payment.objects.get(pk=payment_id)

    with JOURNAL_DOWN, pytest.raises(RuntimeError):
        cancel_payment(payment=payment, actor=project_context["owner"], reason="Virement rejeté")

    payment.refresh_from_db()
    assert payment.cancelled_at is None
    assert payment.cancelled_by_id is None
    assert not FinancialTransaction.objects.filter(type="CANCELLATION").exists()
    expense.refresh_from_db()
    assert expense.status == ExpenseStatus.PAID  # le montant reste soldé


@pytest.mark.django_db
def test_adjustment_is_rolled_back_if_the_journal_fails(
    auth_client, finance_context, project_context
):
    project = finance_context["project"]
    with JOURNAL_DOWN, pytest.raises(RuntimeError):
        record_adjustment(
            project=project,
            actor=project_context["owner"],
            data={"amount": 100_000, "direction": "CREDIT", "reason": "Correction d'inventaire"},
        )
    assert not FinancialTransaction.objects.exists()
    summary = auth_client(project_context["owner"]).get(f"/api/projects/{project.pk}/finance/")
    assert summary.data["budget"]["committed"] == 0


@pytest.mark.django_db
def test_budget_line_creation_is_all_or_nothing(
    auth_client, finance_context, project_context, budget_line
):
    project = finance_context["project"]
    with pytest.raises(KemtaAPIError) as exc:
        create_budget_line(
            project=project,
            actor=project_context["owner"],
            data={"label": "Poste trop gros", "planned_amount": 9_000_000},
        )
    assert exc.value.code == "budget_lines_exceed_budget"

    assert not BudgetLine.objects.filter(label="Poste trop gros").exists()
    assert BudgetLine.objects.count() == 1
    assert ActivityLog.objects.filter(action=ActivityLog.Action.BUDGET_LINE_CREATED).count() == 1


@pytest.mark.django_db
def test_expense_update_is_rolled_back_if_the_journal_fails(
    auth_client, finance_context, project_context, expense
):
    with JOURNAL_DOWN, pytest.raises(RuntimeError):
        update_expense(
            expense=expense,
            actor=project_context["finance"],
            data={"amount": 5_000_000, "supplier": "Autre fournisseur"},
        )

    expense.refresh_from_db()
    assert expense.amount == 1_200_000
    assert expense.supplier == "CIMENCAM Douala"
    assert not ActivityLog.objects.filter(action=ActivityLog.Action.EXPENSE_UPDATED).exists()


@pytest.mark.django_db
def test_budget_line_update_is_rolled_back_if_the_journal_fails(
    auth_client, finance_context, project_context, budget_line
):
    with JOURNAL_DOWN, pytest.raises(RuntimeError):
        update_budget_line(
            line=budget_line,
            actor=project_context["owner"],
            data={"planned_amount": 8_000_000, "label": "Nouveau libellé"},
        )

    budget_line.refresh_from_db()
    assert budget_line.planned_amount == 4_000_000
    assert budget_line.label == "Matériaux — ciment et fer"


@pytest.mark.django_db
def test_receipt_is_not_replaced_if_a_later_step_fails(
    auth_client, finance_context, project_context, expense, receipt
):
    client = auth_client(project_context["finance"])
    first = client.post(
        f"/api/expenses/{expense.pk}/receipt/",
        {"file": receipt(b"%PDF-1.7\nfacture initiale", "initiale.pdf")},
        format="multipart",
    )
    assert first.status_code == 201, first.data
    original_hash = first.data["receipt_hash"]

    with JOURNAL_DOWN, pytest.raises(RuntimeError):
        attach_receipt(
            expense=Expense.objects.get(pk=expense.pk),
            actor=project_context["finance"],
            upload=receipt(b"%PDF-1.7\nfacture corrigee", "corrigee.pdf"),
        )

    expense.refresh_from_db()
    assert expense.receipt_hash == original_hash  # l'ancien justificatif reste en place


@pytest.mark.django_db
def test_expense_creation_leaves_nothing_behind_when_a_rule_fails(
    auth_client, finance_context, project_context
):
    """Un refus de règle métier n'écrit ni la dépense, ni l'événement de journal."""
    response = auth_client(project_context["finance"]).post(
        EXPENSE_URL.format(project=finance_context["project"].pk),
        {
            "title": "Facture en doublon",
            "amount": 10_000,
            "incurred_on": "2026-02-01",
            "budget_line": 999_999,
        },
        format="json",
    )
    assert response.status_code == 404
    assert Expense.objects.count() == 0
    assert not ActivityLog.objects.filter(action=ActivityLog.Action.EXPENSE_CREATED).exists()

    # Cas symétrique côté poste budgétaire : la contrainte de libellé unique ne crée rien.
    client = auth_client(project_context["owner"])
    url = BUDGET_URL.format(project=finance_context["project"].pk)
    assert (
        client.post(
            url, {"label": "Poste valide", "planned_amount": 1_000}, format="json"
        ).status_code
        == 201
    )
    duplicated = client.post(url, {"label": "Poste valide", "planned_amount": 1_000}, format="json")
    assert duplicated.status_code == 409
    assert BudgetLine.objects.filter(label="Poste valide").count() == 1
