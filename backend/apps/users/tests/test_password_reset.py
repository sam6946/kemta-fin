"""MVP-017 — « Mot de passe oublié » : OTP SMS, réponse neutre, sessions révoquées."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import ActivityLog
from apps.users.models import OTPCode, User
from apps.users.services import sms

NEUTRAL = "Si ce numéro est associé à un compte KEMTA, un code vient d'être envoyé par SMS."


# --- Demande -----------------------------------------------------------------


@pytest.mark.django_db
def test_request_reset_sends_otp_with_dedicated_purpose(active_user, api, phone, reset_otp):
    code = reset_otp()
    otp = OTPCode.objects.get(phone=phone, purpose=OTPCode.Purpose.PASSWORD_RESET)
    assert otp.verify(code)
    assert otp.user_id == active_user.pk
    assert otp.channel == OTPCode.Channel.SMS
    assert code not in otp.code_hash


@pytest.mark.django_db
def test_request_reset_response_is_neutral_for_unknown_number(api):
    response = api.post(
        "/api/auth/password/reset/request/", {"phone": "+237699000111"}, format="json"
    )
    assert response.status_code == 200
    assert response.data["detail"] == NEUTRAL
    assert len(sms.outbox) == 0, "Aucun SMS ne part pour un numéro inconnu."
    assert OTPCode.objects.count() == 0


@pytest.mark.django_db
def test_request_reset_response_is_identical_for_known_number(active_user, api, phone):
    response = api.post("/api/auth/password/reset/request/", {"phone": phone}, format="json")
    assert response.status_code == 200
    assert response.data["detail"] == NEUTRAL
    assert set(response.data) == {"detail", "retry_in", "otp_ttl"}


@pytest.mark.django_db
def test_request_reset_is_throttled(active_user, api, phone):
    assert (
        api.post("/api/auth/password/reset/request/", {"phone": phone}, format="json").status_code
        == 200
    )
    second = api.post("/api/auth/password/reset/request/", {"phone": phone}, format="json")
    assert second.status_code == 429
    assert second.data["error"]["code"] == "otp_resend_limited"


@pytest.mark.django_db
def test_request_reset_for_inactive_account_resends_activation_code(
    api, phone, password, last_sms_code
):
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
    # On sort de la fenêtre de cooldown pour tester le renvoi d'activation.
    OTPCode.objects.filter(phone=phone).update(created_at=timezone.now() - timedelta(seconds=120))
    sms.reset_outbox()
    response = api.post("/api/auth/password/reset/request/", {"phone": phone}, format="json")
    assert response.status_code == 200
    assert response.data["detail"] == NEUTRAL
    otp = OTPCode.objects.filter(phone=phone).order_by("-created_at").first()
    assert otp.purpose == OTPCode.Purpose.SIGNUP, (
        "Le compte non activé reçoit un code d'activation."
    )
    assert ActivityLog.objects.filter(action="PASSWORD_RESET_DENIED").exists()


@pytest.mark.django_db
def test_request_reset_for_deleted_account_is_neutral(active_user, api, phone):
    active_user.delete()  # la suppression logique libère le numéro (marqué)
    sms.reset_outbox()
    response = api.post("/api/auth/password/reset/request/", {"phone": phone}, format="json")
    assert response.status_code == 200
    assert response.data["detail"] == NEUTRAL
    assert len(sms.outbox) == 0, "Aucun SMS n'est envoyé à un compte supprimé."
    assert OTPCode.objects.filter(purpose=OTPCode.Purpose.PASSWORD_RESET).count() == 0


# --- Confirmation -------------------------------------------------------------


@pytest.mark.django_db
def test_confirm_reset_changes_password_and_allows_login(
    active_user, api, phone, reset_otp, password
):
    code = reset_otp()
    response = api.post(
        "/api/auth/password/reset/confirm/",
        {
            "phone": phone,
            "code": code,
            "new_password": "Chantier#2026Kribi",
            "new_password_confirm": "Chantier#2026Kribi",
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.data["sessions_revoked"] is True

    active_user.refresh_from_db()
    assert active_user.check_password("Chantier#2026Kribi")
    assert not active_user.check_password(password)

    login = api.post(
        "/api/auth/login/", {"phone": phone, "password": "Chantier#2026Kribi"}, format="json"
    )
    assert login.status_code == 200, login.content
    assert (
        api.post(
            "/api/auth/login/", {"phone": phone, "password": password}, format="json"
        ).status_code
        == 401
    )


@pytest.mark.django_db
def test_confirm_reset_revokes_existing_sessions(active_user, api, phone, password, reset_otp):
    login = api.post("/api/auth/login/", {"phone": phone, "password": password}, format="json")
    refresh_token = login.data["refresh"]
    access_token = login.data["access"]

    code = reset_otp()
    assert (
        api.post(
            "/api/auth/password/reset/confirm/",
            {
                "phone": phone,
                "code": code,
                "new_password": "Chantier#2026Kribi",
                "new_password_confirm": "Chantier#2026Kribi",
            },
            format="json",
        ).status_code
        == 200
    )

    # Le refresh token existant est révoqué (blacklist).
    replay = api.post("/api/auth/token/refresh/", {"refresh": refresh_token}, format="json")
    assert replay.status_code == 401

    # Le access token émis avant le changement de mot de passe est refusé.
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
    me = api.get("/api/auth/me/")
    assert me.status_code == 401


@pytest.mark.django_db
def test_confirm_reset_refuses_wrong_code(active_user, api, phone, reset_otp):
    reset_otp()
    response = api.post(
        "/api/auth/password/reset/confirm/",
        {
            "phone": phone,
            "code": "000000",
            "new_password": "Chantier#2026Kribi",
            "new_password_confirm": "Chantier#2026Kribi",
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "otp_invalid"


@pytest.mark.django_db
def test_confirm_reset_refuses_expired_code(active_user, api, phone, reset_otp):
    code = reset_otp()
    otp = OTPCode.objects.get(phone=phone, purpose=OTPCode.Purpose.PASSWORD_RESET)
    OTPCode.objects.filter(pk=otp.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

    response = api.post(
        "/api/auth/password/reset/confirm/",
        {
            "phone": phone,
            "code": code,
            "new_password": "Chantier#2026Kribi",
            "new_password_confirm": "Chantier#2026Kribi",
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "otp_invalid"


@pytest.mark.django_db
def test_confirm_reset_cannot_reuse_a_code(active_user, api, phone, reset_otp):
    code = reset_otp()
    payload = {
        "phone": phone,
        "code": code,
        "new_password": "Chantier#2026Kribi",
        "new_password_confirm": "Chantier#2026Kribi",
    }
    assert api.post("/api/auth/password/reset/confirm/", payload, format="json").status_code == 200
    second = api.post(
        "/api/auth/password/reset/confirm/",
        {
            **payload,
            "new_password": "Encore#2026Bafang",
            "new_password_confirm": "Encore#2026Bafang",
        },
        format="json",
    )
    assert second.status_code == 400
    assert second.data["error"]["code"] == "otp_invalid"


@pytest.mark.django_db
def test_confirm_reset_refuses_weak_password(active_user, api, phone, reset_otp):
    code = reset_otp()
    response = api.post(
        "/api/auth/password/reset/confirm/",
        {
            "phone": phone,
            "code": code,
            "new_password": "1234567890",
            "new_password_confirm": "1234567890",
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "password_too_weak"


@pytest.mark.django_db
def test_confirm_reset_refuses_password_reuse(active_user, api, phone, password, reset_otp):
    code = reset_otp()
    response = api.post(
        "/api/auth/password/reset/confirm/",
        {
            "phone": phone,
            "code": code,
            "new_password": password,
            "new_password_confirm": password,
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "password_reused"


@pytest.mark.django_db
def test_confirm_reset_keeps_the_code_usable_when_password_is_refused(
    active_user, api, phone, reset_otp
):
    """Un mot de passe refusé ne doit pas consommer le code : l'utilisateur réessaie."""
    code = reset_otp()
    refused = api.post(
        "/api/auth/password/reset/confirm/",
        {
            "phone": phone,
            "code": code,
            "new_password": "1234567890",
            "new_password_confirm": "1234567890",
        },
        format="json",
    )
    assert refused.status_code == 400
    retry = api.post(
        "/api/auth/password/reset/confirm/",
        {
            "phone": phone,
            "code": code,
            "new_password": "Chantier#2026Kribi",
            "new_password_confirm": "Chantier#2026Kribi",
        },
        format="json",
    )
    assert retry.status_code == 200, retry.content


@pytest.mark.django_db
def test_confirm_reset_is_refused_for_inactive_account(api, phone, password, last_sms_code):
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
    OTPCode.objects.filter(phone=phone).update(created_at=timezone.now() - timedelta(seconds=120))
    assert (
        api.post("/api/auth/password/reset/request/", {"phone": phone}, format="json").status_code
        == 200
    )
    code = last_sms_code()

    response = api.post(
        "/api/auth/password/reset/confirm/",
        {
            "phone": phone,
            "code": code,
            "new_password": "Chantier#2026Kribi",
            "new_password_confirm": "Chantier#2026Kribi",
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "otp_invalid"
    user = User.objects.get(phone=phone)
    assert not user.is_active
    assert not user.check_password("Chantier#2026Kribi")
    assert not OTPCode.objects.filter(
        phone=phone, purpose=OTPCode.Purpose.PASSWORD_RESET, consumed_at__isnull=False
    ).exists()


@pytest.mark.django_db
def test_confirm_reset_sends_confirmation_sms(active_user, api, phone, reset_otp):
    code = reset_otp()
    sms.reset_outbox()
    api.post(
        "/api/auth/password/reset/confirm/",
        {
            "phone": phone,
            "code": code,
            "new_password": "Chantier#2026Kribi",
            "new_password_confirm": "Chantier#2026Kribi",
        },
        format="json",
    )
    assert any("mot de passe" in item["message"] for item in sms.outbox)


@pytest.mark.django_db
def test_reset_flow_is_journalised(active_user, api, phone, reset_otp):
    code = reset_otp()
    api.post(
        "/api/auth/password/reset/confirm/",
        {
            "phone": phone,
            "code": code,
            "new_password": "Chantier#2026Kribi",
            "new_password_confirm": "Chantier#2026Kribi",
        },
        format="json",
    )
    actions = set(ActivityLog.objects.values_list("action", flat=True))
    assert {
        "PASSWORD_RESET_REQUESTED",
        "PASSWORD_RESET_CONFIRMED",
        "OTP_SENT",
        "OTP_VERIFIED",
    } <= actions
    entry = ActivityLog.objects.get(action="PASSWORD_RESET_CONFIRMED")
    assert entry.actor_id == active_user.pk
    assert "sessions_revoked" in entry.metadata


# --- Changement de mot de passe (connecté) ------------------------------------


@pytest.mark.django_db
def test_password_change_requires_current_password(active_user, api, password):
    api.force_authenticate(user=active_user)
    response = api.post(
        "/api/auth/password/change/",
        {
            "current_password": "Mauvais#2026Douala",
            "new_password": "Chantier#2026Kribi",
            "new_password_confirm": "Chantier#2026Kribi",
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_credentials"


@pytest.mark.django_db
def test_password_change_revokes_other_sessions(active_user, api, phone, password):
    first = api.post("/api/auth/login/", {"phone": phone, "password": password}, format="json")
    second = api.post("/api/auth/login/", {"phone": phone, "password": password}, format="json")

    api.force_authenticate(user=active_user)
    response = api.post(
        "/api/auth/password/change/",
        {
            "current_password": password,
            "new_password": "Chantier#2026Kribi",
            "new_password_confirm": "Chantier#2026Kribi",
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.data["sessions_revoked"] is True
    assert response.data["access"], "La session courante est renouvelée."

    assert (
        api.post(
            "/api/auth/token/refresh/", {"refresh": first.data["refresh"]}, format="json"
        ).status_code
        == 401
    )
    assert (
        api.post(
            "/api/auth/token/refresh/", {"refresh": second.data["refresh"]}, format="json"
        ).status_code
        == 401
    )
    # Le nouveau jeton fonctionne.
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    assert api.get("/api/auth/me/").status_code == 200
    assert ActivityLog.objects.filter(action="PASSWORD_CHANGED").exists()


@pytest.mark.django_db
def test_password_change_refuses_reuse(active_user, api, password):
    api.force_authenticate(user=active_user)
    response = api.post(
        "/api/auth/password/change/",
        {
            "current_password": password,
            "new_password": password,
            "new_password_confirm": password,
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "password_reused"
