"""Adaptateur d'envoi d'email (vérification de l'adresse ajoutée depuis le dashboard)."""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger("kemta.email")

# Boîte de réception des messages en développement et en test.
outbox: list[dict] = []


def reset_outbox() -> None:
    outbox.clear()


def send_email(to: str, subject: str, message: str) -> bool:
    from apps.users.tasks import send_email_task

    send_email_task.delay(to, subject, message)
    return True


def deliver_email(to: str, subject: str, message: str) -> None:
    """Envoi effectif (worker Celery, ou synchrone en test)."""
    if settings.EMAIL_BACKEND_PROVIDER == "console":
        outbox.append({"to": to, "subject": subject, "message": message})
        if settings.DEBUG:
            print(f"[EMAIL:{to}] {subject} — {message}")
        return
    from django.core.mail import send_mail

    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [to], fail_silently=False)
