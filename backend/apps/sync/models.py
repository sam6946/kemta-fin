"""Registre d'idempotence des opérations de synchronisation (MVP-009).

Principe : **une clé d'idempotence = un seul effet**. Quand un appareil rejoue une opération
après une coupure réseau (le serveur a peut-être appliqué l'opération avant de perdre la
connexion), le registre renvoie le résultat déjà produit au lieu de réexécuter quoi que ce soit.

Deux états seulement :

* `IN_PROGRESS` — l'opération est en cours d'application. Un second envoi avec la même clé est
  refusé (`409 op_in_progress`) : le client réessaiera plus tard, sans risque de doublon ;
* `DONE` — l'opération a été appliquée ; la réponse d'origine (`http_status`, `response_body`)
  est rejouée telle quelle, marquée `Idempotency-Replayed: true` dans le lot.

Le registre ne contient **aucune donnée métier** : juste de quoi identifier l'entité touchée et
rejouer la réponse. Il est purgé par l'exploitation (voir `docs/offline-sync.md`).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class SyncOperationStatus(models.TextChoices):
    IN_PROGRESS = "IN_PROGRESS", "En cours"
    DONE = "DONE", "Appliquée"


class SyncOperation(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="utilisateur",
        on_delete=models.CASCADE,
        related_name="sync_operations",
    )
    idempotency_key = models.CharField("clé d'idempotence", max_length=64)
    operation_type = models.CharField("type d'opération", max_length=32)
    status = models.CharField(
        "état",
        max_length=16,
        choices=SyncOperationStatus.choices,
        default=SyncOperationStatus.IN_PROGRESS,
    )
    http_status = models.PositiveSmallIntegerField("statut HTTP d'origine", null=True, blank=True)
    entity_type = models.CharField("type d'entité", max_length=32, blank=True)
    entity_id = models.BigIntegerField("identifiant d'entité", null=True, blank=True)
    response_body = models.JSONField("réponse d'origine", default=dict, blank=True)

    class Meta:
        verbose_name = "opération de synchronisation"
        verbose_name_plural = "opérations de synchronisation"
        ordering = ["-created_at", "-id"]
        constraints = [
            # La clé n'a de sens qu'une fois par utilisateur : c'est elle qui garantit
            # qu'un renvoi ne crée pas un second effet.
            models.UniqueConstraint(
                fields=["user", "idempotency_key"], name="uniq_sync_operation_per_user"
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["operation_type", "created_at"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - confort d'administration
        return f"{self.operation_type} · {self.idempotency_key[:8]}… ({self.status})"
