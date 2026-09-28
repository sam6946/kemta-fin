"""Métadonnées exposées au frontend (source unique des rôles)."""

import pytest

from apps.core.models import ActivityLog
from apps.users.models import OTPCode


@pytest.mark.django_db
def test_roles_endpoint_is_public_and_cached(api):
    response = api.get("/api/meta/roles/")
    assert response.status_code == 200
    results = response.data["results"]
    assert len(results) == 9
    assert {role["code"] for role in results}
    # Second appel servi par le cache, réponse identique.
    assert api.get("/api/meta/roles/").data["results"] == results


@pytest.mark.django_db
def test_email_can_be_added_later_with_otp(active_user, api, phone, password, last_email_code):
    """L'email n'est pas demandé à l'inscription : il est ajouté puis vérifié plus tard."""
    assert active_user.email in (None, "")

    login = api.post("/api/auth/login/", {"phone": phone, "password": password}, format="json")
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

    request = api.post("/api/auth/email/request/", {"email": "arnaud@example.cm"}, format="json")
    assert request.status_code == 200, request.content

    otp = OTPCode.objects.filter(user=active_user, purpose=OTPCode.Purpose.EMAIL_VERIFY).first()
    assert otp is not None and otp.is_usable
    assert otp.verify(last_email_code())  # le code part bien par email

    # Mauvais code : refusé, et l'email n'est pas marqué comme vérifié.
    wrong = api.post(
        "/api/auth/email/confirm/",
        {"email": "arnaud@example.cm", "code": "000000"},
        format="json",
    )
    assert wrong.status_code == 400
    assert wrong.data["error"]["code"] == "otp_invalid"

    confirm = api.post(
        "/api/auth/email/confirm/",
        {"email": "arnaud@example.cm", "code": last_email_code()},
        format="json",
    )
    assert confirm.status_code == 200, confirm.content
    assert confirm.data["email"] == "arnaud@example.cm"

    active_user.refresh_from_db()
    assert active_user.email == "arnaud@example.cm"
    assert active_user.email_verified_at is not None
    assert ActivityLog.objects.filter(action="EMAIL_VERIFIED").exists()


@pytest.mark.django_db
def test_status_meta_lists_official_statuses(auth_client, project_context):
    """`/api/meta/status/` alimente les listes déroulantes du frontend."""
    response = auth_client(project_context["owner"]).get("/api/meta/status/")

    assert response.status_code == 200
    assert [item["value"] for item in response.data["milestone"]] == [
        "PLANNED",
        "IN_PROGRESS",
        "DONE",
        "BLOCKED",
        "CANCELLED",
    ]
    assert [item["value"] for item in response.data["task"]] == [
        "TODO",
        "IN_PROGRESS",
        "DONE",
        "BLOCKED",
        "CANCELLED",
    ]
    assert response.data["milestone"][2]["label"] == "Terminé"
    assert {"value": "ON_HOLD", "label": "Suspendu"} in response.data["project"]


def test_status_meta_requires_authentication(api):
    assert api.get("/api/meta/status/").status_code == 401
