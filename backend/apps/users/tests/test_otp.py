"""MVP-002 — OTP SMS : haché, expirable, à usage unique, tentatives et renvoi limités."""

from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from apps.users.models import OTPCode, User


def _register(api, phone, password):
    return api.post(
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


@pytest.mark.django_db
def test_otp_is_stored_hashed_and_never_in_clear(api, phone, password, last_sms_code):
    _register(api, phone, password)
    code = last_sms_code()
    otp = OTPCode.objects.get(phone=phone, purpose=OTPCode.Purpose.SIGNUP)

    assert otp.code_hash != code
    assert code not in otp.code_hash
    assert code not in (otp.salt or "")
    # Aucun champ de l'objet ne contient le code en clair.
    serialized = {f.name: str(getattr(otp, f.name)) for f in OTPCode._meta.fields}
    assert all(code not in value for value in serialized.values())


@pytest.mark.django_db
def test_valid_otp_activates_account_and_returns_tokens(api, phone, password, last_sms_code):
    _register(api, phone, password)
    response = api.post(
        "/api/auth/otp/verify/",
        {"phone": phone, "code": last_sms_code(), "purpose": "SIGNUP"},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.data["access"] and response.data["refresh"]
    assert response.data["user"]["phone"] == phone
    user = User.objects.get(phone=phone)
    assert user.is_active and user.is_phone_verified


@pytest.mark.django_db
def test_wrong_code_is_refused_and_decrements_attempts(api, phone, password, last_sms_code):
    _register(api, phone, password)
    code = last_sms_code()
    response = api.post(
        "/api/auth/otp/verify/",
        {"phone": phone, "code": "000000" if code != "000000" else "111111", "purpose": "SIGNUP"},
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "otp_invalid"
    assert response.data["error"]["details"]["remaining_attempts"] == settings.OTP_MAX_ATTEMPTS - 1
    assert User.objects.get(phone=phone).is_active is False


@pytest.mark.django_db
def test_expired_code_is_refused(api, phone, password, last_sms_code):
    _register(api, phone, password)
    code = last_sms_code()
    otp = OTPCode.objects.get(phone=phone, purpose=OTPCode.Purpose.SIGNUP)
    OTPCode.objects.filter(pk=otp.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    response = api.post(
        "/api/auth/otp/verify/",
        {"phone": phone, "code": code, "purpose": "SIGNUP"},
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "otp_invalid"


@pytest.mark.django_db
def test_consumed_code_cannot_be_reused(api, phone, password, last_sms_code):
    _register(api, phone, password)
    code = last_sms_code()
    assert (
        api.post(
            "/api/auth/otp/verify/",
            {"phone": phone, "code": code, "purpose": "SIGNUP"},
            format="json",
        ).status_code
        == 200
    )
    response = api.post(
        "/api/auth/otp/verify/",
        {"phone": phone, "code": code, "purpose": "SIGNUP"},
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "otp_invalid"


@pytest.mark.django_db
def test_max_attempts_locks_the_code(api, phone, password, last_sms_code):
    _register(api, phone, password)
    wrong = "000000" if last_sms_code() != "000000" else "111111"
    statuses = []
    for _ in range(settings.OTP_MAX_ATTEMPTS):
        response = api.post(
            "/api/auth/otp/verify/",
            {"phone": phone, "code": wrong, "purpose": "SIGNUP"},
            format="json",
        )
        statuses.append(response.status_code)
    assert statuses[: settings.OTP_MAX_ATTEMPTS - 1] == [400] * (settings.OTP_MAX_ATTEMPTS - 1)
    assert statuses[-1] == 429
    assert response.data["error"]["code"] == "otp_max_attempts"
    assert OTPCode.objects.get(phone=phone, purpose=OTPCode.Purpose.SIGNUP).is_consumed


@pytest.mark.django_db
def test_resend_is_limited_by_cooldown(api, phone, password):
    _register(api, phone, password)
    response = api.post(
        "/api/auth/otp/resend/", {"phone": phone, "purpose": "SIGNUP"}, format="json"
    )
    assert response.status_code == 429
    assert response.data["error"]["code"] == "otp_resend_limited"
    assert "retry_in" in response.data["error"]["details"]


@pytest.mark.django_db
def test_resend_works_after_cooldown_and_invalidates_previous_code(
    api, phone, password, last_sms_code
):
    _register(api, phone, password)
    first_code = last_sms_code()
    otp = OTPCode.objects.get(phone=phone, purpose=OTPCode.Purpose.SIGNUP)
    OTPCode.objects.filter(pk=otp.pk).update(
        created_at=timezone.now() - timedelta(seconds=settings.OTP_RESEND_COOLDOWN_SECONDS + 1)
    )

    response = api.post(
        "/api/auth/otp/resend/", {"phone": phone, "purpose": "SIGNUP"}, format="json"
    )
    assert response.status_code == 200, response.content
    new_code = last_sms_code()
    # Le renvoi peut produire le même code : on vérifie l'invalidation de l'ancien code
    # (assertions ci-dessous) et non une différence de valeur, qui serait instable.
    newest = OTPCode.objects.filter(phone=phone, purpose=OTPCode.Purpose.SIGNUP).first()
    assert newest is not None and newest.pk != otp.pk  # un nouveau code remplace l'ancien
    assert newest.code_hash == newest.hash_code(new_code, newest.salt)
    assert newest.is_usable
    otp.refresh_from_db()
    assert not otp.is_usable  # l'ancien code est invalidé, jamais supprimé

    assert (
        api.post(
            "/api/auth/otp/verify/",
            {"phone": phone, "code": first_code, "purpose": "SIGNUP"},
            format="json",
        ).status_code
        == 400
    )
    assert (
        api.post(
            "/api/auth/otp/verify/",
            {"phone": phone, "code": new_code, "purpose": "SIGNUP"},
            format="json",
        ).status_code
        == 200
    )


@pytest.mark.django_db
def test_resend_for_unknown_or_active_number_is_neutral(api, phone, password, last_sms_code):
    response = api.post(
        "/api/auth/otp/resend/", {"phone": "+237699000000", "purpose": "SIGNUP"}, format="json"
    )
    assert response.status_code == 200
    assert "activation" in response.data["detail"]
    assert len(OTPCode.objects.all()) == 0


@pytest.mark.django_db
def test_otp_purpose_is_strict(api, phone, password, reset_otp, register_and_activate):
    register_and_activate()
    reset_code = reset_otp()
    # Un code de réinitialisation ne peut pas servir à activer un compte.
    response = api.post(
        "/api/auth/otp/verify/",
        {"phone": phone, "code": reset_code, "purpose": "SIGNUP"},
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "otp_invalid"
