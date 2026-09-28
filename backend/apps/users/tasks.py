"""Tâches asynchrones (Celery) du domaine utilisateurs."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("kemta.tasks")


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_sms_task(self, phone: str, message: str) -> None:
    """Envoi d'un SMS hors du cycle de requête HTTP, avec retry."""
    from apps.users.services import sms

    try:
        sms.deliver_sms(phone, message)
    except Exception as exc:
        logger.warning("Échec d'envoi SMS (tentative %s)", self.request.retries)
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_task(self, to: str, subject: str, message: str) -> None:
    """Envoi d'un email hors du cycle de requête HTTP, avec retry."""
    from apps.users.services import email

    try:
        email.deliver_email(to, subject, message)
    except Exception as exc:
        logger.warning("Échec d'envoi email (tentative %s)", self.request.retries)
        raise self.retry(exc=exc) from exc


@shared_task
def purge_expired_otps() -> int:
    """Purge les codes OTP consommés ou expirés depuis plus de 24 h."""
    from apps.users.models import OTPCode

    threshold = timezone.now() - timedelta(hours=24)
    deleted, _ = OTPCode.objects.filter(created_at__lt=threshold).delete()
    logger.info("Purge OTP : %s code(s) supprimé(s)", deleted)
    return deleted
