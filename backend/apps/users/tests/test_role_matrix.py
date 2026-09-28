"""MVP-004 — la matrice des rôles est testée positivement **et** négativement."""

import pytest

from apps.users.roles import (
    ALL_CAPABILITIES,
    ALL_ROLES,
    ROLE_CAPABILITIES,
    Capability,
    Role,
    role_has,
    roles_payload,
    user_has,
)


def test_nine_roles_are_defined():
    assert len(ALL_ROLES) == 9
    assert set(ALL_ROLES) == {
        Role.PLATFORM_ADMIN,
        Role.ORG_OWNER,
        Role.PROJECT_OWNER,
        Role.ENGINEER,
        Role.CONTRACTOR,
        Role.FIELD_AGENT,
        Role.VALIDATOR,
        Role.FINANCE,
        Role.INVESTOR,
    }


# (rôle, capacité, autorisé ?) — extrait critique de docs/rbac-matrix.md
EXPECTED = [
    (Role.PLATFORM_ADMIN, Capability.VIEW_AUTH_LOGS, True),
    (Role.ORG_OWNER, Capability.CREATE_ORGANIZATION, True),
    (Role.ORG_OWNER, Capability.VIEW_AUTH_LOGS, False),
    (Role.PROJECT_OWNER, Capability.CREATE_ORGANIZATION, False),
    (Role.PROJECT_OWNER, Capability.CREATE_PROJECT, True),
    (Role.PROJECT_OWNER, Capability.MANAGE_MEMBERS, True),
    (Role.ENGINEER, Capability.MANAGE_SCHEDULE, True),
    (Role.ENGINEER, Capability.VALIDATE_EVIDENCE, False),
    (Role.ENGINEER, Capability.MANAGE_FINANCE, False),
    (Role.CONTRACTOR, Capability.CAPTURE_EVIDENCE, True),
    (Role.CONTRACTOR, Capability.VALIDATE_EVIDENCE, False),
    (Role.CONTRACTOR, Capability.MANAGE_FINANCE, False),
    (Role.FIELD_AGENT, Capability.CAPTURE_EVIDENCE, True),
    (Role.FIELD_AGENT, Capability.VIEW_FINANCE, False),
    (Role.FIELD_AGENT, Capability.MANAGE_SCHEDULE, False),
    (Role.VALIDATOR, Capability.VALIDATE_EVIDENCE, True),
    (Role.VALIDATOR, Capability.CAPTURE_EVIDENCE, False),
    (Role.FINANCE, Capability.MANAGE_FINANCE, True),
    (Role.FINANCE, Capability.VALIDATE_EVIDENCE, False),
    (Role.INVESTOR, Capability.VIEW_FINANCE, True),
    (Role.INVESTOR, Capability.CAPTURE_EVIDENCE, False),
    (Role.INVESTOR, Capability.MANAGE_FINANCE, False),
]


@pytest.mark.parametrize("role,capability,expected", EXPECTED)
def test_role_capability_matrix(role, capability, expected):
    assert role_has(role, capability) is expected


@pytest.mark.django_db
@pytest.mark.parametrize("role,capability,expected", EXPECTED)
def test_user_has_matches_the_matrix(role, capability, expected, django_user_model):
    user = django_user_model(role=role, is_active=True)
    # `user_has` ne dépend pas de la base : on vérifie le comportement public.
    assert user_has(user, capability) is expected


def test_unknown_role_has_no_capability():
    assert role_has("UNKNOWN_ROLE", Capability.CREATE_PROJECT) is False


def test_anonymous_user_has_no_capability():
    from django.contrib.auth.models import AnonymousUser

    assert user_has(AnonymousUser(), Capability.CREATE_PROJECT) is False


@pytest.mark.django_db
def test_superuser_bypasses_the_matrix(django_user_model):
    admin = django_user_model(is_superuser=True, is_active=True)
    assert all(user_has(admin, capability) for capability in ALL_CAPABILITIES)


def test_roles_payload_is_stable_for_the_frontend():
    payload = roles_payload()
    assert len(payload) == 9
    assert {item["code"] for item in payload} == set(ALL_ROLES)
    for item in payload:
        assert item["label"]
        assert set(item["capabilities"]) <= set(ALL_CAPABILITIES)
        assert item["capabilities"] == sorted(ROLE_CAPABILITIES.get(item["code"], frozenset()))
