"""Postes budgétaires (MVP-010) : allocation, révisions, suppression logique, totaux serveur.

Ce que ces tests verrouillent :

* un poste est **toujours** rattaché au projet de l'URL et le montant prévu reste un entier FCFA ;
* **rien** de ce qui est envoyé par le client n'est repris comme total (`committed_amount`,
  `expense_count`, `project`) : tout est recalculé depuis le grand livre ;
* la somme des postes ne peut pas dépasser le budget global (contrôle par **écart** en révision) ;
* les révisions et suppressions sont **journalisées avec l'ancienne et la nouvelle valeur** ;
* la liste des postes reste à nombre de requêtes **constant** (pas de N+1).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.models import ActivityLog
from apps.finance.models import BudgetLine, Expense, ExpenseStatus, FinancialTransaction
from apps.finance.services import budget_summary, register_payment
from apps.finance.tests.conftest import BUDGET_URL, EXPENSE_URL


def _url(project):
    return BUDGET_URL.format(project=project.pk)


def _line(project):
    return BudgetLine.objects.get(project=project, label="Gros œuvre — fondations")


@pytest.mark.django_db
def test_created_line_is_scoped_to_its_project_with_server_values(
    auth_client, finance_context, project_context
):
    project = finance_context["project"]
    response = auth_client(project_context["owner"]).post(
        _url(project),
        {
            "label": "Gros œuvre — fondations",
            "category": "MATERIALS",
            "planned_amount": 4_000_000,
            "notes": "Ciment, fer à béton, agrégats",
            "order": 1,
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    body = response.data
    assert body["project"] == project.pk
    assert body["planned_amount"] == 4_000_000
    assert body["category_label"]
    # Valeurs calculées côté serveur, pas saisies : un poste neuf n'a rien consommé.
    assert body["committed_amount"] == 0
    assert body["expense_count"] == 0
    assert body["permissions"]["manage_finance"] is True

    summary = budget_summary(project)
    assert summary["planned"] == 10_000_000
    assert summary["allocated"] == 4_000_000
    assert summary["unallocated"] == 6_000_000
    assert summary["committed"] == 0
    assert summary["balance"] == 10_000_000
    assert summary["consumption_rate"] == Decimal("0.00")
    assert summary["threshold"] == "OK"


@pytest.mark.django_db
def test_client_supplied_totals_are_ignored(auth_client, finance_context, project_context):
    """Un client qui « pousse » un consommé ou un autre projet n'est jamais cru."""
    project = finance_context["project"]
    response = auth_client(project_context["owner"]).post(
        _url(project),
        {
            "label": "Poste piégé",
            "category": "OTHER",
            "planned_amount": 1_000_000,
            "committed_amount": 999_999,
            "expense_count": 42,
            "deleted_at": timezone.now().isoformat(),
            "project": finance_context["organization"].pk,
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["project"] == project.pk
    assert response.data["committed_amount"] == 0
    assert response.data["expense_count"] == 0
    line = BudgetLine.objects.get(pk=response.data["id"])
    assert line.project_id == project.pk
    assert line.deleted_at is None


@pytest.mark.django_db
def test_planned_amount_must_be_an_integer_number_of_fcfa(
    auth_client, finance_context, project_context
):
    project = finance_context["project"]
    client = auth_client(project_context["owner"])

    cents = client.post(
        _url(project),
        {"label": "Centimes", "category": "OTHER", "planned_amount": "1000000.50"},
        format="json",
    )
    assert cents.status_code == 400
    assert cents.data["error"]["code"] == "amount_has_cents"

    negative = client.post(
        _url(project),
        {"label": "Négatif", "category": "OTHER", "planned_amount": -1},
        format="json",
    )
    assert negative.status_code == 400
    assert negative.data["error"]["code"] == "amount_invalid"

    # Un poste à zéro est accepté : il documente une enveloppe encore à arbitrer.
    zero = client.post(
        _url(project),
        {"label": "À arbitrer", "category": "OTHER", "planned_amount": 0},
        format="json",
    )
    assert zero.status_code == 201, zero.data
    assert zero.data["planned_amount"] == 0

    assert BudgetLine.objects.count() == 1  # seul le poste à zéro a été écrit


@pytest.mark.django_db
def test_labels_are_unique_per_project_not_globally(
    auth_client, finance_context, project_context, budget_line
):
    from apps.projects.models import Project

    project = finance_context["project"]
    owner = project_context["owner"]
    duplicate = auth_client(owner).post(
        _url(project),
        {"label": budget_line.label, "category": "OTHER", "planned_amount": 1_000},
        format="json",
    )
    assert duplicate.status_code == 409
    assert duplicate.data["error"]["code"] == "budget_line_already_exists"

    # Le même libellé reste valable sur un autre chantier : l'unicité est par projet.
    other = Project.objects.create(
        organization=finance_context["organization"],
        name="Chantier voisin — Logbaba",
        code="CV-LOG",
        budget_total=1_000_000,
        status="ACTIVE",
        created_by=owner,
    )
    twin = BudgetLine.objects.create(
        project=other,
        label=budget_line.label,
        category="OTHER",
        planned_amount=1_000,
        created_by=owner,
    )
    assert twin.pk != budget_line.pk


@pytest.mark.django_db
def test_allocation_cannot_exceed_the_project_budget(auth_client, finance_context, project_context):
    project = finance_context["project"]
    client = auth_client(project_context["owner"])
    assert (
        client.post(
            _url(project),
            {"label": "Enveloppe A", "category": "OTHER", "planned_amount": 8_000_000},
            format="json",
        ).status_code
        == 201
    )
    refused = client.post(
        _url(project),
        {"label": "Enveloppe B", "category": "OTHER", "planned_amount": 3_000_000},
        format="json",
    )
    assert refused.status_code == 422
    assert refused.data["error"]["code"] == "budget_lines_exceed_budget"
    details = refused.data["error"]["details"]
    assert details["allocated"] == 11_000_000
    assert details["planned"] == 10_000_000
    assert details["over"] == 1_000_000
    # Rien n'a été écrit malgré l'échec du contrôle.
    assert BudgetLine.objects.filter(project=project).count() == 1


@pytest.mark.django_db
def test_update_checks_the_delta_and_respects_the_project_budget(
    auth_client, finance_context, project_context, budget_line
):
    project = finance_context["project"]
    client = auth_client(project_context["owner"])

    too_high = client.patch(
        f"/api/budget-lines/{budget_line.pk}/", {"planned_amount": 12_000_000}, format="json"
    )
    assert too_high.status_code == 422
    assert too_high.data["error"]["code"] == "budget_lines_exceed_budget"
    assert too_high.data["error"]["details"]["over"] == 2_000_000

    budget_line.refresh_from_db()
    assert budget_line.planned_amount == 4_000_000  # inchangé après refus

    accepted = client.patch(
        f"/api/budget-lines/{budget_line.pk}/", {"planned_amount": 9_000_000}, format="json"
    )
    assert accepted.status_code == 200, accepted.data
    assert accepted.data["planned_amount"] == 9_000_000
    assert budget_summary(project)["unallocated"] == 1_000_000


@pytest.mark.django_db
def test_update_logs_the_previous_and_the_new_amount(
    auth_client, finance_context, project_context, budget_line
):
    response = auth_client(project_context["owner"]).patch(
        f"/api/budget-lines/{budget_line.pk}/",
        {"planned_amount": 5_000_000, "notes": "Révision après appel d'offres"},
        format="json",
    )
    assert response.status_code == 200, response.data

    event = ActivityLog.objects.filter(
        action=ActivityLog.Action.BUDGET_LINE_UPDATED, entity_id=str(budget_line.pk)
    ).first()
    assert event is not None
    assert event.actor_id == project_context["owner"].pk
    assert event.metadata["changed"]["planned_amount"] == {"old": 4_000_000, "new": 5_000_000}


@pytest.mark.django_db
def test_a_line_carrying_expenses_cannot_be_deleted(
    auth_client, finance_context, project_context, expense
):
    """Supprimer un poste utilisé rendrait l'historique illisible : refus explicite."""
    line_id = expense.budget_line_id
    response = auth_client(project_context["owner"]).delete(f"/api/budget-lines/{line_id}/")
    assert response.status_code == 409
    assert response.data["error"]["code"] == "budget_line_in_use"
    assert BudgetLine.objects.filter(pk=line_id).exists()


@pytest.mark.django_db
def test_deleting_a_free_line_is_soft_and_journalised(
    auth_client, finance_context, project_context
):
    project = finance_context["project"]
    client = auth_client(project_context["owner"])
    created = client.post(
        _url(project),
        {"label": "Poste sans dépense", "category": "OTHER", "planned_amount": 1_000_000},
        format="json",
    )
    line_id = created.data["id"]

    deleted = client.delete(f"/api/budget-lines/{line_id}/")
    assert deleted.status_code == 204
    # Suppression **logique** : la ligne existe encore, simplement invisible.
    assert BudgetLine.objects.filter(pk=line_id).count() == 0
    assert BudgetLine.all_objects.filter(pk=line_id).count() == 1
    assert ActivityLog.objects.filter(
        action=ActivityLog.Action.BUDGET_LINE_DELETED, entity_id=str(line_id)
    ).exists()
    listing = client.get(_url(project))
    assert [row["id"] for row in listing.data["results"]] == []


@pytest.mark.django_db
def test_line_committed_amount_comes_from_the_ledger(
    auth_client, finance_context, project_context, expense, budget_line
):
    """Le consommé d'un poste suit le grand livre : brouillon → 0, approuvé → montant."""
    project = finance_context["project"]
    owner = project_context["owner"]
    client = auth_client(owner)

    assert client.get(_url(project)).data["results"][0]["committed_amount"] == 0

    assert (
        auth_client(project_context["finance"])
        .post(f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json")
        .status_code
        == 200
    )
    approved = auth_client(owner).post(
        f"/api/expenses/{expense.pk}/transition/", {"action": "APPROVE"}, format="json"
    )
    assert approved.status_code == 200, approved.data

    row = client.get(_url(project)).data["results"][0]
    assert row["committed_amount"] == 1_200_000
    assert row["expense_count"] == 1

    register_payment(
        expense=Expense.objects.get(pk=expense.pk),
        actor=owner,
        data={"amount": 200_000, "paid_on": timezone.localdate()},
    )
    # Un paiement ne consomme pas davantage : il règle un engagement déjà pris.
    assert client.get(_url(project)).data["results"][0]["committed_amount"] == 1_200_000


@pytest.mark.django_db
def test_budget_list_does_not_scale_the_number_of_queries(
    auth_client, finance_context, project_context, budget_line, django_assert_num_queries
):
    """Ajouter des postes, des dépenses et des écritures ne doit pas multiplier les requêtes."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    project = finance_context["project"]
    client = auth_client(project_context["owner"])
    assert (
        client.post(
            _url(project),
            {"label": "Poste initial", "category": "OTHER", "planned_amount": 100_000},
            format="json",
        ).status_code
        == 201
    )

    def measure():
        with CaptureQueriesContext(connection) as captured:
            response = client.get(_url(project))
        assert response.status_code == 200
        return response, len(captured.captured_queries)

    first_response, baseline = measure()

    # Beaucoup plus de données : 4 postes, une dépense approuvée et deux écritures.
    for index in range(4):
        assert (
            client.post(
                _url(project),
                {
                    "label": f"Poste {index}",
                    "category": "OTHER",
                    "planned_amount": 200_000,
                    "order": index,
                },
                format="json",
            ).status_code
            == 201
        )
    other = BudgetLine.objects.create(
        project=project,
        label="Poste approuvé",
        category="OTHER",
        planned_amount=200_000,
        created_by=project_context["owner"],
    )
    small = Expense.objects.create(
        project=project,
        budget_line=other,
        title="Petite dépense",
        amount=100_000,
        incurred_on=timezone.localdate(),
        status=ExpenseStatus.APPROVED,
        created_by=project_context["finance"],
        approved_by=project_context["owner"],
        approved_at=timezone.now(),
    )
    FinancialTransaction.objects.create(
        project=project,
        type="EXPENSE",
        direction="DEBIT",
        amount=small.amount,
        expense=small,
        budget_line=other,
        balance_after=project.budget_total - small.amount,
        created_by=project_context["owner"],
    )
    FinancialTransaction.objects.create(
        project=project,
        type="PAYMENT",
        direction="DEBIT",
        amount=50_000,
        balance_after=project.budget_total - small.amount,
        created_by=project_context["owner"],
    )

    with django_assert_num_queries(baseline):
        second_response = client.get(_url(project))

    assert second_response.data["count"] == 7
    assert second_response.data["summary"]["committed"] == 100_000
    assert second_response.data["summary"]["paid"] == 50_000
    assert second_response.data["summary"]["outstanding"] == 50_000
    rows = {row["label"]: row for row in second_response.data["results"]}
    assert rows["Poste approuvé"]["committed_amount"] == 100_000
    assert rows["Poste approuvé"]["expense_count"] == 1
    assert all("permissions" in row for row in second_response.data["results"])
    # Le résumé de la première mesure reste exploitable (et identique en volume de requêtes).
    assert first_response.data["summary"]["planned"] == 10_000_000
    assert baseline <= 30


@pytest.mark.django_db
def test_finance_summary_exposes_lines_alerts_and_permissions(
    auth_client, finance_context, project_context, budget_line
):
    from apps.finance.tests.conftest import FINANCE_URL

    project = finance_context["project"]
    response = auth_client(project_context["owner"]).get(FINANCE_URL.format(project=project.pk))
    assert response.status_code == 200
    body = response.data
    assert body["budget"]["planned"] == 10_000_000
    assert body["budget"]["allocated"] == 4_000_000
    assert body["budget"]["threshold"] == "OK"
    assert body["permissions"] == {
        "view_finance": True,
        "manage_finance": True,
        "settle_finance": True,
        "can_approve": True,
        "can_pay": True,
        "can_edit": True,
        "can_cancel": True,
    }
    assert [row["budget_line"] for row in body["lines"]] == [budget_line.pk]
    assert body["alerts"] == []
    assert body["generated_at"]


@pytest.mark.django_db
def test_expense_creation_ignores_client_supplied_identifiers(
    auth_client, finance_context, project_context, expense
):
    """Un client ne choisit ni le projet, ni le statut, ni les montants calculés."""
    project = finance_context["project"]
    other = finance_context["organization"]

    response = auth_client(project_context["owner"]).post(
        EXPENSE_URL.format(project=project.pk),
        {
            "title": "Fournitures de bureau",
            "amount": 10_000,
            "incurred_on": "2026-02-11",
            "project": other.pk,
            "status": "PAID",
            "paid_amount": 10_000,
            "outstanding_amount": 0,
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["project"] == project.pk
    assert response.data["amount"] == 10_000
    assert response.data["status"] == ExpenseStatus.DRAFT
    assert response.data["paid_amount"] == 0
    assert response.data["outstanding_amount"] == 10_000
    assert response.data["is_editable"] is True
