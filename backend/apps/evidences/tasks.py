"""Tâches asynchrones des preuves terrain.

La génération des dérivées (miniature + version liste) ne bloque **jamais** la requête
d'upload : le chantier envoie sa photo en 3G, la réponse doit partir tout de suite. Si la
tâche échoue, la preuve reste utilisable (l'original est conservé) et une relance est
possible via `regenerate_derivatives`.
"""

from __future__ import annotations

import logging

from celery import shared_task

from apps.evidences.models import Evidence
from apps.evidences.storage import build_derivatives

logger = logging.getLogger("kemta.tasks")


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_evidence_derivatives(self, evidence_id: int) -> bool:
    """Génère la miniature et la version liste d'une preuve déjà enregistrée."""
    try:
        evidence = Evidence.objects.get(pk=evidence_id)
    except Evidence.DoesNotExist:
        logger.warning("Preuve %s introuvable : dérivées ignorées", evidence_id)
        return False

    try:
        with evidence.file.open("rb") as handle:
            payload = handle.read()
        derivatives = build_derivatives(payload)
        evidence.thumbnail.save(derivatives["thumbnail"].name, derivatives["thumbnail"], save=False)
        evidence.list_version.save(
            derivatives["list_version"].name, derivatives["list_version"], save=False
        )
        evidence.save(update_fields=["thumbnail", "list_version", "updated_at"])
    except Exception as exc:  # pragma: no cover - dépend du stockage/disque
        logger.warning("Échec de génération des dérivées de la preuve %s : %s", evidence_id, exc)
        raise self.retry(exc=exc) from exc

    logger.info("Dérivées générées pour la preuve %s", evidence_id)
    return True
