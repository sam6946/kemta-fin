"""Cache ciblé du dashboard (phase 8 / 11).

Principe : on ne met en cache que **la réponse agrégée d'un projet pour un utilisateur**, pour
une courte durée (`DASHBOARD_CACHE_SECONDS`), avec invalidation explicite :

* la clé embarque un **numéro de version du projet**, incrémenté à chaque écriture qui peut
  changer un indicateur (signaux, voir `signals.py`) — pas d'attente d'expiration après une
  modification ;
* la clé embarque l'**utilisateur** (les permissions et la vue diffèrent selon le rôle) et la
  **date locale** (les retards dépendent du jour) ;
* toute panne du cache est ignorée : on calcule sans cache, la réponse reste exacte.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger("kemta.cache")

VERSION_TTL = 7 * 24 * 3600


def _version_key(project_id: int) -> str:
    return f"dashboard:version:{project_id}"


def project_version(project_id: int) -> int:
    try:
        return int(cache.get(_version_key(project_id)) or 1)
    except Exception:
        return 1


def bump_project_version(project_id: int) -> None:
    """Invalide tous les tableaux de bord du projet (toutes les clés changent)."""
    key = _version_key(project_id)
    try:
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 2, VERSION_TTL)
    except Exception:
        logger.warning("Invalidation du cache du dashboard impossible", exc_info=True)


def dashboard_key(project_id: int, user_id: int) -> str:
    today = timezone.localdate().isoformat()
    return f"dashboard:{project_id}:v{project_version(project_id)}:u{user_id}:{today}"


def get_cached(project_id: int, user_id: int):
    if settings.DASHBOARD_CACHE_SECONDS <= 0:
        return None
    try:
        return cache.get(dashboard_key(project_id, user_id))
    except Exception:
        return None


def set_cached(project_id: int, user_id: int, payload: dict) -> None:
    if settings.DASHBOARD_CACHE_SECONDS <= 0:
        return
    try:
        cache.set(dashboard_key(project_id, user_id), payload, settings.DASHBOARD_CACHE_SECONDS)
    except Exception:
        logger.warning("Mise en cache du dashboard impossible", exc_info=True)
