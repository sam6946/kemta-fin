"""Organisations : création, périmètre, membres et permissions (MVP-005)."""

from __future__ import annotations

import pytest

from apps.core.models import ActivityLog
from apps.organizations.models import Organization, OrganizationMember
from apps.users.roles import Role


@pytest.mark.django_db
def test_organization_owner_can_create_an_organization(auth_client, make_user):
    user = make_user(Role.ORG_OWNER, phone_number="+237690111222")
    response = auth_client(user).post(
        "/api/organizations/",
        {"name": "KEMTA Bâtiment Douala", "type": "PME", "city": "Douala"},
        format="json",
    )

    assert response.status_code == 201, response.data
    organization = Organization.objects.get(pk=response.data["id"])
    assert organization.owner == user
    assert organization.slug == "kemta-batiment-douala"
    # Le créateur devient automatiquement membre propriétaire.
    membership = OrganizationMember.objects.get(organization=organization, user=user)
    assert membership.role == Role.ORG_OWNER
    assert ActivityLog.objects.filter(action=ActivityLog.Action.ORG_CREATED).exists()


@pytest.mark.django_db
def test_role_without_create_capability_is_rejected(auth_client, make_user):
    user = make_user(Role.FIELD_AGENT, phone_number="+237690111333")
    response = auth_client(user).post(
        "/api/organizations/", {"name": "Tentative", "type": "PME"}, format="json"
    )
    assert response.status_code == 403
    assert response.data["error"]["code"] == "permission_denied"


@pytest.mark.django_db
def test_slug_is_unique_even_with_same_name(auth_client, make_user):
    first = make_user(Role.ORG_OWNER, phone_number="+237690111444")
    second = make_user(Role.ORG_OWNER, phone_number="+237690111555")

    client = auth_client(first)
    client.post("/api/organizations/", {"name": "KEMTA Promotion", "type": "PME"}, format="json")
    second_client = auth_client(second)
    second_client.post(
        "/api/organizations/", {"name": "KEMTA Promotion", "type": "PME"}, format="json"
    )

    slugs = set(Organization.objects.values_list("slug", flat=True))
    assert len(slugs) == 2


@pytest.mark.django_db
def test_list_is_scoped_to_the_user(auth_client, make_user):
    owner = make_user(Role.ORG_OWNER, phone_number="+237690111666")
    stranger = make_user(Role.ORG_OWNER, phone_number="+237690111777")
    Organization.objects.create(name="Organisation visible", owner=owner)
    Organization.objects.create(name="Organisation invisible", owner=stranger)

    response = auth_client(owner).get("/api/organizations/")
    assert response.status_code == 200
    names = [item["name"] for item in response.data["results"]]
    assert names == ["Organisation visible"]


@pytest.mark.django_db
def test_detail_out_of_scope_is_404_and_readonly_member_is_403(
    auth_client, organization, make_user
):
    outsider = make_user(Role.ORG_OWNER, phone_number="+237690111888")
    assert auth_client(outsider).get(f"/api/organizations/{organization.id}/").status_code == 404

    # Investisseur : hors périmètre → invisible ; membre consultant → lecture seule.
    reader = make_user(Role.INVESTOR, phone_number="+237690111999")
    assert auth_client(reader).get(f"/api/organizations/{organization.id}/").status_code == 404
    OrganizationMember.objects.create(organization=organization, user=reader, role=Role.INVESTOR)
    assert auth_client(reader).get(f"/api/organizations/{organization.id}/").status_code == 200
    forbidden = auth_client(reader).patch(
        f"/api/organizations/{organization.id}/", {"name": "Nouveau nom"}, format="json"
    )
    assert forbidden.status_code == 403


@pytest.mark.django_db
def test_organization_update_is_logged(auth_client, organization):
    response = auth_client(organization.owner).patch(
        f"/api/organizations/{organization.id}/", {"city": "Yaoundé"}, format="json"
    )
    assert response.status_code == 200
    assert response.data["city"] == "Yaoundé"
    assert ActivityLog.objects.filter(action=ActivityLog.Action.ORG_UPDATED).exists()


@pytest.mark.django_db
def test_deleting_an_organization_with_projects_is_blocked(auth_client, organization, project):
    """Une organisation qui porte des projets n'est pas supprimable (409, message clair)."""
    response = auth_client(organization.owner).delete(f"/api/organizations/{organization.id}/")
    assert response.status_code == 409
    assert response.data["error"]["code"] == "organization_has_projects"
    assert Organization.objects.filter(pk=organization.pk).exists()


@pytest.mark.django_db
def test_deleting_an_empty_organization_is_a_soft_delete(auth_client, make_user):
    owner = make_user(Role.ORG_OWNER, phone_number="+237690112000")
    created = auth_client(owner).post(
        "/api/organizations/", {"name": "Organisation éphémère", "type": "PME"}, format="json"
    )
    response = auth_client(owner).delete(f"/api/organizations/{created.data['id']}/")

    assert response.status_code == 204
    assert Organization.objects.filter(pk=created.data["id"]).count() == 0
    assert Organization.all_objects.get(pk=created.data["id"]).deleted_at is not None


@pytest.mark.django_db
def test_adding_an_unknown_phone_returns_user_not_found(auth_client, organization):
    response = auth_client(organization.owner).post(
        f"/api/organizations/{organization.id}/members/",
        {"phone": "+237699000999", "role": Role.ENGINEER},
        format="json",
    )
    assert response.status_code == 404
    assert response.data["error"]["code"] == "user_not_found"


@pytest.mark.django_db
def test_investor_cannot_manage_members(auth_client, organization, make_user):
    investor = make_user(Role.INVESTOR, phone_number="+237690112111")
    OrganizationMember.objects.create(organization=organization, user=investor, role=Role.INVESTOR)
    candidate = make_user(Role.ENGINEER, phone_number="+237690112222")

    response = auth_client(investor).post(
        f"/api/organizations/{organization.id}/members/",
        {"phone": candidate.phone, "role": Role.ENGINEER},
        format="json",
    )
    assert response.status_code == 403
