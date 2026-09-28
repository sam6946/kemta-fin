"""MVP-003 — connexion, refresh, rotation, révocation, verrouillage."""

import pytest
from django.conf import settings

from apps.users.models import User


@pytest.mark.django_db
def test_login_returns_tokens(register_and_activate, api, phone, password):
    register_and_activate()
    response = api.post("/api/auth/login/", {"phone": phone, "password": password}, format="json")
    assert response.status_code == 200, response.content
    assert response.data["access"] and response.data["refresh"]
    assert response.data["user"]["role"] == "PROJECT_OWNER"


@pytest.mark.django_db
def test_unconfirmed_account_cannot_login(api, phone, password):
    api.post(
        "/api/auth/register/",
        {
            "phone": phone,
            "password": password,
            "password_confirm": password,
            "first_name": "A",
            "last_name": "B",
        },
        format="json",
    )
    response = api.post("/api/auth/login/", {"phone": phone, "password": password}, format="json")
    assert response.status_code == 403
    assert response.data["error"]["code"] == "account_not_confirmed"
    assert response.data["error"]["details"]["can_resend"] is True


@pytest.mark.django_db
def test_unknown_phone_and_wrong_password_give_the_same_answer(active_user, api, phone, password):
    unknown = api.post(
        "/api/auth/login/", {"phone": "+237699111222", "password": password}, format="json"
    )
    wrong = api.post(
        "/api/auth/login/", {"phone": phone, "password": "Mauvais#2026Douala"}, format="json"
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.data["error"]["code"] == wrong.data["error"]["code"] == "invalid_credentials"
    assert unknown.data["error"]["message"] == wrong.data["error"]["message"]


@pytest.mark.django_db
def test_account_is_locked_after_repeated_failures(active_user, api, phone):
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS):
        api.post(
            "/api/auth/login/", {"phone": phone, "password": "Mauvais#2026Douala"}, format="json"
        )

    response = api.post(
        "/api/auth/login/", {"phone": phone, "password": "Kemta#2026Douala"}, format="json"
    )
    assert response.status_code == 429
    assert response.data["error"]["code"] == "account_locked"
    assert User.objects.get(phone=phone).locked_until is not None


@pytest.mark.django_db
def test_successful_login_resets_failure_counter(active_user, api, phone, password):
    api.post("/api/auth/login/", {"phone": phone, "password": "Mauvais#2026Douala"}, format="json")
    api.post("/api/auth/login/", {"phone": phone, "password": password}, format="json")
    user = User.objects.get(phone=phone)
    assert user.failed_login_count == 0
    assert user.locked_until is None


@pytest.mark.django_db
def test_refresh_rotation_invalidates_the_old_token(register_and_activate, api):
    _, payload = register_and_activate()
    old_refresh = payload["refresh"]
    response = api.post("/api/auth/token/refresh/", {"refresh": old_refresh}, format="json")
    assert response.status_code == 200, response.content
    assert response.data["refresh"] != old_refresh

    replay = api.post("/api/auth/token/refresh/", {"refresh": old_refresh}, format="json")
    assert replay.status_code == 401


@pytest.mark.django_db
def test_logout_revokes_refresh_token(active_user, api, phone, password):
    login = api.post("/api/auth/login/", {"phone": phone, "password": password}, format="json")
    refresh = login.data["refresh"]

    api.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    response = api.post("/api/auth/logout/", {"refresh": refresh}, format="json")
    assert response.status_code == 204

    replay = api.post("/api/auth/token/refresh/", {"refresh": refresh}, format="json")
    assert replay.status_code == 401


@pytest.mark.django_db
def test_logout_requires_a_refresh_token(active_user, api):
    api.force_authenticate(user=active_user)
    response = api.post("/api/auth/logout/", {}, format="json")
    assert response.status_code == 400
    assert response.data["error"]["code"] == "missing_refresh_token"


@pytest.mark.django_db
def test_me_requires_authentication(api):
    response = api.get("/api/auth/me/")
    assert response.status_code == 401
    assert response.data["error"]["code"] == "not_authenticated"


@pytest.mark.django_db
def test_me_returns_profile_and_capabilities(active_user, api):
    api.force_authenticate(user=active_user)
    response = api.get("/api/auth/me/")
    assert response.status_code == 200
    assert response.data["phone"] == active_user.phone
    assert response.data["phone_masked"] != active_user.phone
    assert "create_project" in response.data["capabilities"]


@pytest.mark.django_db
def test_deleted_user_token_is_rejected(active_user, api, phone, password):
    login = api.post("/api/auth/login/", {"phone": phone, "password": password}, format="json")
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    assert api.get("/api/auth/me/").status_code == 200

    active_user.delete()  # suppression logique : l'utilisateur devient invisible
    response = api.get("/api/auth/me/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_error_envelope_contains_request_id(api, phone):
    response = api.post("/api/auth/login/", {"phone": phone, "password": "x"}, format="json")
    assert "request_id" in response.data["error"]
    assert response["X-Request-ID"]
