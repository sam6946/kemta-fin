"""Permissions financières (MVP-010) — la matrice de `docs/rbac-matrix.md` §6, appliquée.

Deux niveaux distincts, testés ici de bout en bout par l'API :

* `VIEW_FINANCE` — lire le budget, les dépenses et le grand livre ;
* `MANAGE_FINANCE` — créer/corriger une dépense avant approbation ;
* **engagement** (`can_settle_finance`) — approuver, payer, ajuster, tenir le budget :
  réservé aux rôles de pilotage, un `CONTRACTOR` même doté du drapeau n'y a pas droit.

Un projet hors périmètre répond **404** (jamais 403) : son existence n'est pas révélée.
"""

from __future__ import annotations

import pytest

from apps.finance.tests.conftest import BUDGET_URL, EXPENSE_URL, FINANCE_URL, TRANSACTIONS_URL

READ_URLS = (BUDGET_URL, EXPENSE_URL, FINANCE_URL, TRANSACTIONS_URL)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("actor", "expected"),
    [
        ("owner", 200),
        ("finance", 200),
        ("engineer", 200),
        ("contractor_finance", 200),
        ("investor", 200),
        ("validator", 403),
        ("stranger", 404),
    ],
)
def test_read_access_follows_view_finance(auth_client, finance_context, actor, expected):
    project = finance_context["project"].pk
    for template in READ_URLS:
        response = auth_client(finance_context[actor]).get(template.format(project=project))
        assert response.status_code == expected, (actor, template, response.status_code)


@pytest.mark.django_db
def test_anonymous_is_rejected():
    from rest_framework.test import APIClient

    client = APIClient()
    response = client.get(FINANCE_URL.format(project=1))
    assert response.status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("actor", ["engineer", "validator", "investor", "stranger"])
def test_creating_expense_requires_manage_finance(auth_client, finance_context, actor):
    project = finance_context["project"].pk
    response = auth_client(finance_context[actor]).post(
        EXPENSE_URL.format(project=project),
        {"title": "Carburant", "amount": 50_000, "incurred_on": "2026-02-01"},
        format="json",
    )
    # Hors périmètre : 404 ; dans le périmètre mais sans droit d'écriture : 403.
    assert response.status_code == (404 if actor == "stranger" else 403), response.data


@pytest.mark.django_db
def test_contractor_with_flag_can_create_but_never_settle(
    auth_client, finance_context, project_context, expense
):
    """Décision produit : le drapeau `can_manage_finance` n'accorde pas le droit d'engager."""
    contractor = finance_context["contractor_finance"]
    project = finance_context["project"].pk

    created = auth_client(contractor).post(
        EXPENSE_URL.format(project=project),
        {"title": "Location d'échafaudages", "amount": 300_000, "incurred_on": "2026-02-11"},
        format="json",
    )
    assert created.status_code == 201, created.data
    assert created.data["status"] == "DRAFT"

    # Il peut soumettre (droit de gestion)…
    submitted = auth_client(contractor).post(
        f"/api/expenses/{created.data['id']}/transition/", {"action": "SUBMIT"}, format="json"
    )
    assert submitted.status_code == 200, submitted.data
    assert submitted.data["status"] == "SUBMITTED"

    # … mais ni approuver, ni payer, ni tenir le budget, ni ajuster.
    assert (
        auth_client(contractor)
        .post(
            f"/api/expenses/{created.data['id']}/transition/",
            {"action": "APPROVE"},
            format="json",
        )
        .status_code
        == 403
    )
    assert (
        auth_client(contractor)
        .post(f"/api/expenses/{created.data['id']}/payments/", {"amount": 1000}, format="json")
        .status_code
        == 403
    )
    assert (
        auth_client(contractor)
        .post(
            BUDGET_URL.format(project=project),
            {"label": "Frais divers", "planned_amount": 10_000},
            format="json",
        )
        .status_code
        == 403
    )
    assert (
        auth_client(contractor)
        .post(
            f"/api/projects/{project}/adjustments/",
            {"amount": 1000, "direction": "CREDIT", "reason": "Correction"},
            format="json",
        )
        .status_code
        == 403
    )


@pytest.mark.django_db
def test_engineer_cannot_approve_nor_pay(auth_client, finance_context, project_context, expense):
    engineer = finance_context["engineer"]
    transition = auth_client(project_context["finance"]).post(
        f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json"
    )
    assert transition.status_code == 200

    assert (
        auth_client(engineer)
        .post(f"/api/expenses/{expense.pk}/transition/", {"action": "APPROVE"}, format="json")
        .status_code
        == 403
    )
    assert (
        auth_client(engineer)
        .post(f"/api/expenses/{expense.pk}/payments/", {"amount": 1000}, format="json")
        .status_code
        == 403
    )


@pytest.mark.django_db
def test_platform_admin_can_do_everything_on_any_project(
    auth_client, finance_context, make_user, expense
):
    from apps.users.roles import Role

    admin = make_user(Role.PLATFORM_ADMIN)
    project = finance_context["project"].pk

    assert auth_client(admin).get(FINANCE_URL.format(project=project)).status_code == 200
    assert (
        auth_client(admin)
        .post(
            BUDGET_URL.format(project=project),
            {"label": "Sécurité du site", "planned_amount": 500_000},
            format="json",
        )
        .status_code
        == 201
    )
    # L'administrateur plateforme peut exceptionnellement approuver sa propre dépense
    # (exploitation), contrairement aux rôles métier.
    submitted = auth_client(admin).post(
        f"/api/expenses/{expense.pk}/transition/", {"action": "SUBMIT"}, format="json"
    )
    assert submitted.status_code == 200
    approved = auth_client(admin).post(
        f"/api/expenses/{expense.pk}/transition/", {"action": "APPROVE"}, format="json"
    )
    assert approved.status_code == 200, approved.data


@pytest.fixture()
def foreign_expense(finance_context, make_user):
    """Dépense appartenant à un **autre** chantier, piloté par un autre maître d'ouvrage."""
    from apps.finance.models import Expense
    from apps.projects.models import Project, ProjectMember
    from apps.users.roles import Role

    other_owner = make_user(Role.PROJECT_OWNER, phone_number="+237699111222")
    other = Project.objects.create(
        organization=finance_context["organization"],
        name="Chantier voisin — Logbaba",
        code="CV-LOG",
        city="Douala",
        budget_total=5_000_000,
        status="ACTIVE",
        created_by=other_owner,
    )
    ProjectMember.objects.create(
        project=other, user=other_owner, role=Role.PROJECT_OWNER, can_manage_finance=True
    )
    return Expense.objects.create(
        project=other,
        title="Terrassement",
        amount=900_000,
        currency="XAF",
        incurred_on="2026-02-05",
        created_by=other_owner,
    )


@pytest.mark.django_db
def test_expense_of_another_project_is_invisible(auth_client, finance_context, foreign_expense):
    """Une dépense d'un projet inaccessible répond 404 sur toutes ses routes unitaires."""
    stranger = finance_context["stranger"]
    for url in (
        f"/api/expenses/{foreign_expense.pk}/",
        f"/api/expenses/{foreign_expense.pk}/payments/",
        f"/api/expenses/{foreign_expense.pk}/receipt/",
    ):
        assert auth_client(stranger).get(url).status_code == 404, url
    # Elle reste évidemment visible pour son propre maître d'ouvrage.
    assert (
        auth_client(foreign_expense.created_by)
        .get(f"/api/expenses/{foreign_expense.pk}/")
        .status_code
        == 200
    )
