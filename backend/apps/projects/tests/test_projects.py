"""Projets : création, validation, périmètre, permissions et performance (MVP-005)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.core.models import ActivityLog
from apps.organizations.models import OrganizationMember
from apps.projects.access import PROJECT_PERMISSION_KEYS
from apps.projects.models import Project, ProjectMember
from apps.users.roles import Role

VALID_PAYLOAD = {
    "name": "Réhabilitation du marché de Bonabéri",
    "code": "RMB-01",
    "city": "Douala",
    "region": "Littoral",
    "currency": "XAF",
    "budget_total": 47_500_000,
    "status": "ACTIVE",
    "planned_start_date": "2026-02-01",
    "planned_end_date": "2026-11-30",
}


@pytest.mark.django_db
def test_organization_owner_creates_a_project(auth_client, organization):
    response = auth_client(organization.owner).post(
        "/api/projects/", {**VALID_PAYLOAD, "organization": organization.id}, format="json"
    )

    assert response.status_code == 201, response.data
    project = Project.objects.get(pk=response.data["id"])
    assert project.organization == organization
    assert project.created_by == organization.owner
    assert project.budget_total == Decimal("47500000")
    assert project.currency == "XAF"
    assert project.progress == Decimal("0.00")  # calculé côté serveur, jamais fourni
    # Le créateur devient maître d'ouvrage du projet.
    membership = ProjectMember.objects.get(project=project, user=organization.owner)
    assert membership.role == Role.PROJECT_OWNER
    assert membership.can_validate_evidence is True
    assert membership.can_manage_finance is True
    assert ActivityLog.objects.filter(action=ActivityLog.Action.PROJECT_CREATED).exists()


@pytest.mark.django_db
def test_project_must_be_attached_to_an_organization(auth_client, organization):
    payload = {**VALID_PAYLOAD, "name": "Projet sans organisation"}
    response = auth_client(organization.owner).post("/api/projects/", payload, format="json")

    assert response.status_code == 400
    assert "organization" in response.data["error"]["details"]


@pytest.mark.django_db
def test_project_cannot_be_created_in_a_foreign_organization(auth_client, organization, make_user):
    outsider = make_user(Role.PROJECT_OWNER, phone_number="+237699111222")
    response = auth_client(outsider).post(
        "/api/projects/", {**VALID_PAYLOAD, "organization": organization.id}, format="json"
    )

    assert response.status_code == 400
    assert "organization" in response.data["error"]["details"]


@pytest.mark.django_db
def test_field_agent_cannot_create_a_project(auth_client, organization, project_context):
    response = auth_client(project_context["agent"]).post(
        "/api/projects/", {**VALID_PAYLOAD, "organization": organization.id}, format="json"
    )
    assert response.status_code == 403
    assert response.data["error"]["code"] == "permission_denied"


@pytest.mark.django_db
def test_project_code_is_unique_per_organization(auth_client, organization, project):
    response = auth_client(organization.owner).post(
        "/api/projects/",
        {**VALID_PAYLOAD, "organization": organization.id, "code": project.code or "RBS-T1"},
        format="json",
    )
    assert response.status_code == 409
    assert response.data["error"]["code"] == "project_code_taken"

    # Un même code reste autorisé dans une autre organisation.
    other = auth_client(organization.owner).post(
        "/api/organizations/", {"name": "Autre promoteur", "type": "PROMOTER"}, format="json"
    )
    same_code = auth_client(organization.owner).post(
        "/api/projects/",
        {**VALID_PAYLOAD, "organization": other.data["id"], "code": project.code or "RBS-T1"},
        format="json",
    )
    assert same_code.status_code == 201


@pytest.mark.django_db
def test_amounts_are_integer_fcfa_only(auth_client, organization):
    client = auth_client(organization.owner)

    cents = client.post(
        "/api/projects/",
        {**VALID_PAYLOAD, "organization": organization.id, "budget_total": "1500000.75"},
        format="json",
    )
    assert cents.status_code == 400
    assert cents.data["error"]["code"] == "amount_has_cents"

    negative = client.post(
        "/api/projects/",
        {**VALID_PAYLOAD, "organization": organization.id, "budget_total": -1},
        format="json",
    )
    assert negative.status_code == 400
    assert negative.data["error"]["code"] == "amount_invalid"

    unsupported = client.post(
        "/api/projects/",
        {**VALID_PAYLOAD, "organization": organization.id, "currency": "EUR"},
        format="json",
    )
    assert unsupported.status_code == 400
    assert unsupported.data["error"]["code"] == "currency_not_supported"


@pytest.mark.django_db
def test_inconsistent_dates_are_rejected(auth_client, organization):
    client = auth_client(organization.owner)

    planned = client.post(
        "/api/projects/",
        {
            **VALID_PAYLOAD,
            "organization": organization.id,
            "planned_start_date": "2026-10-01",
            "planned_end_date": "2026-03-01",
        },
        format="json",
    )
    assert planned.status_code == 400
    assert planned.data["error"]["code"] == "dates_inconsistent"
    assert "planned_end_date" in planned.data["error"]["details"]

    actual = client.post(
        "/api/projects/",
        {
            **VALID_PAYLOAD,
            "organization": organization.id,
            "actual_start_date": "2026-05-01",
            "actual_end_date": "2026-04-01",
        },
        format="json",
    )
    assert actual.status_code == 400
    assert "actual_end_date" in actual.data["error"]["details"]


@pytest.mark.django_db
def test_unknown_status_is_rejected(auth_client, organization):
    response = auth_client(organization.owner).post(
        "/api/projects/",
        {**VALID_PAYLOAD, "organization": organization.id, "status": "EN_PANNE"},
        format="json",
    )
    assert response.status_code == 400
    assert "status" in response.data["error"]["details"]


@pytest.mark.django_db
def test_out_of_scope_project_is_invisible(auth_client, project, project_context):
    """Un non-membre ne doit ni voir ni deviner l'existence du projet (404)."""
    assert (
        auth_client(project_context["stranger"]).get(f"/api/projects/{project.id}/").status_code
        == 404
    )
    listed = auth_client(project_context["stranger"]).get("/api/projects/")
    assert listed.status_code == 200
    assert listed.data["count"] == 0


@pytest.mark.django_db
def test_list_exposes_only_the_projects_of_the_user(auth_client, project_context, make_user):
    other_owner = make_user(Role.ORG_OWNER, phone_number="+237699222333")
    other_org = OrganizationMember.objects.none()  # noqa: F841 - lisibilité de l'intention
    from apps.organizations.models import Organization

    second_org = Organization.objects.create(name="Second promoteur", owner=other_owner)
    Project.objects.create(
        organization=second_org, name="Projet d'un autre", created_by=other_owner
    )

    response = auth_client(project_context["engineer"]).get("/api/projects/")
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["name"].startswith("Résidence")


@pytest.mark.django_db
def test_list_filters_by_status_and_search(auth_client, project, project_context, organization):
    """Le propriétaire de l'organisation pilote tous ses projets (filtres et recherche)."""
    Project.objects.create(
        organization=organization,
        name="Entrepôt frigorifique",
        code="EF-9",
        city="Kribi",
        status="DRAFT",
        created_by=project_context["owner"],
    )
    client = auth_client(organization.owner)

    by_status = client.get("/api/projects/?status=DRAFT")
    assert [item["name"] for item in by_status.data["results"]] == ["Entrepôt frigorifique"]

    by_search = client.get("/api/projects/?search=kribi")
    assert [item["name"] for item in by_search.data["results"]] == ["Entrepôt frigorifique"]

    invalid = client.get("/api/projects/?status=NOPE")
    assert invalid.status_code == 400
    assert invalid.data["error"]["code"] == "invalid_status"

    bad_order = client.get("/api/projects/?ordering=budget_total;DROP TABLE")
    assert bad_order.status_code == 400
    assert bad_order.data["error"]["code"] == "invalid_ordering"


@pytest.mark.django_db
def test_list_is_paginated(auth_client, project, project_context, organization):
    for index in range(6):
        Project.objects.create(
            organization=organization,
            name=f"Projet {index:02d}",
            code=f"P{index:02d}",
            created_by=project_context["owner"],
        )

    first = auth_client(organization.owner).get("/api/projects/?page_size=5")
    assert first.status_code == 200
    assert len(first.data["results"]) == 5
    assert first.data["next"] is not None

    second = auth_client(organization.owner).get("/api/projects/?page=2&page_size=5")
    assert len(second.data["results"]) == 2


@pytest.mark.django_db
def test_project_detail_exposes_backend_permissions(auth_client, project, project_context):
    investor = auth_client(project_context["investor"]).get(f"/api/projects/{project.id}/")

    assert investor.status_code == 200
    assert investor.data["permissions"] == {
        "edit_project": False,
        "archive_project": False,
        "manage_members": False,
        "manage_schedule": False,
        "update_task": False,
        "capture_evidence": False,
        "validate_evidence": False,
        "view_finance": True,
        "manage_finance": False,
        "view_activity": True,
    }
    assert list(investor.data["permissions"]) == list(PROJECT_PERMISSION_KEYS)

    engineer = auth_client(project_context["engineer"]).get(f"/api/projects/{project.id}/")
    assert engineer.data["permissions"]["capture_evidence"] is True
    assert engineer.data["permissions"]["validate_evidence"] is False
    assert engineer.data["permissions"]["manage_finance"] is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    "actor,expected_status",
    [
        ("owner", 200),
        ("engineer", 403),  # visible mais modification interdite → 403
        ("agent", 403),
        ("investor", 403),
        ("stranger", 404),  # hors périmètre → 404
    ],
)
def test_update_follows_permissions(auth_client, project, project_context, actor, expected_status):
    response = auth_client(project_context[actor]).patch(
        f"/api/projects/{project.id}/", {"city": "Kribi"}, format="json"
    )
    assert response.status_code == expected_status


@pytest.mark.django_db
def test_update_is_logged_with_previous_values(auth_client, project, project_context):
    response = auth_client(project_context["owner"]).patch(
        f"/api/projects/{project.id}/",
        {"status": "ON_HOLD", "budget_total": 90000000},
        format="json",
    )

    assert response.status_code == 200, response.data
    event = ActivityLog.objects.get(action=ActivityLog.Action.PROJECT_UPDATED)
    assert event.metadata["changed"]["status"] == {"old": "ACTIVE", "new": "ON_HOLD"}
    assert event.metadata["changed"]["budget_total"]["new"] == 90000000


@pytest.mark.django_db
def test_delete_archives_instead_of_erasing(auth_client, project, project_context):
    response = auth_client(project_context["owner"]).delete(f"/api/projects/{project.id}/")

    assert response.status_code == 204
    assert Project.objects.filter(pk=project.pk).count() == 0
    archived = Project.all_objects.get(pk=project.pk)
    assert archived.deleted_at is not None
    assert archived.status == "ARCHIVED"
    assert ActivityLog.objects.filter(action=ActivityLog.Action.PROJECT_ARCHIVED).exists()


@pytest.mark.django_db
def test_progress_is_read_only(auth_client, project, project_context):
    response = auth_client(project_context["owner"]).patch(
        f"/api/projects/{project.id}/", {"progress": "99.00"}, format="json"
    )
    project.refresh_from_db()
    assert response.status_code == 200
    assert project.progress == Decimal("0.00")


@pytest.mark.django_db
def test_project_list_has_no_n_plus_one(auth_client, project, project_context, organization):
    for index in range(8):
        Project.objects.create(
            organization=organization,
            name=f"Projet {index:02d}",
            code=f"NP{index:02d}",
            created_by=project_context["owner"],
        )

    with CaptureQueriesContext(connection) as queries:
        response = auth_client(organization.owner).get("/api/projects/?page_size=50")

    assert response.status_code == 200
    assert len(response.data["results"]) == 9
    # 1 comptage, 1 page de résultats, 1 organisation, 1 créateur, 1 organisations possédées,
    # 1 appartenances + 1 rôles : constant, indépendant du nombre de lignes.
    assert len(queries) <= 10, [query["sql"][:120] for query in queries.captured_queries]
