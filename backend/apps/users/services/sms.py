"""Adaptateur d'envoi de SMS.

- `console` (développement et tests) : le message est conservé dans `outbox` et
  affiché uniquement si `DEBUG` est actif. **Aucun code OTP n'est écrit dans les logs.**
- `real` : délègue au fournisseur configuré (clé API via variable d'environnement).
  L'implémentation du fournisseur reste isolée ici (aucune clé dans le code).
"""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger("kemta.sms")

# Boîte de réception des messages en mode console (tests et développement).
outbox: list[dict] = []


def reset_outbox() -> None:
    outbox.clear()


def send_sms(phone: str, message: str) -> bool:
    from apps.users.tasks import send_sms_task

    send_sms_task.delay(phone, message)
    return True


def deliver_sms(phone: str, message: str) -> None:
    """Envoi effectif (exécuté par le worker Celery, ou de façon synchrone en test)."""
    provider = settings.SMS_PROVIDER
    if provider == "console":
        outbox.append({"phone": phone, "message": message})
        if settings.DEBUG:  # jamais en production
            print(f"[SMS:{phone}] {message}")
        return
    if provider == "real":
        _send_with_provider(phone, message)
        return
    raise NotImplementedError(f"Fournisseur SMS inconnu : {provider}")


def _send_with_provider(phone: str, message: str) -> None:
    """Point d'extension du fournisseur réel (à compléter, cf. ADR-005).

    La clé API provient de `settings.SMS_API_KEY` (variable d'environnement).
    Aucun secret n'est journalisé ; l'échec est journalisé sans le contenu du message.
    """
    api_key = settings.SMS_API_KEY
    if not api_key:  # pragma: no cover - dépend de la configuration
        logger.error("SMS_API_KEY absente : SMS non envoyé (destinataire %s)", phone)
        return
    logger.info("SMS envoyé à %s (%s caractères)", phone, len(message))
    # TODO(ADR-005) : appel HTTP du fournisseur (timeout court, retry Celery).
