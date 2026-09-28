"""Gardes de second niveau (MVP-010) — modèles et services.

Les sérialiseurs refusent déjà ces cas côté API ; ces tests verrouillent les **règles métier
elles-mêmes**, pour qu'un script, une commande de gestion ou l'administration Django ne puisse
pas écrire un montant incohérent. C'est la garantie « le backend reste l'autorité ».
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.core.exceptions import KemtaAPIError
from apps.finance.models import BudgetLine, Expense, ExpenseStatus
from apps.finance.services import (
    consumption_rate,
    create_budget_line,
    create_expense,
    ledger_totals,
    line_committed,
    line_committed_map,
    transition_expense,
    update_budget_line,
    update_expense,
)
from apps.finance.tests.conftest import EXPENSE_URL


@pytest.mark.django_db
def test_budget_line_model_refuses_inconsistent_input(finance_context, project_context):
    project = finance_context["project"]
    actor = project_context["owner"]

    negative = BudgetLine(
        project=project, label="Négatif", category="OTHER", planned_amount=-1, created_by=actor
    )
    with pytest.raises(ValidationError) as error:
        negative.clean()
    assert "planned_amount" in error.value.message_dict

    # Un libellé est unique par projet (contrainte de base, pas seulement de service).
    BudgetLine.objects.create(
        project=project,
        label="Poste unique",
        category="OTHER",
        planned_amount=1_000,
        created_by=actor,
    )
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        BudgetLine.objects.create(
            project=project,
            label="Poste unique",
            category="OTHER",
            planned_amount=2_000,
            created_by=actor,
        )


@pytest.mark.django_db
def test_expense_model_refuses_inconsistent_input(budget_line, project_context, finance_context):
    from apps.projects.models import Project

    project = budget_line.project
    actor = project_context["finance"]

    cents = Expense(
        project=project,
        title="Centimes",
        amount=Decimal("1000.50"),
        currency="XAF",
        incurred_on="2026-02-01",
        created_by=actor,
    )
    with pytest.raises(ValidationError) as error:
        cents.clean()
    assert "amount" in error.value.message_dict

    negative = Expense(
        project=project,
        title="Négatif",
        amount=-5,
        currency="XAF",
        incurred_on="2026-02-01",
        created_by=actor,
    )
    with pytest.raises(ValidationError):
        negative.clean()

    other = Project.objects.create(
        organization=finance_context["organization"],
        name="Chantier distinct",
        code="DIST-1",
        budget_total=1_000_000,
        created_by=actor,
    )
    other_line = BudgetLine.objects.create(
        project=other,
        label="Poste d'un autre projet",
        category="OTHER",
        planned_amount=1,
        created_by=actor,
    )
    mismatched = Expense(
        project=project,
        budget_line=other_line,
        title="Mauvais poste",
        amount=1_000,
        currency="XAF",
        incurred_on="2026-02-01",
        created_by=actor,
    )
    with pytest.raises(ValidationError) as error:
        mismatched.clean()
    assert "budget_line" in error.value.message_dict

    # Une facture ne peut pas être postérieure à la dépense qu'elle justifie.
    future_invoice = Expense(
        project=project,
        title="Facture en avance",
        amount=1_000,
        currency="XAF",
        incurred_on="2026-02-01",
        invoice_date="2026-02-10",
        created_by=actor,
    )
    with pytest.raises(ValidationError) as error:
        future_invoice.clean()
    assert "invoice_date" in error.value.message_dict


@pytest.mark.django_db
def test_service_guards_on_budget_lines(finance_context, project_context):
    project = finance_context["project"]
    actor = project_context["owner"]

    with pytest.raises(KemtaAPIError) as error:
        create_budget_line(project=project, actor=actor, data={"planned_amount": 1_000})
    assert error.value.code == "label_required"

    line = create_budget_line(
        project=project, actor=actor, data={"label": "Poste valide", "planned_amount": 1_000}
    )
    with pytest.raises(KemtaAPIError) as error:
        update_budget_line(line=line, actor=actor, data={"label": "   "})
    assert error.value.code == "label_required"


@pytest.mark.django_db
def test_service_guards_on_expenses(finance_context, project_context):
    project = finance_context["project"]
    actor = project_context["finance"]

    with pytest.raises(KemtaAPIError) as error:
        create_expense(project=project, actor=actor, data={"amount": 1_000, "title": "  "})
    assert error.value.code == "title_required"

    with pytest.raises(KemtaAPIError) as error:
        create_expense(project=project, actor=actor, data={"amount": 1_000, "title": "Sans date"})
    assert error.value.code == "incurred_on_required"

    # Un identifiant de poste non numérique est traité comme introuvable (jamais une 500).
    with pytest.raises(KemtaAPIError) as error:
        create_expense(
            project=project,
            actor=actor,
            data={
                "amount": 1_000,
                "title": "Poste invalide",
                "incurred_on": "2026-02-01",
                "budget_line": "abc",
            },
        )
    assert error.value.code == "budget_line_not_found"

    expense = create_expense(
        project=project,
        actor=actor,
        data={
            "amount": 1_000,
            "title": "Dépense modifiable",
            "incurred_on": "2026-02-01",
            "invoice_number": "FAC-1",
        },
    )
    with pytest.raises(KemtaAPIError) as error:
        update_expense(expense=expense, actor=actor, data={"title": "  "})
    assert error.value.code == "title_required"

    # Reposer le **même** numéro de facture ne doit pas déclencher la détection de doublon.
    updated = update_expense(
        expense=expense,
        actor=actor,
        data={"invoice_number": "FAC-1", "incurred_on": "2026-02-05", "amount": 2_000},
    )
    assert updated.invoice_number == "FAC-1"
    assert str(updated.incurred_on) == "2026-02-05"

    with pytest.raises(KemtaAPIError) as error:
        update_expense(expense=expense, actor=actor, data={"amount": "2000.50"})
    assert error.value.code == "amount_has_cents"


@pytest.mark.django_db
def test_expense_without_budget_line_still_consumes_the_project_budget(
    auth_client, finance_context, project_context, transition
):
    """Une dépense hors poste reste possible : elle consomme le budget global (et rien d'autre)."""
    project = finance_context["project"]
    created = auth_client(project_context["finance"]).post(
        EXPENSE_URL.format(project=project.pk),
        {"title": "Frais divers sans poste", "amount": 300_000, "incurred_on": "2026-02-08"},
        format="json",
    )
    assert created.status_code == 201, created.data
    assert created.data["budget_line"] is None
    assert created.data["budget_line_label"] is None

    submitted = auth_client(project_context["finance"]).post(
        f"/api/expenses/{created.data['id']}/transition/", {"action": "SUBMIT"}, format="json"
    )
    assert submitted.status_code == 200
    approved = transition(
        project_context["owner"], Expense.objects.get(pk=created.data["id"]), "APPROVE"
    )
    assert approved.status_code == 200, approved.data
    assert ledger_totals(project)["committed"] == 300_000
    assert line_committed(project, None) == Decimal("0")


@pytest.mark.django_db
def test_project_without_budget_is_fully_consumed_as_soon_as_anything_is_committed(
    auth_client, finance_context, project_context, transition
):
    """Budget de projet à zéro : tout engagement est un dépassement (taux ramené à 100 %)."""
    project = finance_context["project"]
    project.budget_total = 0
    project.save(update_fields=["budget_total"])
    created = auth_client(project_context["finance"]).post(
        EXPENSE_URL.format(project=project.pk),
        {"title": "Dépense sur budget nul", "amount": 150_000, "incurred_on": "2026-02-08"},
        format="json",
    )
    assert created.status_code == 201
    assert (
        auth_client(project_context["finance"])
        .post(
            f"/api/expenses/{created.data['id']}/transition/", {"action": "SUBMIT"}, format="json"
        )
        .status_code
        == 200
    )
    refused = transition(
        project_context["owner"], Expense.objects.get(pk=created.data["id"]), "APPROVE"
    )
    assert refused.status_code == 422
    assert refused.data["error"]["code"] == "budget_exceeded"

    accepted = transition(
        project_context["owner"],
        Expense.objects.get(pk=created.data["id"]),
        "APPROVE",
        override_reason="Chantier financé hors budget prévisionnel, décision du promoteur.",
    )
    assert accepted.status_code == 200, accepted.data
    summary = auth_client(project_context["owner"]).get(f"/api/projects/{project.pk}/finance/")
    assert summary.data["budget"]["threshold"] == "EXCEEDED"
    assert Decimal(str(summary.data["budget"]["consumption_rate"])) == Decimal("100.00")


@pytest.mark.django_db
def test_committed_map_covers_every_line_by_default(finance_context, project_context, budget_line):
    """`line_committed_map` sans liste explicite agrège bien tous les postes du projet."""
    project = finance_context["project"]
    second = BudgetLine.objects.create(
        project=project,
        label="Second poste",
        category="OTHER",
        planned_amount=500_000,
        created_by=project_context["owner"],
    )
    consumed = line_committed_map(project)
    assert set(consumed) == {budget_line.pk, second.pk}
    assert all(value == Decimal("0") for value in consumed.values())
    assert line_committed_map(project, []) == {}


def test_consumption_rate_is_bounded_and_explicit():
    assert consumption_rate(Decimal("1000"), Decimal("250")) == Decimal("25.00")
    # Budget nul : soit rien n'est engagé (0 %), soit il y a un engagement (100 %, jamais l'infini).
    assert consumption_rate(Decimal("0"), Decimal("0")) == Decimal("0.00")
    assert consumption_rate(Decimal("0"), Decimal("500")) == Decimal("100.00")


@pytest.mark.django_db
def test_expense_transition_requires_the_service_not_the_orm(
    finance_context, project_context, expense
):
    """Le statut ne se contourne pas : passer APPROVED sans écriture laisserait un solde faux."""
    with pytest.raises(KemtaAPIError) as error:
        transition_expense(expense=expense, actor=project_context["owner"], action="REJECT")
    assert error.value.code == "invalid_transition"

    expense.status = ExpenseStatus.APPROVED
    expense.save(update_fields=["status"])
    # Le grand livre ne connaît pas cet engagement : la synthèse reste à zéro (aucune invention).
    assert ledger_totals(expense.project)["committed"] == Decimal("0")


@pytest.mark.django_db
def test_oversized_receipt_is_refused(auth_client, finance_context, project_context, expense):
    """Un justificatif trop volumineux est refusé avant écriture (413 explicite)."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    payload = b"%PDF-1.4\n" + b"x" * (1024 * 1024 + 1)  # dépasse la limite de 1 Mo
    response = auth_client(project_context["finance"]).post(
        f"/api/expenses/{expense.pk}/receipt/",
        {"file": SimpleUploadedFile("enorme.pdf", payload, content_type="application/pdf")},
        format="multipart",
    )
    assert response.status_code == 413
    assert response.data["error"]["code"] == "file_too_large"
    expense.refresh_from_db()
    assert not expense.receipt


@pytest.mark.django_db
def test_settlement_rights_are_a_strict_subset_of_management_rights(
    finance_context, project_context
):
    """`can_settle_finance` exige la capacité de gestion **et** un rôle de pilotage."""
    from apps.finance.access import can_manage_finance, can_settle_finance

    project = finance_context["project"]
    # Sans droit de gestion du tout : jamais d'engagement possible.
    assert can_manage_finance(finance_context["engineer"], project) is False
    assert can_settle_finance(finance_context["engineer"], project) is False
    # Droit de gestion accordé par drapeau à un contractant : il gère, il n'engage pas.
    assert can_manage_finance(finance_context["contractor_finance"], project) is True
    assert can_settle_finance(finance_context["contractor_finance"], project) is False
    # Rôle de pilotage : gestion et engagement.
    assert can_settle_finance(project_context["owner"], project) is True
    assert can_settle_finance(project_context["finance"], project) is True
