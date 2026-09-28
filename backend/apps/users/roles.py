"""Rôles et capacités : **source de vérité unique**, partagée avec le frontend.

Toute permission critique est testée en positif et en négatif
(`tests/test_role_matrix.py`) à partir des ensembles ci-dessous.
"""

from __future__ import annotations

from django.conf import settings


class Role:
    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    ORG_OWNER = "ORG_OWNER"
    PROJECT_OWNER = "PROJECT_OWNER"
    ENGINEER = "ENGINEER"
    CONTRACTOR = "CONTRACTOR"
    FIELD_AGENT = "FIELD_AGENT"
    VALIDATOR = "VALIDATOR"
    FINANCE = "FINANCE"
    INVESTOR = "INVESTOR"


ROLE_CHOICES = [
    (Role.PLATFORM_ADMIN, "Administrateur plateforme"),
    (Role.ORG_OWNER, "Promoteur / propriétaire d'organisation"),
    (Role.PROJECT_OWNER, "Maître d'ouvrage"),
    (Role.ENGINEER, "Ingénieur / bureau d'études"),
    (Role.CONTRACTOR, "Entreprise / PME de travaux"),
    (Role.FIELD_AGENT, "Agent terrain / chef de chantier"),
    (Role.VALIDATOR, "Contrôleur / validateur"),
    (Role.FINANCE, "Gestionnaire financier"),
    (Role.INVESTOR, "Investisseur / bailleur"),
]

ROLE_LABELS = dict(ROLE_CHOICES)
ALL_ROLES = [code for code, _ in ROLE_CHOICES]


class Capability:
    CREATE_ORGANIZATION = "create_organization"
    CREATE_PROJECT = "create_project"
    EDIT_PROJECT = "edit_project"
    ARCHIVE_PROJECT = "archive_project"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_SCHEDULE = "manage_schedule"
    UPDATE_TASK = "update_task"
    CAPTURE_EVIDENCE = "capture_evidence"
    VALIDATE_EVIDENCE = "validate_evidence"
    VIEW_FINANCE = "view_finance"
    MANAGE_FINANCE = "manage_finance"
    VIEW_ACTIVITY = "view_activity"
    VIEW_AUTH_LOGS = "view_auth_logs"
    VIEW_OPERATIONS = "view_operations"


ALL_CAPABILITIES = [value for name, value in vars(Capability).items() if not name.startswith("_")]


def _set(*roles: str) -> frozenset:
    return frozenset(roles)


# Matrice des capacités globales (voir docs/rbac-matrix.md).
ROLE_CAPABILITIES: dict[str, frozenset] = {
    Role.PLATFORM_ADMIN: _set(*ALL_CAPABILITIES),
    Role.ORG_OWNER: _set(
        Capability.CREATE_ORGANIZATION,
        Capability.CREATE_PROJECT,
        Capability.EDIT_PROJECT,
        Capability.ARCHIVE_PROJECT,
        Capability.MANAGE_MEMBERS,
        Capability.MANAGE_SCHEDULE,
        Capability.UPDATE_TASK,
        Capability.CAPTURE_EVIDENCE,
        Capability.VALIDATE_EVIDENCE,
        Capability.VIEW_FINANCE,
        Capability.MANAGE_FINANCE,
        Capability.VIEW_ACTIVITY,
    ),
    Role.PROJECT_OWNER: _set(
        Capability.CREATE_PROJECT,
        Capability.EDIT_PROJECT,
        Capability.ARCHIVE_PROJECT,
        Capability.MANAGE_MEMBERS,
        Capability.MANAGE_SCHEDULE,
        Capability.UPDATE_TASK,
        Capability.CAPTURE_EVIDENCE,
        Capability.VALIDATE_EVIDENCE,
        Capability.VIEW_FINANCE,
        Capability.MANAGE_FINANCE,
        Capability.VIEW_ACTIVITY,
    ),
    Role.ENGINEER: _set(
        Capability.MANAGE_SCHEDULE,
        Capability.UPDATE_TASK,
        Capability.CAPTURE_EVIDENCE,
        Capability.VIEW_FINANCE,
        Capability.VIEW_ACTIVITY,
    ),
    Role.CONTRACTOR: _set(
        Capability.UPDATE_TASK,
        Capability.CAPTURE_EVIDENCE,
        Capability.VIEW_FINANCE,
        Capability.VIEW_ACTIVITY,
    ),
    Role.FIELD_AGENT: _set(Capability.CAPTURE_EVIDENCE),
    Role.VALIDATOR: _set(Capability.VALIDATE_EVIDENCE, Capability.VIEW_ACTIVITY),
    Role.FINANCE: _set(
        Capability.VIEW_FINANCE,
        Capability.MANAGE_FINANCE,
        Capability.VIEW_ACTIVITY,
    ),
    Role.INVESTOR: _set(Capability.VIEW_FINANCE, Capability.VIEW_ACTIVITY),
}


def role_has(role: str, capability: str) -> bool:
    return capability in ROLE_CAPABILITIES.get(role, frozenset())


def user_has(user, capability: str) -> bool:
    """Le backend reste l'autorité : le superutilisateur a tous les droits."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return role_has(getattr(user, "role", ""), capability)


def default_role() -> str:
    configured = getattr(settings, "DEFAULT_SIGNUP_ROLE", Role.PROJECT_OWNER)
    return configured if configured in ALL_ROLES else Role.PROJECT_OWNER


def roles_payload() -> list[dict]:
    """Charge utile de `GET /api/meta/roles/` (consommée par le frontend)."""
    return [
        {
            "code": code,
            "label": ROLE_LABELS[code],
            "capabilities": sorted(ROLE_CAPABILITIES.get(code, frozenset())),
        }
        for code in ALL_ROLES
    ]
