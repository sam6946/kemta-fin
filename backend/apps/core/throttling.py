"""Rate limiting général résilient (phase 11).

Les limites *générales* (par IP, par utilisateur, par portée d'envoi) protègent contre
l'abus ; elles ne doivent pas devenir un point de défaillance : si le cache (Redis) est
indisponible, la requête passe (**fail-open**) et l'incident est journalisé.

Les limites des endpoints d'authentification (OTP, connexion, réinitialisation — voir
`apps/users/throttling.py`) restent, elles, strictes : mieux vaut refuser une connexion que de
laisser deviner un code OTP sans frein.
"""

from __future__ import annotations

import logging

from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle

logger = logging.getLogger("kemta.throttle")


class FailOpenMixin:
    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            logger.warning("Rate limiting indisponible (cache) : requête autorisée", exc_info=True)
            return True


class KemtaAnonThrottle(FailOpenMixin, AnonRateThrottle):
    pass


class KemtaUserThrottle(FailOpenMixin, UserRateThrottle):
    pass


class KemtaScopedThrottle(FailOpenMixin, ScopedRateThrottle):
    pass
