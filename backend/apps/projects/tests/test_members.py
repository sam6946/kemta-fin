"""MVP-005 — membres de projet : ajout contrôlé, rôles, retrait, journalisation."""

import pytest

from apps.core.models import ActivityLog
from apps.projects.access import accessible_projects, has_project_capability
from apps.projects.models import ProjectMember
from apps.users.roles import Capability, Role


@pytest.mark.django_db
def test_manager_can_add_member_by_phone(auth_client, project_context, project, make_user):
    newcomer = make_user(Role.ENGINEER, phone_number="+237699444555")
    response = auth_client(project_context["owner"]).post(
        f"/api/projects/{project.id}/members/",
        {"phone": "+237 6 99 44 45 55", "role": Role.CONTRACTOR, "can_manage_finance": True},
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.data["user"]["id"] == newcomer.id
    assert response.data["role"] == Role.CONTRACTOR
    assert response.data["can_manage_finance"] is True

    membership = ProjectMember.objects.get(project=project, user=newcomer)
    assert membership.is_active
    assert project in accessible_projects(newcomer)
    assert has_project_capability(newcomer, project, Capability.MANAGE_FINANCE)

    entry = ActivityLog.objects.get(action="MEMBER_ADDED", project=project)
    assert entry.metadata["user_id"] == newcomer.id
    assert entry.metadata["role"] == Role.CONTRACTOR


@pytest.mark.django_db
def test_add_member_requires_existing_account(auth_client, project_context, project):
    response = auth_client(project_context["owner"]).post(
        f"/api/projects/{project.id}/members/",
        {"phone": "+237699000777", "role": Role.ENGINEER},
        format="json",
    )
    assert response.status_code == 404
    assert response.data["error"]["code"] == "user_not_found"
    assert ProjectMember.objects.filter(project=project).count() == 6  # fixtures inchangées


@pytest.mark.django_db
def test_add_member_rejects_invalid_phone(auth_client, project_context, project):
    response = auth_client(project_context["owner"]).post(
        f"/api/projects/{project.id}/members/",
        {"phone": "123", "role": Role.ENGINEER},
        format="json",
    )
    assert response.status_code == 400
    assert "phone" in response.data["error"]["details"]


@pytest.mark.django_db
def test_duplicate_member_is_refused(auth_client, project_context, project):
    response = auth_client(project_context["owner"]).post(
        f"/api/projects/{project.id}/members/",
        {"phone": project_context["engineer"].phone, "role": Role.ENGINEER},
        format="json",
    )
    assert response.status_code == 409
    assert response.data["error"]["code"] == "member_already_exists"


@pytest.mark.django_db
@pytest.mark.parametrize("actor", ["agent", "investor", "engineer", "finance", "validator"])
def test_non_managers_cannot_add_members(auth_client, project_context, project, actor, make_user):
    newcomer = make_user(Role.ENGINEER, phone_number="+237699555666")
    response = auth_client(project_context[actor]).post(
        f"/api/projects/{project.id}/members/",
        {"phone": newcomer.phone, "role": Role.ENGINEER},
        format="json",
    )
    assert response.status_code == 403, response.content
    assert response.data["error"]["code"] == "permission_denied"
    assert not ProjectMember.objects.filter(project=project, user=newcomer).exists()


@pytest.mark.django_db
def test_removed_member_loses_access(auth_client, project_context, project, make_user):
    member = project_context["agent"]
    membership = ProjectMember.objects.get(project=project, user=member)
    client = auth_client(project_context["owner"])

    assert client.delete(f"/api/projects/{project.id}/members/{membership.id}/").status_code == 204
    assert not ProjectMember.objects.get(pk=membership.pk).is_active
    assert ProjectMember.objects.filter(pk=membership.pk).exists(), (
        "Retrait logique : l'historique demeure."
    )

    assert auth_client(member).get(f"/api/projects/{project.id}/").status_code == 404
    assert ActivityLog.objects.filter(action="MEMBER_REMOVED", project=project).exists()


@pytest.mark.django_db
def test_role_change_is_journalised_with_old_and_new_role(auth_client, project_context, project):
    membership = ProjectMember.objects.get(project=project, user=project_context["agent"])
    response = auth_client(project_context["owner"]).patch(
        f"/api/projects/{project.id}/members/{membership.id}/",
        {
            "phone": project_context["agent"].phone,
            "role": Role.VALIDATOR,
            "can_validate_evidence": True,
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.data["role"] == Role.VALIDATOR

    entry = ActivityLog.objects.filter(action="MEMBER_ROLE_CHANGED", project=project).first()
    assert entry is not None
    assert entry.metadata["changed"]["role"] == {"old": Role.FIELD_AGENT, "new": Role.VALIDATOR}
    # Le nouveau rôle ouvre la validation de preuves.
    assert has_project_capability(project_context["agent"], project, Capability.VALIDATE_EVIDENCE)


@pytest.mark.django_db
def test_project_must_keep_at_least_one_manager(auth_client, project_context, project):
    membership = ProjectMember.objects.get(project=project, user=project_context["owner"])
    response = auth_client(project_context["owner"]).patch(
        f"/api/projects/{project.id}/members/{membership.id}/",
        {"phone": project_context["owner"].phone, "role": Role.FIELD_AGENT},
        format="json",
    )
    assert response.status_code == 409
    assert response.data["error"]["code"] == "last_manager"
    assert ProjectMember.objects.get(pk=membership.pk).role == Role.PROJECT_OWNER


@pytest.mark.django_db
def test_project_creator_cannot_be_removed(auth_client, project_context, project):
    membership = ProjectMember.objects.get(project=project, user=project_context["owner"])
    response = auth_client(project_context["owner"]).delete(
        f"/api/projects/{project.id}/members/{membership.id}/"
    )
    assert response.status_code == 409
    assert response.data["error"]["code"] == "cannot_remove_creator"


@pytest.mark.django_db
def test_investor_sees_members_but_cannot_manage(auth_client, project_context, project):
    response = auth_client(project_context["investor"]).get(f"/api/projects/{project.id}/members/")
    assert response.status_code == 200
    assert response.data["count"] == 6  # lecture autorisée


@pytest.mark.django_db
def test_organization_owner_sees_all_projects_of_the_organization(
    auth_client, organization, project, make_user, project_context
):
    """Le propriétaire de l'organisation pilote l'ensemble de ses projets."""
    assert has_project_capability(organization.owner, project, Capability.EDIT_PROJECT)
    response = auth_client(organization.owner).get(f"/api/projects/{project.id}/")
    assert response.status_code == 200
    assert response.data["permissions"]["manage_members"] is True


@pytest.mark.django_db
def test_partial_patch_of_role_keeps_capabilities(auth_client, project_context, project):
    """Un PATCH qui ne change que le rôle ne doit pas révoquer les capacités accordées.

    Régression : `default=False` sur les capacités les remettait à zéro dès qu'un client
    n'envoyait que `role`, ce qui retirait des droits sans le dire.
    """
    membership = ProjectMember.objects.get(project=project, user=project_context["engineer"])
    membership.can_validate_evidence = True
    membership.can_manage_finance = True
    membership.save(update_fields=["can_validate_evidence", "can_manage_finance"])

    response = auth_client(project_context["owner"]).patch(
        f"/api/projects/{project.id}/members/{membership.id}/",
        {"role": Role.VALIDATOR},
        format="json",
    )

    assert response.status_code == 200
    membership.refresh_from_db()
    assert membership.role == Role.VALIDATOR
    assert membership.can_validate_evidence is True  # conservé
    assert membership.can_manage_finance is True  # conservé


@pytest.mark.django_db
def test_partial_patch_of_capabilities_keeps_role(auth_client, project_context, project):
    """Inversement, un PATCH de capacités seules ne doit pas toucher au rôle ni réactiver."""
    membership = ProjectMember.objects.get(project=project, user=project_context["engineer"])

    response = auth_client(project_context["owner"]).patch(
        f"/api/projects/{project.id}/members/{membership.id}/",
        {"can_manage_finance": True},
        format="json",
    )

    assert response.status_code == 200
    membership.refresh_from_db()
    assert membership.role == Role.ENGINEER
    assert membership.can_manage_finance is True
    assert membership.can_validate_evidence is False  # jamais accordé
    assert membership.is_active is True


@pytest.mark.django_db
def test_partial_patch_does_not_reactivate_a_removed_member(auth_client, project_context, project):
    """`is_active` n'est modifié que s'il est explicitement fourni."""
    membership = ProjectMember.objects.get(project=project, user=project_context["engineer"])
    membership.is_active = False
    membership.save(update_fields=["is_active"])
    owner_client = auth_client(project_context["owner"])

    response = owner_client.patch(
        f"/api/projects/{project.id}/members/{membership.id}/",
        {"can_validate_evidence": True},
        format="json",
    )
    assert response.status_code == 200
    membership.refresh_from_db()
    assert membership.is_active is False

    response = owner_client.patch(
        f"/api/projects/{project.id}/members/{membership.id}/",
        {"is_active": True, "role": Role.ENGINEER},
        format="json",
    )
    assert response.status_code == 200
    membership.refresh_from_db()
    assert membership.is_active is True
