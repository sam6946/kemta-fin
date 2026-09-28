"""Notifications in-app (MVP-014, phase 10).

Regroupement : tant qu'une notification d'un même groupe (`group_key`) n'est **pas lue**, les
nouveaux événements similaires l'incrémentent au lieu d'en créer une autre (« 3 preuves
rejetées » plutôt que trois lignes). Une contrainte d'unicité partielle garantit cette
règle même sous concurrence : au plus une notification *ouverte* par utilisateur et groupe.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import TimeStampedModel


class Notification(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="destinataire",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="projet",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    event_type = models.CharField("événement", max_length=48, db_index=True)
    group_key = models.CharField("clé de regroupement", max_length=120)
    dedupe_key = models.CharField(
        "clé d'unicité",
        max_length=160,
        blank=True,
        help_text="Un même événement ne notifie qu'une fois.",
    )
    title = models.CharField("titre", max_length=200)
    body = models.CharField("détail", max_length=500, blank=True)
    count = models.PositiveIntegerField("événements regroupés", default=1)
    data = models.JSONField("données", default=dict, blank=True)
    last_event_at = models.DateTimeField("dernier événement", default=timezone.now)
    read_at = models.DateTimeField("lue le", null=True, blank=True)

    class Meta:
        verbose_name = "notification"
        verbose_name_plural = "notifications"
        ordering = ["-last_event_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "group_key"],
                condition=Q(read_at__isnull=True),
                name="uniq_open_notification_per_group",
            ),
            models.UniqueConstraint(
                fields=["user", "dedupe_key"],
                condition=~Q(dedupe_key=""),
                name="uniq_notification_dedupe_key",
            ),
        ]
        indexes = [models.Index(fields=["user", "read_at", "-last_event_at"])]

    def __str__(self) -> str:
        return f"{self.event_type} → {self.user_id} (x{self.count})"

    @property
    def is_read(self) -> bool:
        return self.read_at is not None
