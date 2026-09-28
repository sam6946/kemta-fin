"""Matrice d'accès projet : chaque capacité critique est testée en positif **et** en négatif."""

import pytest

from apps.projects.access import (
    accessible_projects,
    build_capabilities_map,
    has_project_access,
    has_project_capability,
    resolve_capabilities,
)
from apps.projects.models import ProjectMember
from apps.users.roles import Capability, Role

# (acteur du jeu de fixtures, capacité, attendu)
MATRIX = [
    ("owner", Capability.EDIT_PROJECT, True),
    ("owner", Capability.MANAGE_MEMBERS, True),
    ("owner", Capability.MANAGE_FINANCE, True),
    ("owner", Capability.VALIDATE_EVIDENCE, True),
    ("engineer", Capability.EDIT_PROJECT, False),
    ("engineer", Capability.MANAGE_SCHEDULE, True),
    ("engineer", Capability.CAPTURE_EVIDENCE, True),
    ("engineer", Capability.VALIDATE_EVIDENCE, False),
    ("engineer", Capability.MANAGE_FINANCE, False),
    ("agent", Capability.CAPTURE_EVIDENCE, True),
    ("agent", Capability.MANAGE_SCHEDULE, False),
    ("agent", Capability.VIEW_FINANCE, False),
    ("validator", Capability.VALIDATE_EVIDENCE, True),
    ("validator", Capability.CAPTURE_EVIDENCE, False),
    ("validator", Capability.MANAGE_MEMBERS, False),
    ("finance", Capability.MANAGE_FINANCE, True),
    ("finance", Capability.VIEW_FINANCE, True),
    ("finance", Capability.VALIDATE_EVIDENCE, False),
    ("investor", Capability.VIEW_FINANCE, True),
    ("investor", Capability.VIEW_ACTIVITY, True),
    ("investor", Capability.EDIT_PROJECT, False),
    ("investor", Capability.MANAGE_FINANCE, False),
    ("investor", Capability.CAPTURE_EVIDENCE, False),
]


@pytest.mark.django_db
@pytest.mark.parametrize("actor,capability,expected", MATRIX)
def test_capability_matrix(project_context, project, actor, capability, expected):
    assert has_project_capability(project_context[actor], project, capability) is expected


@pytest.mark.django_db
@pytest.mark.parametrize("actor,capability,expected", MATRIX)
def test_vectorised_map_matches_single_project_resolution(
    project_context, project, actor, capability, expected
):
    """La carte calculée en liste doit être identique au calcul unitaire (pas de divergence)."""
    user = project_context[actor]
    assert resolve_capabilities(user, project)[capability] is expected
    assert build_capabilities_map(user, [project])[project.pk][capability] is expected


@pytest.mark.django_db
def test_stranger_has_no_access_and_no_capability(project_context, project):
    stranger = project_context["stranger"]
    assert has_project_access(stranger, project) is False
    assert project not in accessible_projects(stranger)
    assert resolve_capabilities(stranger, project) == dict.fromkeys(
        resolve_capabilities(stranger, project), False
    )


@pytest.mark.django_db
def test_per_project_role_overrides_global_role(project, make_user, organization):
    """Un `INVESTOR` global qui est `ENGINEER` sur un projet agit comme ingénieur sur ce projet."""
    user = make_user(Role.INVESTOR, phone_number="+237699123456")
    ProjectMember.objects.create(project=project, user=user, role=Role.ENGINEER)

    assert has_project_capability(user, project, Capability.MANAGE_SCHEDULE) is True
    # Les capacités de l'ingénieur s'appliquent, pas celles de l'investisseur :
    # l'ingénieur ne peut pas gérer les finances (l'investisseur, lui, ne fait que lire).
    assert has_project_capability(user, project, Capability.MANAGE_FINANCE) is False
    assert has_project_capability(user, project, Capability.EDIT_PROJECT) is False
    # …mais son rôle global reste inchangé ailleurs.
    assert not accessible_projects(user).exclude(pk=project.pk).exists()


@pytest.mark.django_db
def test_flags_grant_extra_capabilities_within_the_project_only(project, make_user):
    agent = make_user(Role.FIELD_AGENT, phone_number="+237699654321")
    ProjectMember.objects.create(
        project=project, user=agent, role=Role.FIELD_AGENT, can_validate_evidence=True
    )
    assert has_project_capability(agent, project, Capability.VALIDATE_EVIDENCE) is True
    assert has_project_capability(agent, project, Capability.MANAGE_MEMBERS) is False


@pytest.mark.django_db
def test_inactive_membership_removes_access(project, project_context):
    membership = ProjectMember.objects.get(project=project, user=project_context["engineer"])
    membership.is_active = False
    membership.save(update_fields=["is_active"])

    engineer = project_context["engineer"]
    assert has_project_access(engineer, project) is False
    assert has_project_capability(engineer, project, Capability.CAPTURE_EVIDENCE) is False


@pytest.mark.django_db
def test_platform_admin_sees_everything(project, make_user):
    admin = make_user(Role.PLATFORM_ADMIN, phone_number="+237699000111")
    assert has_project_access(admin, project) is True
    assert all(resolve_capabilities(admin, project).values())
