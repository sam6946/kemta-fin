"""Service OTP : génération, hachage, expiration, tentatives, renvoi contrôlé."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.core.activity import log_event
from apps.core.exceptions import KemtaAPIError
from apps.users.models import OTPCode


def generate_code(length: int | None = None) -> str:
    """Code numérique aléatoire (cryptographiquement sûr, sans zéro initial)."""
    size = length or settings.OTP_LENGTH
    return "".join(str(secrets.randbelow(10)) for _ in range(size))


def _destination_filters(phone: str | None, email: str | None) -> dict[str, Any]:
    if phone:
        return {"phone": phone}
    return {"email": (email or "").lower()}


def _enforce_send_limits(
    *, phone: str | None, email: str | None, purpose: str, ip_address: str | None
) -> None:
    """Cooldown + quotas par destination et par adresse IP."""
    now = timezone.now()
    filters = _destination_filters(phone, email)
    queryset = OTPCode.objects.filter(purpose=purpose, **filters)

    last = queryset.order_by("-created_at").first()
    if last and (now - last.created_at) < timedelta(seconds=settings.OTP_RESEND_COOLDOWN_SECONDS):
        retry_in = int(
            settings.OTP_RESEND_COOLDOWN_SECONDS - (now - last.created_at).total_seconds()
        )
        raise KemtaAPIError(
            "otp_resend_limited",
            f"Merci de patienter {max(retry_in, 1)} s avant de renvoyer un code.",
            http_status=429,
            details={"retry_in": max(retry_in, 1)},
        )

    window = now - timedelta(hours=1)
    sent_to_destination = queryset.filter(created_at__gte=window).count()
    if sent_to_destination >= settings.OTP_RESEND_LIMIT_PER_PHONE:
        raise KemtaAPIError(
            "otp_resend_limited",
            "Trop de codes demandés pour ce numéro. Réessayez plus tard.",
            http_status=429,
        )

    if ip_address:
        sent_from_ip = OTPCode.objects.filter(
            purpose=purpose, ip_address=ip_address, created_at__gte=window
        ).count()
        if sent_from_ip >= settings.OTP_RESEND_LIMIT_PER_IP:
            raise KemtaAPIError(
                "otp_resend_limited",
                "Trop de codes demandés depuis cette connexion. Réessayez plus tard.",
                http_status=429,
            )


def issue_otp(
    *,
    purpose: str,
    phone: str | None = None,
    email: str | None = None,
    user=None,
    request=None,
) -> tuple[OTPCode, str]:
    """Crée un OTP (haché), invalide le précédent et déclenche l'envoi.

    Retourne `(otp, code_en_clair)` : le code en clair n'est **jamais** persisté ni
    journalisé, il sert uniquement à l'envoi immédiat.
    """
    from apps.users.services import email as email_service
    from apps.users.services import sms

    phone = (phone or "").strip() or None
    email = (email or "").strip().lower() or None
    ip_address = request.META.get("REMOTE_ADDR") if request else None
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:200] if request else ""

    _enforce_send_limits(phone=phone, email=email, purpose=purpose, ip_address=ip_address)

    now = timezone.now()
    filters = _destination_filters(phone, email)

    # Le code précédent (même usage) est invalidé : un seul code valide à la fois.
    OTPCode.objects.filter(
        purpose=purpose, consumed_at__isnull=True, expires_at__gt=now, **filters
    ).update(expires_at=now)

    code = generate_code()
    otp = OTPCode(
        user=user,
        phone=phone or "",
        email=email,
        channel=OTPCode.Channel.SMS if phone else OTPCode.Channel.EMAIL,
        purpose=purpose,
        expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    otp.set_code(code)
    otp.save()

    if phone:
        sms.send_sms(
            phone,
            f"KEMTA : votre code de vérification est {code}. "
            f"Il expire dans {settings.OTP_TTL_SECONDS // 60} minutes. "
            f"Ne le communiquez à personne.",
        )
    elif email:
        email_service.send_email(
            email,
            "KEMTA — vérification de votre adresse email",
            f"Votre code de vérification est {code}. "
            f"Il expire dans {settings.OTP_TTL_SECONDS // 60} minutes. "
            f"Ne le communiquez à personne.",
        )

    log_event(
        "OTP_SENT",
        actor=user,
        entity_type="OTPCode",
        entity_id=otp.pk,
        metadata={"purpose": purpose, "channel": otp.channel},
        request=request,
    )
    return otp, code


def get_active_otp(
    *, purpose: str, phone: str | None = None, email: str | None = None, user=None
) -> OTPCode | None:
    """Renvoie le dernier code utilisable **sans le consommer** (vérification préalable)."""
    phone = (phone or "").strip() or None
    email = (email or "").strip().lower() or None
    filters = _destination_filters(phone, email)
    queryset = OTPCode.objects.filter(purpose=purpose, **filters)
    if user is not None:
        queryset = queryset.filter(user=user)
    otp = queryset.order_by("-created_at").first()
    return otp if otp is not None and otp.is_usable else None


def verify_otp(
    *,
    code: str,
    purpose: str,
    phone: str | None = None,
    email: str | None = None,
    user=None,
    request=None,
) -> OTPCode:
    """Vérifie un code. Lève `KemtaAPIError('otp_invalid')` ou `('otp_max_attempts')`."""
    phone = (phone or "").strip() or None
    email = (email or "").strip().lower() or None
    filters = _destination_filters(phone, email)
    queryset = OTPCode.objects.filter(purpose=purpose, **filters)
    if user is not None:
        queryset = queryset.filter(user=user)

    otp = queryset.order_by("-created_at").first()
    otp_user = user or (otp.user if otp and otp.user_id else None)
    user = otp_user

    if otp is None or not otp.is_usable:
        log_event(
            "OTP_FAILED",
            actor=user,
            entity_type="OTPCode",
            entity_id=otp.pk if otp else None,
            metadata={"purpose": purpose, "reason": "no_active_code"},
            request=request,
        )
        raise KemtaAPIError("otp_invalid", "Code invalide ou expiré.")

    if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        otp.consume()
        raise KemtaAPIError(
            "otp_max_attempts",
            "Nombre de tentatives dépassé. Demandez un nouveau code.",
            http_status=429,
        )

    otp.attempts += 1
    otp.save(update_fields=["attempts"])

    if not otp.verify(code):
        log_event(
            "OTP_FAILED",
            actor=user,
            entity_type="OTPCode",
            entity_id=otp.pk,
            metadata={"purpose": purpose, "attempts": otp.attempts},
            request=request,
        )
        if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
            otp.consume()
            raise KemtaAPIError(
                "otp_max_attempts",
                "Nombre de tentatives dépassé. Demandez un nouveau code.",
                http_status=429,
            )
        raise KemtaAPIError(
            "otp_invalid",
            "Code invalide ou expiré.",
            details={"remaining_attempts": max(settings.OTP_MAX_ATTEMPTS - otp.attempts, 0)},
        )

    otp.consume()
    log_event(
        "OTP_VERIFIED",
        actor=user,
        entity_type="OTPCode",
        entity_id=otp.pk,
        metadata={"purpose": purpose},
        request=request,
    )
    return otp
