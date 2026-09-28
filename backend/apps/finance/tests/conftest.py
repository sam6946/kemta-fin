"""Fixtures financières : projet doté d'un budget, postes, dépenses et paiements."""

from __future__ import annotations

import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.projects.models import ProjectMember
from apps.users.roles import Role

BUDGET_URL = "/api/projects/{project}/budget-lines/"
EXPENSE_URL = "/api/projects/{project}/expenses/"
FINANCE_URL = "/api/projects/{project}/finance/"
TRANSACTIONS_URL = "/api/projects/{project}/transactions/"


@pytest.fixture()
def funded_project(project):
    """Projet doté d'un budget de 10 000 000 FCFA (montants ronds, faciles à vérifier)."""
    project.budget_total = 10_000_000
    project.save(update_fields=["budget_total"])
    return project


@pytest.fixture()
def finance_context(funded_project, project_context, make_user):
    """Acteurs financiers supplémentaires : contractant gestionnaire, ingénieur, investisseur.

    Le majordome de projet (`owner`) et le financier (`finance`) du `project_context` sont
    réutilisés : ce sont eux qui portent le droit d'engager de l'argent.
    """

    def _member(role: str, **flags):
        user = make_user(role, phone_number=f"+237697{uuid.uuid4().int % 10_000_000:07d}")
        ProjectMember.objects.create(project=funded_project, user=user, role=role, **flags)
        return user

    return {
        **project_context,
        "project": funded_project,
        "contractor_finance": _member(Role.CONTRACTOR, can_manage_finance=True),
        "engineer": project_context["engineer"],
        "investor": project_context["investor"],
    }


@pytest.fixture()
def budget_line(auth_client, finance_context, project_context):
    """Poste budgétaire de 4 000 000 FCFA (le majordome d'ouvrage le crée par l'API)."""
    response = auth_client(project_context["owner"]).post(
        BUDGET_URL.format(project=finance_context["project"].pk),
        {
            "label": "Matériaux — ciment et fer",
            "category": "MATERIALS",
            "planned_amount": 4_000_000,
            "order": 1,
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    from apps.finance.models import BudgetLine

    return BudgetLine.objects.get(pk=response.data["id"])


@pytest.fixture()
def expense(auth_client, finance_context, project_context, budget_line):
    """Dépense de 1 200 000 FCFA en brouillon, créée par le financier."""
    response = auth_client(project_context["finance"]).post(
        EXPENSE_URL.format(project=finance_context["project"].pk),
        {
            "title": "Achat 400 sacs de ciment",
            "description": "Livraison chantier Bonamoussadi",
            "amount": 1_200_000,
            "incurred_on": "2026-02-10",
            "supplier": "CIMENCAM Douala",
            "invoice_number": "FAC-2026-0142",
            "invoice_date": "2026-02-09",
            "budget_line": budget_line.pk,
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    from apps.finance.models import Expense

    return Expense.objects.get(pk=response.data["id"])


@pytest.fixture()
def transition(auth_client):
    """`transition(user, expense, "APPROVE", comment=…, override_reason=…)`."""

    def _transition(user, target, action: str, **extra):
        payload = {"action": action, **extra}
        return auth_client(user).post(
            f"/api/expenses/{target.pk}/transition/", payload, format="json"
        )

    return _transition


@pytest.fixture()
def pay(auth_client):
    def _pay(user, target, amount: int, **extra):
        return auth_client(user).post(
            f"/api/expenses/{target.pk}/payments/",
            {"amount": amount, "paid_on": "2026-02-15", **extra},
            format="json",
        )

    return _pay


@pytest.fixture()
def receipt():
    """Fabrique de justificatif : petite facture PDF valide (en-tête `%PDF-` reconnu).

    Les tests passent toujours un `SimpleUploadedFile` : la validation serveur se fait sur la
    **signature binaire**, jamais sur le nom ou le type déclaré.
    """

    def _receipt(
        payload: bytes | None = None,
        name: str = "facture.pdf",
        content_type: str = "application/pdf",
    ) -> SimpleUploadedFile:
        return SimpleUploadedFile(
            name,
            b"%PDF-1.7\nfacture fournisseur\n%%EOF\n" if payload is None else payload,
            content_type=content_type,
        )

    return _receipt
