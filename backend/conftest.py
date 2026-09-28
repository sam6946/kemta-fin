"""Fixtures partagées : isolation du cache, boîte SMS, utilisateurs et périmètre projet."""

from __future__ import annotations

import logging
import re

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.users.models import OTPCode, User
from apps.users.roles import Role
from apps.users.services import email as email_service
from apps.users.services import sms

TEST_PHONE = "+237690123456"
TEST_PHONE_2 = "+237677888999"
# Conforme à la politique : > 10 caractères, non courant, non similaire aux attributs.
STRONG_PASSWORD = "Kemta#2026Douala"
NEW_STRONG_PASSWORD = "Chantier#2026Kribi"
CODE_RE = re.compile(r"code de vérification est (\d{6})")

# Numéros dédiés au jeu de test du périmètre projets (phase 3).
PHONES_BY_ROLE = {
    Role.PLATFORM_ADMIN: "+237691000001",
    Role.ORG_OWNER: "+237691000002",
    Role.PROJECT_OWNER: "+237691000003",
    Role.ENGINEER: "+237691000004",
    Role.CONTRACTOR: "+237691000005",
    Role.FIELD_AGENT: "+237691000006",
    Role.VALIDATOR: "+237691000007",
    Role.FINANCE: "+237691000008",
    Role.INVESTOR: "+237691000009",
}


@pytest.fixture(autouse=True)
def clear_cache():
    """Les compteurs de rate limiting et le cache de rôles doivent repartir de zéro."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def reset_sms_outbox():
    sms.reset_outbox()
    email_service.reset_outbox()
    yield
    sms.reset_outbox()
    email_service.reset_outbox()


@pytest.fixture(autouse=True)
def propagate_app_logs():
    """Permet à `caplog` de capturer les logs applicatifs pendant les tests."""
    logger = logging.getLogger("kemta")
    previous = logger.propagate
    logger.propagate = True
    yield
    logger.propagate = previous


@pytest.fixture()
def api():
    return APIClient()


@pytest.fixture()
def phone():
    return TEST_PHONE


@pytest.fixture()
def password():
    return STRONG_PASSWORD


@pytest.fixture()
def last_sms_code():
    """Retourne le dernier code OTP envoyé (mode console)."""

    def _get(index: int = -1) -> str:
        message = sms.outbox[index]["message"]
        match = CODE_RE.search(message)
        assert match, f"Aucun code trouvé dans : {message}"
        return match.group(1)

    return _get


@pytest.fixture()
def last_email_code():
    """Retourne le dernier code OTP envoyé par email (mode console)."""

    def _get(index: int = -1) -> str:
        message = email_service.outbox[index]["message"]
        match = CODE_RE.search(message)
        assert match, f"Aucun code trouvé dans : {message}"
        return match.group(1)

    return _get


@pytest.fixture()
def register_and_activate(api, last_sms_code):
    """Crée un utilisateur activé (inscription + OTP) et retourne l'objet `User`."""

    def _make(phone_number: str = TEST_PHONE, pwd: str = STRONG_PASSWORD, **extra):
        payload = {
            "phone": phone_number,
            "password": pwd,
            "password_confirm": pwd,
            "first_name": "Arnaud",
            "last_name": "Nkoulou",
            **extra,
        }
        response = api.post("/api/auth/register/", payload, format="json")
        assert response.status_code == 201, response.content
        code = last_sms_code()
        verify = api.post(
            "/api/auth/otp/verify/",
            {"phone": phone_number, "code": code, "purpose": "SIGNUP"},
            format="json",
        )
        assert verify.status_code == 200, verify.content
        return User.objects.get(phone=phone_number), verify.data

    return _make


@pytest.fixture()
def active_user(register_and_activate):
    user, _ = register_and_activate()
    return user


@pytest.fixture()
def reset_otp(api, last_sms_code):
    """Déclenche une demande de réinitialisation et retourne le code reçu."""

    def _request(phone_number: str = TEST_PHONE) -> str:
        response = api.post(
            "/api/auth/password/reset/request/", {"phone": phone_number}, format="json"
        )
        assert response.status_code == 200, response.content
        return last_sms_code()

    return _request


@pytest.fixture()
def otp_record():
    def _get(phone_number: str = TEST_PHONE, purpose: str = OTPCode.Purpose.SIGNUP):
        return (
            OTPCode.objects.filter(phone=phone_number, purpose=purpose)
            .order_by("-created_at")
            .first()
        )

    return _get


# ---------------------------------------------------------------------------
# Périmètre projets (phase 3)
# ---------------------------------------------------------------------------
@pytest.fixture()
def make_user():
    """Crée un utilisateur actif avec un rôle donné, sans passer par l'API."""

    def _make(role: str = Role.FIELD_AGENT, phone_number: str | None = None, **extra) -> User:
        user = User(
            phone=phone_number or PHONES_BY_ROLE.get(role, "+237699000000"),
            first_name="Test",
            last_name=role.title().replace("_", " "),
            role=role,
            is_active=True,
            is_phone_verified=True,
            **extra,
        )
        user.set_password(STRONG_PASSWORD)
        user.save()
        return user

    return _make


@pytest.fixture()
def auth_client():
    """Client API authentifié par JWT (comme le frontend)."""

    def _client(user: User) -> APIClient:
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    return _client


@pytest.fixture()
def organization(make_user):
    """Organisation de référence possédée par un utilisateur `ORG_OWNER`."""

    from apps.organizations.models import Organization, OrganizationMember

    owner = make_user(Role.ORG_OWNER)
    organization = Organization.objects.create(
        name="KEMTA Promotion Douala", type="PROMOTER", city="Douala", owner=owner
    )
    OrganizationMember.objects.create(organization=organization, user=owner, role=Role.ORG_OWNER)
    return organization


@pytest.fixture()
def project(organization, make_user):
    """Projet de référence (FCFA, jalons à venir) piloté par un `PROJECT_OWNER`."""

    from apps.projects.models import Project, ProjectMember

    owner = make_user(Role.PROJECT_OWNER)
    project = Project.objects.create(
        organization=organization,
        name="Résidence Bonamoussadi — tranche 1",
        code="RBS-T1",
        city="Douala",
        region="Littoral",
        budget_total=50_000_000,
        status="ACTIVE",
        planned_start_date="2026-01-05",
        planned_end_date="2026-12-20",
        created_by=owner,
    )
    ProjectMember.objects.create(
        project=project,
        user=owner,
        role=Role.PROJECT_OWNER,
        can_validate_evidence=True,
        can_manage_finance=True,
    )
    return project


@pytest.fixture()
def project_context(project, organization, make_user):
    """Acteurs du projet : propriétaire, ingénieur, agent terrain, validateur, financier, investisseur."""

    from apps.projects.models import ProjectMember
    from apps.users.roles import Role

    members = {
        "owner": ProjectMember.objects.get(project=project).user,
        "organization": organization,
        "project": project,
    }
    specifications = {
        "engineer": (Role.ENGINEER, {}),
        "agent": (Role.FIELD_AGENT, {}),
        "validator": (Role.VALIDATOR, {"can_validate_evidence": True}),
        "finance": (Role.FINANCE, {"can_manage_finance": True}),
        "investor": (Role.INVESTOR, {}),
    }
    for key, (role, flags) in specifications.items():
        user = make_user(role)
        ProjectMember.objects.create(project=project, user=user, role=role, **flags)
        members[key] = user
    members["stranger"] = make_user(Role.ENGINEER, phone_number="+237699888777")
    return members
