"""Preuves terrain et historique de validation (MVP-007, MVP-008).

Règles structurantes :

* une preuve porte **toujours** un projet, un auteur, un horodatage et un statut ;
* le fichier est validé par son **contenu réel** (magic bytes Pillow), jamais par son nom ;
* `hash_sha256` identifie la pièce : un doublon dans le même projet est refusé (409) ;
* `idempotency_key` rend le renvoi d'un upload interrompu inoffensif (même clé → même preuve) ;
* l'historique de validation (`EvidenceValidation`) est **append-only** : aucune mise à jour ni
  suppression n'est possible, y compris par l'administration.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import SoftDeleteModel, TimeStampedModel


class EvidenceStatus(models.TextChoices):
    PENDING = "PENDING", "En attente de validation"
    VALIDATED = "VALIDATED", "Validée"
    REJECTED = "REJECTED", "Rejetée"
    FLAGGED = "FLAGGED", "Signalée"


class SyncStatus(models.TextChoices):
    """État d'acheminement de la pièce **depuis le terrain**.

    La file locale (IndexedDB) est la source de vérité côté appareil (phase 6) ; le serveur
    ne connaît que ce qu'il a reçu, d'où les états `SYNCED` (reçu) et `CONFLICT` (reçu mais
    rattaché à un doublon).
    """

    PENDING = "PENDING", "En attente d'envoi"
    UPLOADING = "UPLOADING", "Envoi en cours"
    SYNCED = "SYNCED", "Reçue"
    FAILED = "FAILED", "Échec d'envoi"
    CONFLICT = "CONFLICT", "Doublon / conflit"


class GpsStatus(models.TextChoices):
    """Qualité de la localisation : jamais un échec silencieux."""

    AVAILABLE = "AVAILABLE", "Disponible"
    UNAVAILABLE = "UNAVAILABLE", "Indisponible"
    DENIED = "DENIED", "Refusée par l'utilisateur"


class ValidationAction(models.TextChoices):
    VALIDATE = "VALIDATE", "Validation"
    REJECT = "REJECT", "Rejet"
    FLAG = "FLAG", "Signalement"
    REOPEN = "REOPEN", "Réouverture"


# Transitions autorisées : la machine à états est explicite et testée.
ALLOWED_TRANSITIONS: dict[str, dict[str, str]] = {
    EvidenceStatus.PENDING: {
        ValidationAction.VALIDATE: EvidenceStatus.VALIDATED,
        ValidationAction.REJECT: EvidenceStatus.REJECTED,
        ValidationAction.FLAG: EvidenceStatus.FLAGGED,
    },
    EvidenceStatus.VALIDATED: {
        ValidationAction.FLAG: EvidenceStatus.FLAGGED,
        ValidationAction.REJECT: EvidenceStatus.REJECTED,
        ValidationAction.REOPEN: EvidenceStatus.PENDING,
    },
    EvidenceStatus.REJECTED: {
        ValidationAction.REOPEN: EvidenceStatus.PENDING,
        ValidationAction.VALIDATE: EvidenceStatus.VALIDATED,
    },
    EvidenceStatus.FLAGGED: {
        ValidationAction.REOPEN: EvidenceStatus.PENDING,
        ValidationAction.VALIDATE: EvidenceStatus.VALIDATED,
        ValidationAction.REJECT: EvidenceStatus.REJECTED,
    },
}

# Actions exigeant un commentaire (traçabilité du motif).
ACTIONS_REQUIRING_COMMENT = frozenset({ValidationAction.REJECT, ValidationAction.FLAG})


def evidence_upload_path(instance: Evidence, filename: str) -> str:
    """Chemin de stockage régénéré côté serveur : le nom client est ignoré.

    Forme : `evidences/{project_id}/{année}/{mois}/{uuid}.{ext}` — la date utilisée est
    celle de la capture, ce qui garde les archives lisibles par chantier et par mois.
    """
    captured = instance.captured_at
    year = captured.year if captured else 1970
    month = f"{captured.month:02d}" if captured else "00"
    suffix = ""
    if filename and "." in filename:
        candidate = filename.rsplit(".", 1)[1].lower()
        if candidate.isalnum() and len(candidate) <= 5:
            suffix = f".{candidate}"
    return f"evidences/{instance.project_id}/{year}/{month}/{uuid.uuid4().hex}{suffix}"


class Evidence(TimeStampedModel, SoftDeleteModel):
    """Photo de chantier contextualisée (auteur, lieu, horodatage, statut)."""

    project = models.ForeignKey(
        "projects.Project",
        verbose_name="projet",
        on_delete=models.CASCADE,
        related_name="evidences",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="auteur",
        on_delete=models.PROTECT,
        related_name="evidences",
    )
    task = models.ForeignKey(
        "projects.Task",
        verbose_name="tâche",
        on_delete=models.SET_NULL,
        related_name="evidences",
        null=True,
        blank=True,
    )

    file = models.FileField("fichier", upload_to=evidence_upload_path, max_length=255)
    thumbnail = models.ImageField(
        "miniature", upload_to="evidences/thumbnails/", null=True, blank=True, max_length=255
    )
    list_version = models.ImageField(
        "version allégée", upload_to="evidences/lists/", null=True, blank=True, max_length=255
    )
    hash_sha256 = models.CharField("empreinte SHA-256", max_length=64, db_index=True)

    captured_at = models.DateTimeField("horodatage de capture", db_index=True)
    received_at = models.DateTimeField("reçu le", auto_now_add=True)

    latitude = models.DecimalField(
        "latitude", max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        "longitude", max_digits=9, decimal_places=6, null=True, blank=True
    )
    gps_accuracy = models.FloatField("précision GPS (m)", null=True, blank=True)
    gps_status = models.CharField(
        "statut GPS", max_length=16, choices=GpsStatus.choices, default=GpsStatus.UNAVAILABLE
    )

    device_model = models.CharField("appareil", max_length=120, blank=True)
    device_platform = models.CharField("plateforme", max_length=60, blank=True)
    app_version = models.CharField("version de l'application", max_length=32, blank=True)
    description = models.TextField("description", blank=True)

    status = models.CharField(
        "statut", max_length=16, choices=EvidenceStatus.choices, default=EvidenceStatus.PENDING
    )
    sync_status = models.CharField(
        "synchronisation", max_length=16, choices=SyncStatus.choices, default=SyncStatus.SYNCED
    )
    idempotency_key = models.CharField("clé d'idempotence", max_length=64)
    size_bytes = models.PositiveIntegerField("taille (octets)", default=0)
    content_type = models.CharField("type MIME détecté", max_length=32, blank=True)

    class Meta:
        verbose_name = "preuve terrain"
        verbose_name_plural = "preuves terrain"
        ordering = ["-captured_at", "-id"]
        constraints = [
            # Dédoublonnage à l'échelle du projet : une même photo ne compte qu'une fois.
            models.UniqueConstraint(
                fields=["project", "hash_sha256"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_evidence_hash_per_project",
            ),
            # Rejeu d'un upload interrompu : même clé, même auteur → même preuve.
            models.UniqueConstraint(
                fields=["author", "idempotency_key"], name="uniq_evidence_idempotency_key"
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status", "deleted_at"]),
            models.Index(fields=["author", "created_at"]),
            models.Index(fields=["captured_at"]),
        ]

    def __str__(self) -> str:
        return f"Preuve #{self.pk} · {self.project} · {self.get_status_display()}"

    # -- Validation métier --------------------------------------------------
    def clean(self) -> None:
        errors: dict[str, str] = {}

        for field, minimum, maximum in (
            ("latitude", Decimal("-90"), Decimal("90")),
            ("longitude", Decimal("-180"), Decimal("180")),
        ):
            raw = getattr(self, field)
            if raw is None:
                continue
            try:
                value = Decimal(str(raw))
            except (ArithmeticError, ValueError):
                errors[field] = "Coordonnées invalides."
                continue
            setattr(self, field, value)
            if not minimum <= value <= maximum:
                errors[field] = "Coordonnées hors limites."

        # Le statut GPS doit refléter la réalité : des coordonnées ⇒ GPS disponible.
        if self.latitude is not None and self.longitude is not None:
            if self.gps_status != GpsStatus.AVAILABLE:
                self.gps_status = GpsStatus.AVAILABLE
        elif self.gps_status == GpsStatus.AVAILABLE:
            errors["gps_status"] = "Coordonnées absentes : le GPS ne peut pas être « disponible »."

        if self.gps_accuracy is not None and self.gps_accuracy < 0:
            errors["gps_accuracy"] = "La précision GPS ne peut pas être négative."

        # À la création, `received_at` (auto_now_add) n'est pas encore renseigné : on ne
        # compare que lorsqu'il existe réellement.
        if self.captured_at and self.received_at and self.captured_at > self.received_at:
            errors["captured_at"] = "L'horodatage ne peut pas être postérieur à la réception."

        if not self.hash_sha256 or len(self.hash_sha256) != 64:
            errors["hash_sha256"] = "Empreinte SHA-256 invalide."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    # -- Lecture ------------------------------------------------------------
    @property
    def has_derivatives(self) -> bool:
        return bool(self.thumbnail and self.list_version)

    @property
    def is_actionable(self) -> bool:
        """Une preuve en attente attend une décision d'un validateur."""
        return self.status == EvidenceStatus.PENDING


class EvidenceValidation(models.Model):
    """Événement de validation : **immuable** et append-only.

    Aucune modification ni suppression n'est possible via l'ORM (`save` sur une instance
    existante et `delete` lèvent une erreur). L'historique est la seule preuve de qui a
    décidé quoi, quand, et pourquoi.
    """

    evidence = models.ForeignKey(
        Evidence, verbose_name="preuve", on_delete=models.CASCADE, related_name="validations"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="acteur",
        on_delete=models.PROTECT,
        related_name="evidence_validations",
    )
    action = models.CharField("action", max_length=16, choices=ValidationAction.choices)
    from_status = models.CharField("statut avant", max_length=16, choices=EvidenceStatus.choices)
    to_status = models.CharField("statut après", max_length=16, choices=EvidenceStatus.choices)
    comment = models.TextField("commentaire", blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "validation de preuve"
        verbose_name_plural = "validations de preuve"
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["evidence", "created_at"])]

    def __str__(self) -> str:
        return f"{self.get_action_display()} · preuve #{self.evidence_id} · {self.actor_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("L'historique de validation est immuable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("L'historique de validation ne peut pas être supprimé.")

    @staticmethod
    def next_status(current: str, action: str) -> str | None:
        """Statut résultant d'une action, ou `None` si la transition est interdite."""
        return ALLOWED_TRANSITIONS.get(current, {}).get(action)
