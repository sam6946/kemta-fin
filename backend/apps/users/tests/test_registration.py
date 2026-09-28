"""MVP-001 — inscription par numéro de téléphone (sans email)."""

import pytest

from apps.users.models import OTPCode, User


@pytest.mark.django_db
def test_register_creates_inactive_user_and_sends_otp(api, phone, password, last_sms_code):
    response = api.post(
        "/api/auth/register/",
        {
            "phone": "06 90 12 34 56",
            "password": password,
            "password_confirm": password,
            "first_name": "Arnaud",
            "last_name": "Nkoulou",
        },
        format="json",
    )
    assert response.status_code == 201, response.content

    user = User.objects.get(phone="+237690123456")
    assert user.is_active is False, "Aucun compte n'est actif avant validation de l'OTP."
    assert user.is_phone_verified is False
    assert user.check_password(password)
    assert user.email in (None, ""), "L'email n'est jamais demandé à l'inscription."

    otp = OTPCode.objects.filter(user=user, purpose=OTPCode.Purpose.SIGNUP).first()
    assert otp is not None and otp.is_usable
    assert last_sms_code()  # un SMS contenant le code a été émis


@pytest.mark.django_db
def test_register_response_never_contains_code_or_password(api, phone, password):
    response = api.post(
        "/api/auth/register/",
        {
            "phone": phone,
            "password": password,
            "password_confirm": password,
            "first_name": "Arnaud",
            "last_name": "Nkoulou",
        },
        format="json",
    )
    body = response.content.decode()
    assert password not in body
    assert "code" not in body.lower()
    assert response.data["phone_masked"].endswith("56")


@pytest.mark.django_db
def test_invalid_phone_is_refused(api, password):
    response = api.post(
        "/api/auth/register/",
        {
            "phone": "12345",
            "password": password,
            "password_confirm": password,
            "first_name": "A",
            "last_name": "B",
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "validation_error"
    assert "phone" in response.data["error"]["details"]


@pytest.mark.django_db
def test_foreign_phone_is_refused(api, password):
    response = api.post(
        "/api/auth/register/",
        {
            "phone": "+33612345678",
            "password": password,
            "password_confirm": password,
            "first_name": "A",
            "last_name": "B",
        },
        format="json",
    )
    assert response.status_code == 400
    assert "phone" in response.data["error"]["details"]


@pytest.mark.django_db
def test_duplicate_phone_is_refused_with_clear_code(api, register_and_activate, phone, password):
    register_and_activate()
    response = api.post(
        "/api/auth/register/",
        {
            "phone": phone,
            "password": password,
            "password_confirm": password,
            "first_name": "Arnaud",
            "last_name": "Nkoulou",
        },
        format="json",
    )
    assert response.status_code == 409
    assert response.data["error"]["code"] == "phone_already_used"


@pytest.mark.django_db
def test_pending_account_can_resend_otp(api, phone, password):
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
    response = api.post(
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
    assert response.status_code == 409
    assert response.data["error"]["code"] == "phone_pending_activation"
    assert response.data["error"]["details"]["can_resend"] is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    "weak",
    ["1234567890", "password", "0000000000", "Kemta123", "1234"],
)
def test_weak_passwords_are_refused(api, phone, weak):
    response = api.post(
        "/api/auth/register/",
        {
            "phone": phone,
            "password": weak,
            "password_confirm": weak,
            "first_name": "Arnaud",
            "last_name": "Nkoulou",
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "password_too_weak"


@pytest.mark.django_db
def test_password_mismatch_is_refused(api, phone, password):
    response = api.post(
        "/api/auth/register/",
        {
            "phone": phone,
            "password": password,
            "password_confirm": "Autre#2026Douala",
            "first_name": "A",
            "last_name": "B",
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "password_mismatch"
