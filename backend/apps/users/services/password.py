"""Réinitialisation et changement de mot de passe (MVP-017)."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from apps.core.activity import log_event
from apps.core.exceptions import KemtaAPIError
from apps.users.models import OTPCode, User
from apps.users.services import sms
from apps.users.services.otp import get_active_otp, issue_otp, verify_otp

# Message unique : la réponse ne révèle jamais si le compte existe.
NEUTRAL_MESSAGE_FR = (
    "Si ce numéro est associé à un compte KEMTA, un code vient d'être envoyé par SMS."
)


def revoke_all_sessions(user: User) -> int:
    """Révoque **toutes** les sessions actives (blacklist des refresh tokens)."""
    from rest_framework_simplejwt.token_blacklist.models import (
        BlacklistedToken,
        OutstandingToken,
    )

    revoked = 0
    for token in OutstandingToken.objects.filter(user=user).iterator():
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        revoked += int(created)
    return revoked


def _neutral_payload() -> dict:
    return {
        "detail": NEUTRAL_MESSAGE_FR,
        "retry_in": settings.OTP_RESEND_COOLDOWN_SECONDS,
        "otp_ttl": settings.OTP_TTL_SECONDS,
    }


def request_password_reset(phone: str, request=None) -> dict:
    """Demande de réinitialisation.

    **Réponse neutre dans tous les cas** : numéro inconnu, compte désactivé ou
    compte non activé (dans ce dernier cas, un OTP d'activation est renvoyé).
    """
    if not phone:  # numéro invalide : réponse neutre, aucun OTP
        log_event(
            "PASSWORD_RESET_REQUESTED",
            entity_type="User",
            metadata={"outcome": "invalid_phone"},
            request=request,
        )
        return _neutral_payload()

    user = User.all_objects.filter(phone=phone).first()

    if user is None or user.deleted_at is not None:
        log_event(
            "PASSWORD_RESET_DENIED" if user is not None else "PASSWORD_RESET_REQUESTED",
            actor=user if user is not None and user.pk else None,
            entity_type="User",
            entity_id=user.pk if user else None,
            metadata={"outcome": "deleted_account" if user else "unknown_phone"},
            request=request,
        )
        return _neutral_payload()

    if not user.is_active:
        # Compte créé mais jamais activé : on renvoie un code d'activation.
        log_event(
            "PASSWORD_RESET_DENIED",
            actor=user,
            entity_type="User",
            entity_id=user.pk,
            metadata={"outcome": "not_activated"},
            request=request,
        )
        issue_otp(purpose=OTPCode.Purpose.SIGNUP, phone=user.phone, user=user, request=request)
        return _neutral_payload()

    issue_otp(purpose=OTPCode.Purpose.PASSWORD_RESET, phone=user.phone, user=user, request=request)
    log_event(
        "PASSWORD_RESET_REQUESTED",
        actor=user,
        entity_type="User",
        entity_id=user.pk,
        metadata={"outcome": "otp_sent"},
        request=request,
    )
    return _neutral_payload()


def confirm_password_reset(*, phone: str, code: str, new_password: str, request=None) -> User:
    """Valide l'OTP puis change le mot de passe (opération atomique sur l'essentiel)."""
    user = User.all_objects.filter(phone=phone).first()

    # 1. Un code actif doit exister (sans le consommer : la saisie du mot de passe
    #    peut encore échouer, l'utilisateur doit pouvoir réessayer).
    active = get_active_otp(purpose=OTPCode.Purpose.PASSWORD_RESET, phone=phone)
    if active is None:
        log_event(
            "PASSWORD_RESET_FAILED",
            actor=user if user and user.pk else None,
            entity_type="User",
            entity_id=user.pk if user else None,
            metadata={"reason": "no_active_code"},
            request=request,
        )
        raise KemtaAPIError("otp_invalid", "Code invalide ou expiré.")

    if user is None or user.deleted_at is not None or not user.is_active:
        log_event(
            "PASSWORD_RESET_DENIED",
            actor=user if user and user.pk else None,
            entity_type="User",
            entity_id=user.pk if user else None,
            metadata={"reason": "account_unavailable"},
            request=request,
        )
        raise KemtaAPIError("otp_invalid", "Code invalide ou expiré.")

    if user.check_password(new_password):
        raise KemtaAPIError(
            "password_reused", "Le nouveau mot de passe doit être différent de l'actuel."
        )

    try:
        validate_password(new_password, user=user)
    except DjangoValidationError as exc:
        raise KemtaAPIError(
            "password_too_weak",
            "Mot de passe non conforme.",
            details={"messages": list(exc.messages)},
        ) from exc

    # 2. Le code est consommé (usage unique, tentatives limitées).
    verify_otp(code=code, purpose=OTPCode.Purpose.PASSWORD_RESET, phone=phone, request=request)

    # 3. Changement effectif + révocation de toutes les sessions.
    user.set_password(new_password)
    user.touch_password_changed()
    user.save(update_fields=["password", "password_changed_at"])
    revoked = revoke_all_sessions(user)

    sms.send_sms(
        user.phone,
        "KEMTA : votre mot de passe vient d'être modifié. "
        "Si vous n'êtes pas à l'origine de cette action, contactez-nous immédiatement.",
    )
    log_event(
        "PASSWORD_RESET_CONFIRMED",
        actor=user,
        entity_type="User",
        entity_id=user.pk,
        metadata={"sessions_revoked": revoked},
        request=request,
    )
    return user


def change_password(*, user: User, new_password: str, request=None) -> int:
    """Changement de mot de passe pour un utilisateur connecté.

    Révoque toutes les sessions (celle-ci incluse) puis renvoie une nouvelle paire
    de jetons : la session courante continue, les autres appareils sont déconnectés.
    """
    user.set_password(new_password)
    user.touch_password_changed()
    user.save(update_fields=["password", "password_changed_at"])
    revoked = revoke_all_sessions(user)
    log_event(
        "PASSWORD_CHANGED",
        actor=user,
        entity_type="User",
        entity_id=user.pk,
        metadata={"sessions_revoked": revoked},
        request=request,
    )
    return revoked


def email_verification_pending(user: User) -> bool:
    return bool(user.email) and user.email_verified_at is None


def mark_email_verified(user: User, email: str, request=None) -> None:
    user.email = email
    user.email_verified_at = timezone.now()
    user.save(update_fields=["email", "email_verified_at"])
    log_event(
        "EMAIL_VERIFIED",
        actor=user,
        entity_type="User",
        entity_id=user.pk,
        request=request,
    )
