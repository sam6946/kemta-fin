"""Exigences transverses de sécurité : secrets, énumération, rate limiting, métadonnées."""

import logging

import pytest

from apps.core.models import ActivityLog
from apps.users.models import OTPCode
from apps.users.services import sms


@pytest.mark.django_db
def test_no_secret_in_logs_during_the_full_auth_journey(
    api, phone, password, last_sms_code, reset_otp, caplog
):
    caplog.set_level(logging.DEBUG)
    api.post(
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
    signup_code = last_sms_code()
    api.post(
        "/api/auth/otp/verify/",
        {"phone": phone, "code": signup_code, "purpose": "SIGNUP"},
        format="json",
    )
    reset_code = reset_otp()
    api.post(
        "/api/auth/password/reset/confirm/",
        {
            "phone": phone,
            "code": reset_code,
            "new_password": "Chantier#2026Kribi",
            "new_password_confirm": "Chantier#2026Kribi",
        },
        format="json",
    )

    messages = " ".join(record.getMessage() for record in caplog.records)
    for secret in (password, signup_code, reset_code, "Chantier#2026Kribi"):
        assert secret not in messages, f"Secret `{secret}` trouvé dans les logs !"

    # Les journaux applicatifs ne contiennent rien de sensible non plus.
    for entry in ActivityLog.objects.all():
        blob = f"{entry.metadata}"
        assert password not in blob
        assert signup_code not in blob
        assert reset_code not in blob


@pytest.mark.django_db
def test_otp_codes_are_never_persisted_in_clear(active_user, reset_otp, phone):
    code = reset_otp()
    assert not OTPCode.objects.filter(code_hash=code).exists()
    reset_otp_record = OTPCode.objects.filter(
        phone=phone, purpose=OTPCode.Purpose.PASSWORD_RESET
    ).first()
    assert code not in reset_otp_record.code_hash


@pytest.mark.django_db
def test_no_account_enumeration_between_known_and_unknown_numbers(active_user, api, phone):
    known = api.post("/api/auth/password/reset/request/", {"phone": phone}, format="json")
    unknown = api.post(
        "/api/auth/password/reset/request/", {"phone": "+237699222333"}, format="json"
    )
    assert known.status_code == unknown.status_code
    assert known.data["detail"] == unknown.data["detail"]
    assert set(known.data) == set(unknown.data)


@pytest.mark.django_db
def test_login_endpoint_is_rate_limited(api):
    codes = []
    for _ in range(14):
        response = api.post(
            "/api/auth/login/", {"phone": "+237699333444", "password": "x"}, format="json"
        )
        codes.append(response.status_code)
        if response.status_code == 429 and response.data["error"]["code"] == "rate_limited":
            break
    assert 429 in codes
    assert response.data["error"]["code"] == "rate_limited"


@pytest.mark.django_db
def test_sensitive_endpoints_are_throttled_by_configuration():
    from apps.users.views import (
        LoginView,
        OTPVerifyView,
        PasswordResetConfirmView,
        PasswordResetRequestView,
        RegisterView,
    )

    for view in (
        RegisterView,
        OTPVerifyView,
        PasswordResetRequestView,
        PasswordResetConfirmView,
        LoginView,
    ):
        assert view.throttle_classes, f"{view.__name__} doit être protégé par rate limiting."


@pytest.mark.django_db
def test_sms_console_provider_does_not_leak_outside_debug(active_user, settings, phone):
    settings.DEBUG = False
    sms.reset_outbox()
    from apps.users.services import otp

    otp.issue_otp(purpose=OTPCode.Purpose.PASSWORD_RESET, phone=phone, user=active_user)
    assert len(sms.outbox) == 1  # le message est capté, jamais imprimé hors DEBUG


@pytest.mark.django_db
def test_tokens_issued_before_password_change_are_rejected(active_user, api, phone, password):
    from datetime import timedelta

    from django.utils import timezone

    login = api.post("/api/auth/login/", {"phone": phone, "password": password}, format="json")
    access = login.data["access"]

    api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    assert api.get("/api/auth/me/").status_code == 200

    # Simule une réinitialisation postérieure à l'émission du jeton.
    active_user.password_changed_at = timezone.now() + timedelta(seconds=30)
    active_user.save(update_fields=["password_changed_at"])

    response = api.get("/api/auth/me/")
    assert response.status_code == 401
    assert response.data["error"]["code"] == "not_authenticated"
