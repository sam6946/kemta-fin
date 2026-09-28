"""Dépenses (MVP-010) — machine à états, engagement budgétaire et journalisation.

Règles vérifiées ici (voir `docs/flows/finance.md`) :

* une dépense naît en **brouillon** et n'engage de budget qu'à l'**approbation** ;
* elle est toujours rattachée au projet (et à un poste du **même** projet) ;
* l'auteur d'une dépense ne l'approuve pas lui-même (sauf administration plateforme) ;
* le rejet exige un motif ; une dépense payée ou annulée est terminale ;
* annuler un engagement écrit une **contre-écriture** : rien n'est jamais supprimé ;
* chaque transition est journalisée (auteur, date, statut avant/après, motif).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.models import ActivityLog
from apps.finance.models import (
    EXPENSE_TRANSITIONS,
    Expense,
    ExpenseStatus,
    FinancialTransaction,
    TransactionDirection,
    TransactionType,
)
from apps.finance.tests.conftest import EXPENSE_URL


def _create(client, project, **overrides):
    payload = {
        "title": "Location bétonnière",
        "amount": 450_000,
        "incurred_on": "2026-02-12",
        **overrides,
    }
    return client.post(EXPENSE_URL.format(project=project.pk), payload, format="json")


@pytest.mark.django_db
def test_created_expense_is_a_draft_attached_to_its_project(
    auth_client, finance_context, project_context, budget_line
):
    project = finance_context["project"]
    response = _create(
        auth_client(project_context["finance"]),
        project,
        budget_line=budget_line.pk,
        supplier="SOCAMAT",
        invoice_number="FAC-777",
    )
    assert response.status_code == 201, response.data
    body = response.data
    assert body["status"] == "DRAFT"
    assert body["status_label"] == "Brouillon"
    assert body["currency"] == "XAF"
    assert body["amount"] == 450_000
    assert body["budget_line"] == budget_line.pk
    assert body["budget_line_label"] == budget_line.label
    assert body["paid_amount"] == 0
    assert body["outstanding_amount"] == 450_000
    assert body["is_editable"] is True
    assert body["created_by"]["id"] == project_context["finance"].pk
    assert body["permissions"]["can_edit"] is True
    assert body["permissions"]["can_approve"] is False  # brouillon : rien à approuver
    assert body["receipt_url"] is None

    # Un brouillon n'engage rien.
    assert FinancialTransaction.objects.filter(project=project).count() == 0


@pytest.mark.django_db
def test_amount_rules(auth_client, finance_context, project_context):
    client = auth_client(project_context["finance"])
    project = finance_context["project"]

    cents = _create(client, project, amount="450000.25")
    assert cents.status_code == 400
    assert cents.data["error"]["code"] == "amount_has_cents"

    zero = _create(client, project, amount=0)
    assert zero.status_code == 400

    negative = _create(client, project, amount=-1_000)
    assert negative.status_code == 400

    missing_title = _create(client, project, title="   ")
    assert missing_title.status_code == 400
    assert "title" in missing_title.data["error"]["details"]

    missing_date = _create(client, project, incurred_on=None)
    assert missing_date.status_code == 400

    assert Expense.objects.count() == 0


@pytest.mark.django_db
def test_budget_line_must_belong_to_the_same_project(
    auth_client, finance_context, project_context, expense
):
    """Une dépense ne peut pas être rattachée au poste d'un autre chantier."""
    from apps.finance.models import BudgetLine
    from apps.projects.models import Project

    other = Project.objects.create(
        organization=finance_context["organization"],
        name="Chantier voisin",
        code="CV-2",
        budget_total=1_000_000,
        status="ACTIVE",
        created_by=project_context["owner"],
    )
    foreign_line = BudgetLine.objects.create(
        project=other,
        label="Poste voisin",
        category="OTHER",
        planned_amount=100_000,
        created_by=project_context["owner"],
    )
    response = _create(
        auth_client(project_context["finance"]),
        finance_context["project"],
        budget_line=foreign_line.pk,
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "budget_line_other_project"

    unknown = _create(
        auth_client(project_context["finance"]), finance_context["project"], budget_line=999_999
    )
    assert unknown.status_code == 404
    assert unknown.data["error"]["code"] == "budget_line_not_found"


@pytest.mark.django_db
def test_invoice_number_is_unique_per_project(
    auth_client, finance_context, project_context, expense
):
    client = auth_client(project_context["finance"])
    duplicate = _create(client, finance_context["project"], invoice_number=expense.invoice_number)
    assert duplicate.status_code == 409
    assert duplicate.data["error"]["code"] == "invoice_already_used"

    without_number = _create(client, finance_context["project"], invoice_number="")
    assert without_number.status_code == 201


@pytest.mark.django_db
def test_update_draft_logs_old_and_new_values(
    auth_client, finance_context, project_context, expense
):
    response = auth_client(project_context["finance"]).patch(
        f"/api/expenses/{expense.pk}/",
        {"amount": 1_500_000, "supplier": "CIMENCAM Bafoussam"},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["amount"] == 1_500_000
    log = ActivityLog.objects.filter(
        action=ActivityLog.Action.EXPENSE_UPDATED, entity_id=str(expense.pk)
    ).latest("created_at")
    assert log.actor_id == project_context["finance"].pk
    assert log.metadata["changed"]["amount"] == {"old": 1_200_000, "new": 1_500_000}
    assert log.metadata["changed"]["supplier"]["new"] == "CIMENCAM Bafoussam"
    # Aucun autre champ n'est pollué dans le journal : seules les valeurs changées apparaissent.
    assert set(log.metadata["changed"]) == {"amount", "supplier"}


@pytest.mark.django_db
def test_approved_expense_is_no_longer_editable(
    auth_client, finance_context, project_context, expense, transition
):
    assert (
        auth_client(project_context["finance"])
        .post(f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json")
        .status_code
        == 200
    )
    assert transition(project_context["owner"], expense, "APPROVE").status_code == 200

    locked = auth_client(project_context["finance"]).patch(
        f"/api/expenses/{expense.pk}/", {"amount": 1}, format="json"
    )
    assert locked.status_code == 409
    assert locked.data["error"]["code"] == "expense_locked"
    expense.refresh_from_db()
    assert expense.amount == 1_200_000


@pytest.mark.django_db
def test_approval_commits_the_budget_atomically(
    auth_client, finance_context, project_context, expense, transition
):
    project = finance_context["project"]
    submitted = auth_client(project_context["finance"]).post(
        f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json"
    )
    assert submitted.status_code == 200
    assert submitted.data["status"] == "SUBMITTED"

    approved = transition(project_context["owner"], expense, "APPROVE")
    assert approved.status_code == 200, approved.data
    assert approved.data["status"] == "APPROVED"
    assert approved.data["approved_by"]["id"] == project_context["owner"].pk
    assert approved.data["approved_at"] is not None
    assert approved.data["is_editable"] is False
    assert approved.data["permissions"]["can_pay"] is True

    entry = FinancialTransaction.objects.get(project=project)
    assert entry.type == TransactionType.EXPENSE
    assert entry.direction == TransactionDirection.DEBIT
    assert entry.amount == 1_200_000
    assert entry.balance_after == 8_800_000  # 10 000 000 − 1 200 000, calculé par le serveur
    assert entry.created_by_id == project_context["owner"].pk

    log = ActivityLog.objects.filter(
        action=ActivityLog.Action.EXPENSE_APPROVED, entity_id=str(expense.pk)
    ).latest("created_at")
    assert log.metadata["from_status"] == "SUBMITTED"
    assert log.metadata["to_status"] == "APPROVED"

    summary = auth_client(project_context["owner"]).get(f"/api/projects/{project.pk}/finance/")
    assert summary.data["budget"]["committed"] == 1_200_000
    assert summary.data["budget"]["outstanding"] == 1_200_000
    assert summary.data["budget"]["balance"] == 8_800_000


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("from_status", "action", "allowed"),
    [
        (ExpenseStatus.DRAFT, "APPROVE", ["CANCEL", "SUBMIT"]),
        (ExpenseStatus.SUBMITTED, "SUBMIT", ["APPROVE", "CANCEL", "REJECT"]),
    ],
)
def test_invalid_transitions_are_refused_with_the_allowed_actions(
    auth_client, finance_context, project_context, expense, transition, from_status, action, allowed
):
    if from_status == ExpenseStatus.SUBMITTED:
        assert (
            auth_client(project_context["finance"])
            .post(f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json")
            .status_code
            == 200
        )
    response = transition(project_context["owner"], expense, action)
    assert response.status_code == 409
    assert response.data["error"]["code"] == "invalid_transition"
    assert response.data["error"]["details"]["allowed_actions"] == allowed
    expense.refresh_from_db()
    assert expense.status == from_status


@pytest.mark.django_db
def test_transition_map_is_closed(expense):
    """Garde-fou de la table de transitions : aucune sortie de PAID ni de CANCELLED."""
    assert EXPENSE_TRANSITIONS[ExpenseStatus.PAID] == {}
    assert EXPENSE_TRANSITIONS[ExpenseStatus.CANCELLED] == {}
    assert set(EXPENSE_TRANSITIONS[ExpenseStatus.DRAFT]) == {"SUBMIT", "CANCEL"}
    assert set(EXPENSE_TRANSITIONS[ExpenseStatus.REJECTED]) == {"SUBMIT", "CANCEL"}


@pytest.mark.django_db
def test_author_cannot_approve_own_expense(
    auth_client, finance_context, project_context, transition
):
    project = finance_context["project"]
    owner = project_context["owner"]
    created = _create(auth_client(owner), project)
    assert created.status_code == 201, created.data
    assert (
        auth_client(owner)
        .post(
            f"/api/expenses/{created.data['id']}/transition/", {"action": "SUBMIT"}, format="json"
        )
        .status_code
        == 200
    )
    refused = transition(owner, Expense.objects.get(pk=created.data["id"]), "APPROVE")
    assert refused.status_code == 403
    assert refused.data["error"]["code"] == "cannot_approve_own_expense"

    # Un autre responsable financier (ici le rôle FINANCE) peut l'approuver.
    assert (
        transition(
            project_context["finance"], Expense.objects.get(pk=created.data["id"]), "APPROVE"
        ).status_code
        == 200
    )


@pytest.mark.django_db
def test_rejection_requires_a_motif_and_can_be_resubmitted(
    auth_client, finance_context, project_context, expense, transition
):
    client = auth_client(project_context["finance"])
    assert (
        client.post(
            f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json"
        ).status_code
        == 200
    )

    missing = transition(project_context["owner"], expense, "REJECT")
    assert missing.status_code == 400
    assert missing.data["error"]["code"] == "comment_required"

    rejected = transition(
        project_context["owner"], expense, "REJECT", comment="Facture fournisseur manquante."
    )
    assert rejected.status_code == 200
    assert rejected.data["status"] == "REJECTED"
    assert FinancialTransaction.objects.count() == 0  # un rejet n'engage rien

    # Une dépense rejetée est corrigeable puis resoumettable.
    fixed = client.patch(
        f"/api/expenses/{expense.pk}/", {"description": "Facture jointe"}, format="json"
    )
    assert fixed.status_code == 200
    again = client.post(
        f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json"
    )
    assert again.status_code == 200
    assert again.data["status"] == "SUBMITTED"


@pytest.mark.django_db
def test_cancelling_a_committed_expense_writes_a_counter_entry(
    auth_client, finance_context, project_context, expense, transition
):
    project = finance_context["project"]
    auth_client(project_context["finance"]).post(
        f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json"
    )
    assert transition(project_context["owner"], expense, "APPROVE").status_code == 200

    cancelled = transition(
        project_context["owner"], expense, "CANCEL", comment="Commande finalement annulée."
    )
    assert cancelled.status_code == 200, cancelled.data
    assert cancelled.data["status"] == "CANCELLED"
    assert cancelled.data["cancelled_at"] is not None

    counter = FinancialTransaction.objects.get(type=TransactionType.CANCELLATION)
    assert counter.direction == TransactionDirection.CREDIT
    assert counter.amount == 1_200_000
    assert counter.balance_after == 10_000_000
    # Les deux écritures restent : le grand livre est append-only.
    assert FinancialTransaction.objects.filter(project=project).count() == 2

    summary = auth_client(project_context["owner"]).get(f"/api/projects/{project.pk}/finance/")
    assert summary.data["budget"]["committed"] == 0
    assert summary.data["budget"]["balance"] == 10_000_000

    # Une dépense annulée est terminale.
    assert transition(project_context["owner"], expense, "SUBMIT").status_code == 409


@pytest.mark.django_db
def test_cancelling_a_draft_writes_no_ledger_entry(
    auth_client, finance_context, project_context, expense, transition
):
    response = transition(project_context["finance"], expense, "CANCEL")
    assert response.status_code == 200
    assert response.data["status"] == "CANCELLED"
    assert FinancialTransaction.objects.count() == 0


@pytest.mark.django_db
def test_cancelling_a_partially_paid_expense_is_refused(
    auth_client, finance_context, project_context, expense, transition, pay
):
    auth_client(project_context["finance"]).post(
        f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json"
    )
    assert transition(project_context["owner"], expense, "APPROVE").status_code == 200
    assert pay(project_context["owner"], expense, 200_000).status_code == 201

    refused = transition(project_context["owner"], expense, "CANCEL")
    assert refused.status_code == 409
    assert refused.data["error"]["code"] == "expense_has_payments"
    assert refused.data["error"]["details"]["paid"] == 200_000
    expense.refresh_from_db()
    assert expense.status == "APPROVED"


@pytest.mark.django_db
def test_expense_list_filters_summary_and_ordering(
    auth_client, finance_context, project_context, expense
):
    client = auth_client(project_context["engineer"])
    url = EXPENSE_URL.format(project=finance_context["project"].pk)

    page = client.get(url)
    assert page.status_code == 200
    assert page.data["count"] == 1
    assert page.data["counts"]["draft"] == 1
    assert page.data["summary"]["planned"] == 10_000_000
    assert page.data["results"][0]["title"] == expense.title

    assert client.get(url, {"status": "draft"}).data["count"] == 1
    assert client.get(url, {"status": "DRAFT,APPROVED"}).data["count"] == 1
    assert client.get(url, {"status": "PAID"}).data["count"] == 0
    assert client.get(url, {"unpaid": "1"}).data["count"] == 0
    assert client.get(url, {"ordering": "amount"}).status_code == 200

    unknown = client.get(url, {"status": "INCONNU"})
    assert unknown.status_code == 400
    assert unknown.data["error"]["code"] == "invalid_status"
    bad_order = client.get(url, {"ordering": "supplier"})
    assert bad_order.status_code == 400
    assert bad_order.data["error"]["code"] == "invalid_ordering"


@pytest.mark.django_db
def test_expense_list_stays_at_constant_query_count(auth_client, finance_context, project_context):
    """Une liste de dépenses ne doit jamais coûter une requête par ligne."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    client = auth_client(project_context["finance"])
    project = finance_context["project"]
    url = EXPENSE_URL.format(project=project.pk)
    assert _create(client, project).status_code == 201

    def count_queries() -> int:
        with CaptureQueriesContext(connection) as context:
            response = client.get(url)
            assert response.status_code == 200
            assert len(response.data["results"]) >= 1
        return len(context)

    before = count_queries()
    for index in range(6):
        created = _create(client, project, title=f"Dépense {index}", amount=10_000 + index)
        assert created.status_code == 201
    after = count_queries()
    assert after == before, (before, after)


@pytest.mark.django_db
def test_expense_detail_is_scoped_to_accessible_projects(
    auth_client, finance_context, project_context, expense
):
    from apps.projects.models import Project

    other = Project.objects.create(
        organization=finance_context["organization"],
        name="Autre chantier",
        code="AUT-1",
        budget_total=1_000_000,
        status="ACTIVE",
        created_by=project_context["owner"],
    )
    other_expense = Expense.objects.create(
        project=other,
        title="Dépense voisine",
        amount=5_000,
        currency="XAF",
        incurred_on="2026-02-01",
        created_by=project_context["owner"],
    )
    # L'ingénieur est membre du premier projet : la liste ne fuit pas vers l'autre chantier.
    page = auth_client(project_context["engineer"]).get(
        EXPENSE_URL.format(project=finance_context["project"].pk)
    )
    assert [row["id"] for row in page.data["results"]] == [expense.pk]
    assert other_expense.pk not in [row["id"] for row in page.data["results"]]


@pytest.mark.django_db
def test_outstanding_amount_reflects_payments(
    auth_client, finance_context, project_context, expense, transition, pay
):
    auth_client(project_context["finance"]).post(
        f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json"
    )
    assert transition(project_context["owner"], expense, "APPROVE").status_code == 200
    assert pay(project_context["owner"], expense, 1_200_000).status_code == 201

    detail = auth_client(project_context["engineer"]).get(f"/api/expenses/{expense.pk}/")
    assert Decimal(str(detail.data["paid_amount"])) == Decimal("1200000")
    assert detail.data["outstanding_amount"] == 0
    assert detail.data["status"] == "PAID"
    assert len(detail.data["payments"]) == 1
