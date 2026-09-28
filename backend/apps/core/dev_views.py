"""Outils de développement — **jamais disponibles en production**.

Le fournisseur SMS de développement est un adaptateur « console » : les messages restent
dans le processus serveur. Exposer cette boîte de réception permet de tester le parcours
OTP/réinitialisation de bout en bout (démo, poste de développement, CI manuelle).

Garde-fous :
- désactivé par défaut dès que `DJANGO_ENV=production` ou `DEBUG=false` ;
- réponse `404` (et non `403`) quand c'est désactivé : l'existence de l'endpoint n'est pas révélée ;
- aucun secret technique n'est exposé (uniquement les messages SMS/email en attente).
"""

from __future__ import annotations

from django.conf import settings
from django.http import Http404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

MAX_ITEMS = 20


def dev_tools_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_DEV_OUTBOX", False))


class DevOutboxView(APIView):
    """`GET /api/dev/outbox/` — derniers messages SMS/email émis en développement."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request):
        if not dev_tools_enabled():
            raise Http404("Ressource indisponible.")

        from apps.users.services import email as email_service
        from apps.users.services import sms

        return Response(
            {
                "notice": "Outils de développement — désactivés en production.",
                "sms": sms.outbox[-MAX_ITEMS:],
                "emails": email_service.outbox[-MAX_ITEMS:],
            }
        )
