"""Tâches d'entretien transverses (phase 10) : nettoyage des fichiers temporaires et du suivi."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("kemta.tasks")


@shared_task
def purge_old_task_runs(days: int | None = None) -> int:
    """Supprime le suivi des tâches terminées au-delà de la rétention (échecs conservés 4× plus)."""
    from .models import TaskRun

    retention = days if days is not None else settings.TASK_RUN_RETENTION_DAYS
    now = timezone.now()
    ok_deleted, _ = (
        TaskRun.objects.filter(finished_at__lt=now - timedelta(days=retention))
        .exclude(state=TaskRun.State.FAILURE)
        .delete()
    )
    failed_deleted, _ = TaskRun.objects.filter(
        state=TaskRun.State.FAILURE, finished_at__lt=now - timedelta(days=retention * 4)
    ).delete()
    total = ok_deleted + failed_deleted
    logger.info("Suivi des tâches : %s exécution(s) purgée(s)", total)
    return total


@shared_task
def cleanup_temp_files(max_age_hours: int | None = None) -> int:
    """Supprime les fichiers temporaires d'envoi (`MEDIA_ROOT/tmp`) plus vieux que la limite.

    Seuls les fichiers *réguliers* sous `tmp/` sont supprimés — jamais un lien symbolique ni
    un fichier hors de ce dossier : les preuves et justificatifs ne sont jamais concernés.
    """
    hours = max_age_hours if max_age_hours is not None else settings.TEMP_FILE_MAX_AGE_HOURS
    root = Path(settings.MEDIA_ROOT) / "tmp"
    if not root.is_dir():
        return 0
    limit = time.time() - hours * 3600
    removed = 0
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if path.stat().st_mtime < limit:
                path.unlink()
                removed += 1
        except OSError:  # fichier déjà supprimé ou verrouillé : on passera au prochain cycle
            logger.warning("Nettoyage impossible : %s", path.name)
    logger.info("Fichiers temporaires : %s supprimé(s)", removed)
    return removed
