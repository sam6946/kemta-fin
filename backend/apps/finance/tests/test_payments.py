"""Paiements (MVP-010) — on ne paie que le dû, et rien ne se supprime jamais.

Règles vérifiées :

* seul un rôle de pilotage financier paie ;
* une dépense doit être **approuvée** avant tout paiement ;
* la somme des paiements ne peut jamais dépasser le montant de la dépense (refus `422`) ;
* une dépense soldée passe automatiquement en `PAID`, et reperd ce statut si un paiement
  est annulé ;
* l'annulation d'un paiement écrit une **contre-écriture** : le grand livre reste complet.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import ActivityLog
from apps.finance.models import (
    ExpenseStatus,
    FinancialTransaction,
    Payment,
    TransactionDirection,
    TransactionType,
)
from apps.finance.services import ledger_totals


def _approve(auth_client, project_context, expense, transition):
    submitted = auth_client(project_context["finance"]).post(
        f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json"
    )
    assert submitted.status_code == 200, submitted.data
    approved = transition(project_context["owner"], expense, "APPROVE")
    assert approved.status_code == 200, approved.data
    return approved


@pytest.mark.django_db
def test_partial_payment_then_settlement(
    auth_client, finance_context, project_context, expense, transition, pay
):
    _approve(auth_client, project_context, expense, transition)

    partial = pay(
        project_context["owner"], expense, 500_000, method="MOBILE_MONEY", reference="MM-4471"
    )
    assert partial.status_code == 201, partial.data
    assert partial.data["payment"]["amount"] == 500_000
    assert partial.data["payment"]["method_label"] == "Mobile Money"
    assert partial.data["payment"]["created_by"]["id"] == project_context["owner"].pk
    assert partial.data["expense"]["status"] == "APPROVED"  # reste dû : 700 000
    assert partial.data["expense"]["paid_amount"] == 500_000
    assert partial.data["expense"]["outstanding_amount"] == 700_000

    final = pay(project_context["owner"], expense, 700_000)
    assert final.status_code == 201, final.data
    assert final.data["expense"]["status"] == "PAID"
    assert final.data["expense"]["outstanding_amount"] == 0

    totals = ledger_totals(expense.project)
    assert totals["committed"] == 1_200_000  # le paiement ne change pas l'engagé
    assert totals["paid"] == 1_200_000

    payments = FinancialTransaction.objects.filter(type=TransactionType.PAYMENT)
    assert [entry.direction for entry in payments] == [
        TransactionDirection.DEBIT,
        TransactionDirection.DEBIT,
    ]
    # Le solde après opération reste celui de l'engagé : payer ne re-consomme pas le budget.
    assert {entry.balance_after for entry in payments} == {8_800_000}


@pytest.mark.django_db
def test_payment_list_exposes_server_totals(
    auth_client, finance_context, project_context, expense, transition, pay
):
    _approve(auth_client, project_context, expense, transition)
    assert pay(project_context["owner"], expense, 200_000).status_code == 201

    response = auth_client(project_context["engineer"]).get(f"/api/expenses/{expense.pk}/payments/")
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["totals"] == {
        "amount": 1_200_000,
        "paid": 200_000,
        "outstanding": 1_000_000,
    }
    assert {method["value"] for method in response.data["methods"]} == {
        "CASH",
        "BANK_TRANSFER",
        "MOBILE_MONEY",
        "CHEQUE",
    }
    assert response.data["results"][0]["is_cancelled"] is False


@pytest.mark.django_db
def test_overpayment_is_refused_and_writes_nothing(
    auth_client, finance_context, project_context, expense, transition, pay
):
    _approve(auth_client, project_context, expense, transition)
    assert pay(project_context["owner"], expense, 1_000_000).status_code == 201

    refused = pay(project_context["owner"], expense, 500_000)
    assert refused.status_code == 422
    assert refused.data["error"]["code"] == "payment_exceeds_outstanding"
    assert refused.data["error"]["details"]["outstanding"] == 200_000

    assert Payment.objects.filter(expense=expense).count() == 1
    assert ledger_totals(expense.project)["paid"] == 1_000_000
    expense.refresh_from_db()
    assert expense.status == ExpenseStatus.APPROVED


@pytest.mark.django_db
def test_payment_requires_an_approved_expense(
    auth_client, finance_context, project_context, expense, pay
):
    draft = pay(project_context["owner"], expense, 100_000)
    assert draft.status_code == 409
    assert draft.data["error"]["code"] == "expense_not_approved"

    assert (
        auth_client(project_context["finance"])
        .post(f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json")
        .status_code
        == 200
    )
    submitted = pay(project_context["owner"], expense, 100_000)
    assert submitted.status_code == 409


@pytest.mark.django_db
def test_payment_rules_on_amount_and_date(
    auth_client, finance_context, project_context, expense, transition, pay
):
    _approve(auth_client, project_context, expense, transition)

    for invalid in (0, -1000):
        response = pay(project_context["owner"], expense, invalid)
        assert response.status_code == 400, response.data

    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
    future = pay(project_context["owner"], expense, 100_000, paid_on=tomorrow)
    assert future.status_code == 400
    assert future.data["error"]["code"] == "payment_date_in_future"
    assert Payment.objects.count() == 0


@pytest.mark.django_db
def test_only_settlement_roles_can_pay(
    auth_client, finance_context, project_context, expense, transition
):
    _approve(auth_client, project_context, expense, transition)
    for actor in ("contractor_finance", "engineer", "investor"):
        response = auth_client(finance_context[actor]).post(
            f"/api/expenses/{expense.pk}/payments/",
            {"amount": 10_000, "paid_on": "2026-02-15"},
            format="json",
        )
        assert response.status_code == 403, (actor, response.data)
    assert Payment.objects.count() == 0


@pytest.mark.django_db
def test_cancelling_a_payment_restores_the_outstanding_amount(
    auth_client, finance_context, project_context, expense, transition, pay
):
    _approve(auth_client, project_context, expense, transition)
    paid = pay(project_context["owner"], expense, 1_200_000)
    assert paid.status_code == 201
    payment_id = paid.data["payment"]["id"]
    assert paid.data["expense"]["status"] == "PAID"

    cancelled = auth_client(project_context["owner"]).post(
        f"/api/payments/{payment_id}/cancel/", {"reason": "Virement revenu impayé"}, format="json"
    )
    assert cancelled.status_code == 200, cancelled.data
    assert cancelled.data["is_cancelled"] is True
    assert cancelled.data["cancelled_by"]["id"] == project_context["owner"].pk

    expense.refresh_from_db()
    assert expense.status == ExpenseStatus.APPROVED  # le montant redevient dû
    totals = ledger_totals(expense.project)
    assert totals["paid"] == 0
    assert totals["committed"] == 1_200_000
    counter = FinancialTransaction.objects.get(type=TransactionType.CANCELLATION)
    assert counter.direction == TransactionDirection.CREDIT
    assert counter.amount == 1_200_000
    assert counter.payment_id == payment_id

    log = ActivityLog.objects.filter(
        action=ActivityLog.Action.PAYMENT_CANCELLED, entity_id=str(payment_id)
    ).latest("created_at")
    assert log.metadata["reason"] == "Virement revenu impayé"

    again = auth_client(project_context["owner"]).post(
        f"/api/payments/{payment_id}/cancel/", {}, format="json"
    )
    assert again.status_code == 409
    assert again.data["error"]["code"] == "payment_already_cancelled"

    # Le paiement redevient exactement payable : la contrainte métier est intacte.
    assert pay(project_context["owner"], expense, 1_200_000).status_code == 201


@pytest.mark.django_db
def test_cancelling_someone_elses_payment_requires_settlement_rights(
    auth_client, finance_context, project_context, expense, transition, pay
):
    _approve(auth_client, project_context, expense, transition)
    payment_id = pay(project_context["owner"], expense, 300_000).data["payment"]["id"]

    refused = auth_client(finance_context["contractor_finance"]).post(
        f"/api/payments/{payment_id}/cancel/", {}, format="json"
    )
    assert refused.status_code == 403
    assert Payment.objects.get(pk=payment_id).cancelled_at is None


@pytest.mark.django_db
def test_payment_logs_author_date_and_amount(
    auth_client, finance_context, project_context, expense, transition, pay
):
    _approve(auth_client, project_context, expense, transition)
    response = pay(project_context["owner"], expense, 400_000, method="BANK_TRANSFER")
    assert response.status_code == 201

    log = ActivityLog.objects.filter(
        action=ActivityLog.Action.PAYMENT_RECORDED, entity_id=str(response.data["payment"]["id"])
    ).latest("created_at")
    assert log.actor_id == project_context["owner"].pk
    assert log.project_id == expense.project_id
    assert log.metadata["amount"] == 400_000  # FCFA entiers, jamais de centimes
    assert log.metadata["method"] == "BANK_TRANSFER"
    assert log.metadata["paid_on"] == "2026-02-15"
    assert log.metadata["outstanding"] == 800_000
    assert log.created_at is not None
