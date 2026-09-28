"""Grand livre, seuils budgétaires et justificatifs (MVP-010).

Trois garanties structurelles :

* le grand livre est **append-only** — aucune modification, aucune suppression, ni par le
  modèle, ni par le queryset, ni par l'API ;
* les seuils 80 % / 100 % sont journalisés **au franchissement** (pas à chaque écriture) ;
* un dépassement est refusé (`422`) sauf dépassement **motivé**, alors tracé ;
* un justificatif est validé par son contenu réel, jamais par son extension.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.models import Sum

from apps.core.models import ActivityLog
from apps.finance.models import (
    BudgetLine,
    Expense,
    FinancialTransaction,
    Payment,
    TransactionDirection,
    TransactionType,
)
from apps.finance.services import (
    budget_summary,
    cancel_payment,
    record_adjustment,
    register_payment,
    transition_expense,
)
from apps.finance.tests.conftest import EXPENSE_URL, TRANSACTIONS_URL

OVER_BUDGET_REASON = "Dépassement validé par le comité de pilotage du 12 février."


def _expense_on(auth_client, project_context, project, amount, **overrides):
    response = auth_client(project_context["finance"]).post(
        EXPENSE_URL.format(project=project.pk),
        {
            "title": overrides.pop("title", f"Dépense {amount}"),
            "amount": amount,
            "incurred_on": "2026-02-12",
            **overrides,
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    return response.data["id"]


def _approve(auth_client, project_context, transition, expense_id, **extra):
    submitted = auth_client(project_context["finance"]).post(
        f"/api/expenses/{expense_id}/transition/", {"action": "SUBMIT"}, format="json"
    )
    assert submitted.status_code == 200, submitted.data
    return transition(
        project_context["owner"], Expense.objects.get(pk=expense_id), "APPROVE", **extra
    )


# --------------------------------------------------------------------------- grand livre
@pytest.mark.django_db
def test_ledger_is_append_only(auth_client, finance_context, project_context, expense, transition):
    assert _approve(auth_client, project_context, transition, expense.pk).status_code == 200
    entry = FinancialTransaction.objects.get()
    original_amount = entry.amount

    entry.amount = Decimal("1")
    with pytest.raises(IntegrityError), transaction.atomic():
        entry.save(update_fields=["amount"])

    with pytest.raises(IntegrityError), transaction.atomic():
        entry.delete()

    with pytest.raises(IntegrityError), transaction.atomic():
        FinancialTransaction.objects.filter(pk=entry.pk).update(amount=Decimal("1"))

    with pytest.raises(IntegrityError), transaction.atomic():
        FinancialTransaction.objects.filter(pk=entry.pk).delete()

    entry.refresh_from_db()
    assert entry.amount == original_amount


@pytest.mark.django_db
def test_transactions_endpoint_exposes_the_ledger_and_server_totals(
    auth_client, finance_context, project_context, expense, transition, pay
):
    project = finance_context["project"]
    assert _approve(auth_client, project_context, transition, expense.pk).status_code == 200
    assert pay(project_context["owner"], expense, 200_000).status_code == 201
    adjustment = auth_client(project_context["owner"]).post(
        f"/api/projects/{project.pk}/adjustments/",
        {"amount": 50_000, "direction": "CREDIT", "reason": "Correction d'inventaire"},
        format="json",
    )
    assert adjustment.status_code == 201, adjustment.data

    response = auth_client(project_context["engineer"]).get(
        TRANSACTIONS_URL.format(project=project.pk)
    )
    assert response.status_code == 200
    assert response.data["count"] == 3
    assert response.data["totals"] == {
        "committed": 1_150_000,  # 1 200 000 engagés − 50 000 d'ajustement créditeur
        "paid": 200_000,
        "adjustments": -50_000,
        "balance": 8_850_000,
    }
    first = response.data["results"][0]
    assert first["type_label"] == "Ajustement"
    assert first["direction_label"] == "Crédit (libère le budget)"
    assert first["amount"] == 50_000
    assert first["balance_after"] == 8_850_000
    assert first["created_by"]["id"] == project_context["owner"].pk

    filtered = auth_client(project_context["engineer"]).get(
        TRANSACTIONS_URL.format(project=project.pk), {"type": "payment"}
    )
    assert filtered.data["count"] == 1
    assert filtered.data["results"][0]["expense_title"] == expense.title

    unknown = auth_client(project_context["engineer"]).get(
        TRANSACTIONS_URL.format(project=project.pk), {"type": "INCONNU"}
    )
    assert unknown.status_code == 400
    assert unknown.data["error"]["code"] == "invalid_type"


@pytest.mark.django_db
def test_ledger_totals_are_computed_in_sql_not_accumulated(
    auth_client, finance_context, project_context, expense, transition, pay
):
    """Le solde est toujours dérivé du grand livre : le poser en Python serait une source d'écart."""
    project = finance_context["project"]
    assert _approve(auth_client, project_context, transition, expense.pk).status_code == 200
    assert pay(project_context["owner"], expense, 300_000).status_code == 201
    payment = Payment.objects.get()
    cancel_payment(payment=payment, actor=project_context["owner"], reason="Virement rejeté")
    counter = FinancialTransaction.objects.get(type=TransactionType.CANCELLATION)

    assert counter.direction == TransactionDirection.CREDIT
    rows = FinancialTransaction.objects.filter(project=project)
    assert rows.count() == 3
    # Somme **brute** des écritures : l'engagement, le paiement et sa contre-écriture
    # restent tous les trois dans le grand livre (1,2 M + 300 k + 300 k).
    assert rows.aggregate(total=Sum("amount"))["total"] == Decimal("1800000")
    balance = rows.order_by("-id").first().balance_after
    assert balance == 8_800_000  # engagement intact : seul le décaissement a été annulé

    summary = auth_client(project_context["owner"]).get(f"/api/projects/{project.pk}/finance/")
    assert summary.data["budget"]["committed"] == 1_200_000
    assert summary.data["budget"]["paid"] == 0
    assert summary.data["budget"]["outstanding"] == 1_200_000
    assert summary.data["budget"]["balance"] == 8_800_000


# --------------------------------------------------------------------------- seuils
@pytest.mark.django_db
def test_thresholds_are_logged_once_at_crossing(
    auth_client, finance_context, project_context, transition
):
    project = finance_context["project"]
    project.budget_total = 1_000_000
    project.save(update_fields=["budget_total"])

    # 50 % : aucun seuil franchi.
    assert (
        _approve(
            auth_client,
            project_context,
            transition,
            _expense_on(auth_client, project_context, project, 500_000),
        ).status_code
        == 200
    )
    assert not ActivityLog.objects.filter(
        action=ActivityLog.Action.BUDGET_THRESHOLD_REACHED
    ).exists()

    # 90 % : le seuil des 80 % est franchi, une seule fois.
    assert (
        _approve(
            auth_client,
            project_context,
            transition,
            _expense_on(auth_client, project_context, project, 400_000),
        ).status_code
        == 200
    )
    crossings = ActivityLog.objects.filter(action=ActivityLog.Action.BUDGET_THRESHOLD_REACHED)
    assert crossings.count() == 1
    assert crossings.get().metadata["threshold_percent"] == 80
    assert crossings.get().metadata["committed"] == 900_000
    assert crossings.get().project_id == project.pk

    # 95 % : au-dessus du seuil, aucune nouvelle alerte de franchissement.
    assert (
        _approve(
            auth_client,
            project_context,
            transition,
            _expense_on(auth_client, project_context, project, 50_000),
        ).status_code
        == 200
    )
    assert (
        ActivityLog.objects.filter(action=ActivityLog.Action.BUDGET_THRESHOLD_REACHED).count() == 1
    )
    assert not ActivityLog.objects.filter(action=ActivityLog.Action.BUDGET_EXCEEDED).exists()

    summary = auth_client(project_context["owner"]).get(f"/api/projects/{project.pk}/finance/")
    alerts = {alert["code"]: alert for alert in summary.data["alerts"]}
    assert alerts["BUDGET_THRESHOLD_REACHED"]["severity"] == "warning"
    assert alerts["BUDGET_THRESHOLD_REACHED"]["amount"] == 50_000  # reste disponible
    assert summary.data["budget"]["threshold"] == "WARNING"
    assert Decimal(str(summary.data["budget"]["consumption_rate"])) == Decimal("95.00")


@pytest.mark.django_db
def test_exceeding_the_budget_is_refused_then_allowed_with_a_reason(
    auth_client, finance_context, project_context, transition
):
    project = finance_context["project"]
    project.budget_total = 1_000_000
    project.save(update_fields=["budget_total"])
    expense_id = _expense_on(auth_client, project_context, project, 1_200_000, title="Cuve d'eau")
    assert (
        auth_client(project_context["finance"])
        .post(f"/api/expenses/{expense_id}/transition/", {"action": "SUBMIT"}, format="json")
        .status_code
        == 200
    )

    refused = transition(project_context["owner"], Expense.objects.get(pk=expense_id), "APPROVE")
    assert refused.status_code == 422
    assert refused.data["error"]["code"] == "budget_exceeded"
    assert refused.data["error"]["details"]["overruns"][0]["over"] == 200_000
    assert refused.data["error"]["details"]["min_override_reason_length"] == 10
    assert FinancialTransaction.objects.count() == 0

    too_short = transition(
        project_context["owner"],
        Expense.objects.get(pk=expense_id),
        "APPROVE",
        override_reason="ok",
    )
    assert too_short.status_code == 422
    assert FinancialTransaction.objects.count() == 0

    accepted = transition(
        project_context["owner"],
        Expense.objects.get(pk=expense_id),
        "APPROVE",
        override_reason=OVER_BUDGET_REASON,
    )
    assert accepted.status_code == 200, accepted.data
    assert accepted.data["status"] == "APPROVED"

    log = ActivityLog.objects.filter(
        action=ActivityLog.Action.EXPENSE_APPROVED, entity_id=str(expense_id)
    ).latest("created_at")
    assert log.metadata["over_budget_override"] == OVER_BUDGET_REASON

    # Le dépassement est journalisé au franchissement des 100 % et reste visible dans la synthèse.
    exceeded = ActivityLog.objects.get(action=ActivityLog.Action.BUDGET_EXCEEDED)
    assert exceeded.metadata["threshold_percent"] == 100
    assert exceeded.metadata["committed"] == 1_200_000
    summary = auth_client(project_context["owner"]).get(f"/api/projects/{project.pk}/finance/")
    assert summary.data["budget"]["threshold"] == "EXCEEDED"
    assert summary.data["alerts"][0]["code"] == "BUDGET_EXCEEDED"
    assert summary.data["alerts"][0]["severity"] == "critical"
    assert summary.data["alerts"][0]["amount"] == 200_000


@pytest.mark.django_db
def test_line_overrun_is_reported(
    auth_client, finance_context, project_context, budget_line, transition
):
    """Un dépassement de poste est signalé même si le budget global reste tenu."""
    project = finance_context["project"]
    expense_id = _expense_on(
        auth_client,
        project_context,
        project,
        4_500_000,
        title="Fer à béton supplémentaire",
        budget_line=budget_line.pk,
    )
    assert (
        auth_client(project_context["finance"])
        .post(f"/api/expenses/{expense_id}/transition/", {"action": "SUBMIT"}, format="json")
        .status_code
        == 200
    )

    # Le poste est dépassé : la dépense n'est engagée qu'avec un motif explicite.
    refused = transition(project_context["owner"], Expense.objects.get(pk=expense_id), "APPROVE")
    assert refused.status_code == 422
    assert refused.data["error"]["details"]["overruns"][0]["budget_line"] == budget_line.pk
    assert refused.data["error"]["details"]["overruns"][0]["over"] == 500_000

    approved = transition(
        project_context["owner"],
        Expense.objects.get(pk=expense_id),
        "APPROVE",
        override_reason="Avenant matériaux validé par la maîtrise d'ouvrage.",
    )
    assert approved.status_code == 200, approved.data

    summary = auth_client(project_context["owner"]).get(f"/api/projects/{project.pk}/finance/")
    alerts = [alert for alert in summary.data["alerts"] if alert["code"] == "BUDGET_LINE_EXCEEDED"]
    assert len(alerts) == 1
    assert alerts[0]["budget_line"] == budget_line.pk
    assert alerts[0]["amount"] == 500_000
    line_summary = next(
        row for row in summary.data["lines"] if row["budget_line"] == budget_line.pk
    )
    assert line_summary["committed"] == 4_500_000
    assert line_summary["planned"] == 4_000_000


# --------------------------------------------------------------------------- ajustements
@pytest.mark.django_db
def test_adjustments_are_motivated_and_signed(auth_client, finance_context, project_context):
    project = finance_context["project"]
    client = auth_client(project_context["owner"])

    too_short = client.post(
        f"/api/projects/{project.pk}/adjustments/",
        {"amount": 10_000, "direction": "DEBIT", "reason": "ok"},
        format="json",
    )
    assert too_short.status_code == 400  # première barrière : le sérialiseur
    assert "reason" in too_short.data["error"]["details"]

    # Seconde barrière (le service est l'autorité, même appelé sans passer par l'API).
    from apps.core.exceptions import KemtaAPIError

    with pytest.raises(KemtaAPIError) as exc:
        record_adjustment(
            project=project,
            actor=project_context["owner"],
            data={"amount": 10_000, "direction": "DEBIT", "reason": "oups"},
        )
    assert exc.value.code == "reason_required"

    debit = client.post(
        f"/api/projects/{project.pk}/adjustments/",
        {"amount": 2_000_000, "direction": "DEBIT", "reason": "Facture oubliée de décembre"},
        format="json",
    )
    assert debit.status_code == 201, debit.data
    assert debit.data["type"] == "ADJUSTMENT"
    assert debit.data["direction"] == "DEBIT"
    assert debit.data["balance_after"] == 8_000_000

    credit = client.post(
        f"/api/projects/{project.pk}/adjustments/",
        {"amount": 500_000, "direction": "CREDIT", "reason": "Remise négociée fournisseur"},
        format="json",
    )
    assert credit.status_code == 201
    assert credit.data["balance_after"] == 8_500_000

    summary = client.get(f"/api/projects/{project.pk}/finance/")
    assert summary.data["budget"]["committed"] == 1_500_000
    transactions = client.get(TRANSACTIONS_URL.format(project=project.pk))
    assert transactions.data["totals"]["adjustments"] == 1_500_000

    log = ActivityLog.objects.filter(action=ActivityLog.Action.ADJUSTMENT_RECORDED).latest(
        "created_at"
    )
    assert log.actor_id == project_context["owner"].pk
    assert log.metadata["reason"] == "Remise négociée fournisseur"
    assert log.metadata["balance_after"] == 8_500_000


@pytest.mark.django_db
def test_adjustment_above_the_budget_logs_the_crossing(
    auth_client, finance_context, project_context
):
    project = finance_context["project"]
    project.budget_total = 1_000_000
    project.save(update_fields=["budget_total"])

    response = auth_client(project_context["owner"]).post(
        f"/api/projects/{project.pk}/adjustments/",
        {"amount": 900_000, "direction": "DEBIT", "reason": "Régularisation de facture"},
        format="json",
    )
    assert response.status_code == 201, response.data
    assert ActivityLog.objects.filter(action=ActivityLog.Action.BUDGET_THRESHOLD_REACHED).exists()


@pytest.mark.django_db
def test_adjustment_requires_settlement_rights(auth_client, finance_context, project_context):
    project = finance_context["project"]
    response = auth_client(finance_context["contractor_finance"]).post(
        f"/api/projects/{project.pk}/adjustments/",
        {"amount": 10_000, "direction": "CREDIT", "reason": "Tentative"},
        format="json",
    )
    assert response.status_code == 403
    assert FinancialTransaction.objects.count() == 0


# --------------------------------------------------------------------------- justificatifs
@pytest.mark.django_db
def test_receipt_is_stored_hashed_and_served_privately(
    auth_client, finance_context, project_context, expense, receipt
):
    client = auth_client(project_context["finance"])
    upload = client.post(
        f"/api/expenses/{expense.pk}/receipt/", {"file": receipt()}, format="multipart"
    )
    assert upload.status_code == 201, upload.data
    assert upload.data["receipt_url"] == f"/api/expenses/{expense.pk}/receipt/"
    assert len(upload.data["receipt_hash"]) == 64  # SHA-256 hexadécimal

    download = client.get(f"/api/expenses/{expense.pk}/receipt/")
    assert download.status_code == 200
    assert download["Content-Type"] == "application/pdf"
    assert download["Cache-Control"] == "private, max-age=60"
    assert "inline" in download["Content-Disposition"]
    payload = b"".join(download.streaming_content)
    assert payload.startswith(b"%PDF-")

    assert ActivityLog.objects.filter(
        action=ActivityLog.Action.EXPENSE_RECEIPT_ATTACHED, entity_id=str(expense.pk)
    ).exists()


@pytest.mark.django_db
def test_receipt_content_is_validated(
    auth_client, finance_context, project_context, expense, receipt
):
    client = auth_client(project_context["finance"])
    text = client.post(
        f"/api/expenses/{expense.pk}/receipt/",
        {"file": receipt(b"ceci n'est pas une facture", "notes.txt", "text/plain")},
        format="multipart",
    )
    assert text.status_code == 415
    assert text.data["error"]["code"] == "unsupported_media_type"

    empty = client.post(
        f"/api/expenses/{expense.pk}/receipt/",
        {"file": receipt(b"", "vide.pdf")},
        format="multipart",
    )
    assert empty.status_code == 400
    assert empty.data["error"]["code"] == "file_empty"

    missing = client.post(f"/api/expenses/{expense.pk}/receipt/", {}, format="multipart")
    assert missing.status_code == 400
    assert missing.data["error"]["code"] == "file_required"

    # Un vrai JPEG par signature est accepté, même sans extension explicite.
    jpeg = client.post(
        f"/api/expenses/{expense.pk}/receipt/",
        {"file": receipt(b"\xff\xd8\xff\xe0facture", "photo.jpg", "image/jpeg")},
        format="multipart",
    )
    assert jpeg.status_code == 201, jpeg.data
    assert jpeg.data["receipt_url"].endswith(f"/api/expenses/{expense.pk}/receipt/")


@pytest.mark.django_db
def test_receipt_access_follows_finance_permissions(
    auth_client, finance_context, project_context, expense, receipt
):
    client = auth_client(project_context["finance"])
    assert (
        client.post(
            f"/api/expenses/{expense.pk}/receipt/", {"file": receipt()}, format="multipart"
        ).status_code
        == 201
    )

    # Sans droit de gestion : lecture possible, dépôt refusé.
    engineer = auth_client(finance_context["engineer"])
    assert engineer.get(f"/api/expenses/{expense.pk}/receipt/").status_code == 200
    assert (
        engineer.post(
            f"/api/expenses/{expense.pk}/receipt/", {"file": receipt()}, format="multipart"
        ).status_code
        == 403
    )

    # Hors périmètre projet : invisible.
    assert (
        auth_client(finance_context["stranger"])
        .get(f"/api/expenses/{expense.pk}/receipt/")
        .status_code
        == 404
    )


@pytest.mark.django_db
def test_receipt_missing_returns_404(auth_client, finance_context, project_context, expense):
    response = auth_client(project_context["engineer"]).get(f"/api/expenses/{expense.pk}/receipt/")
    assert response.status_code == 404
    assert response.data["error"]["code"] == "receipt_not_available"


@pytest.mark.django_db
def test_receipt_can_be_replaced(auth_client, finance_context, project_context, expense, receipt):
    client = auth_client(project_context["finance"])
    first = client.post(
        f"/api/expenses/{expense.pk}/receipt/",
        {"file": receipt(b"%PDF-1.7\nversion 1")},
        format="multipart",
    )
    assert first.status_code == 201
    second = client.post(
        f"/api/expenses/{expense.pk}/receipt/",
        {"file": receipt(b"\x89PNG\r\n\x1a\nfacture", "photo.png", "image/png")},
        format="multipart",
    )
    assert second.status_code == 201, second.data
    assert second.data["receipt_hash"] != first.data["receipt_hash"]
    assert len(Payment.objects.all()) == 0  # aucun effet de bord financier
    assert len(BudgetLine.objects.all()) == 1


# --------------------------------------------------------------------------- accès aux services
@pytest.mark.django_db
def test_services_refuse_writes_outside_the_transaction_boundary(
    finance_context, project_context, expense
):
    """Les règles vivent dans les services : même appelées directement, elles contrôlent les droits."""
    from apps.core.exceptions import KemtaAPIError

    stranger = finance_context["stranger"]
    with pytest.raises(KemtaAPIError) as exc:
        register_payment(expense=expense, actor=stranger, data={"amount": 1_000})
    assert exc.value.code == "permission_denied"

    with pytest.raises(KemtaAPIError):
        transition_expense(expense=expense, actor=stranger, action="SUBMIT")

    with pytest.raises(KemtaAPIError):
        record_adjustment(
            project=expense.project, actor=stranger, data={"amount": 1, "reason": "test"}
        )

    payment = Payment.objects.create(
        expense=expense,
        amount=1_000,
        paid_on="2026-02-15",
        created_by=expense.created_by,
    )
    with pytest.raises(KemtaAPIError):
        cancel_payment(payment=payment, actor=stranger)
    payment.refresh_from_db()
    assert payment.cancelled_at is None


@pytest.mark.django_db
def test_every_entry_stores_the_balance_of_the_operation(
    auth_client, finance_context, project_context, expense, transition, pay
):
    """Chaque écriture porte le solde **après** opération : l'historique se relit sans recalcul.

    On rejoue le grand livre dans l'ordre et on vérifie que `balance_after` correspond, pour
    chaque ligne, au solde que l'opération a réellement produit — y compris l'annulation d'un
    paiement, qui ne libère pas l'engagement de la dépense.
    """
    project = finance_context["project"]
    owner = project_context["owner"]
    assert transition(project_context["finance"], expense, "SUBMIT").status_code == 200
    assert transition(owner, expense, "APPROVE").status_code == 200
    assert pay(owner, expense, 700_000).status_code == 201
    payment = Payment.objects.get(expense=expense)
    assert (
        auth_client(owner)
        .post(f"/api/payments/{payment.pk}/cancel/", {"reason": "Virement rejeté"}, format="json")
        .status_code
        == 200
    )
    adjustment = auth_client(owner).post(
        f"/api/projects/{project.pk}/adjustments/",
        {"amount": 250_000, "direction": "DEBIT", "reason": "Gardiennage du chantier"},
        format="json",
    )
    assert adjustment.status_code == 201, adjustment.data

    committed = Decimal("0")
    entries = list(
        FinancialTransaction.objects.filter(project=project).order_by("created_at", "id")
    )
    assert len(entries) == 4
    for entry in entries:
        if entry.type in {TransactionType.EXPENSE, TransactionType.ADJUSTMENT} and (
            entry.direction == TransactionDirection.DEBIT
        ):
            committed += entry.amount
        elif entry.type == TransactionType.ADJUSTMENT or (
            entry.type == TransactionType.CANCELLATION and entry.payment_id is None
        ):
            committed -= entry.amount
        # Une contre-écriture de paiement annule un décaissement : l'engagement reste dû.
        assert entry.balance_after == project.budget_total - committed, entry.type

    assert committed == Decimal("1450000")
    assert entries[-1].balance_after == project.budget_total - committed
    assert budget_summary(project)["committed"] == committed
