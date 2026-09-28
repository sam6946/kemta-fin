"""Modèles transverses : bases horodatées, suppression logique, journal d'activité."""

from __future__ import annotations

from django.conf import settings
from django.db import IntegrityError, models
from django.db.models import Manager, QuerySet
from django.utils import timezone


class TimeStampedModel(models.Model):
    """`created_at` / `updated_at` automatiques."""

    created_at = models.DateTimeField("créé le", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteQuerySet(QuerySet):
    def delete(self):
        # La suppression en masse est logique (jamais physique).
        return self.update(deleted_at=timezone.now())

    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        return self.filter(deleted_at__isnull=False)


class SoftDeleteManager(Manager.from_queryset(SoftDeleteQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class SoftDeleteModel(models.Model):
    """Suppression logique : les lignes restent en base, exclues des vues normales."""

    deleted_at = models.DateTimeField("supprimé le", null=True, blank=True, db_index=True)

    objects = SoftDeleteManager()
    all_objects = Manager.from_queryset(SoftDeleteQuerySet)()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])

    def hard_delete(self, using=None, keep_parents=False):
        return super().delete(using=using, keep_parents=keep_parents)

    def restore(self):
        self.deleted_at = None
        self.save(update_fields=["deleted_at"])


class ActivityLogQuerySet(QuerySet):
    """Queryset en lecture seule : le journal est immuable."""

    def delete(self):
        raise IntegrityError("Les événements du journal ne peuvent pas être supprimés.")

    def update(self, **kwargs):
        raise IntegrityError("Les événements du journal ne peuvent pas être modifiés.")


class ActivityLogManager(Manager.from_queryset(ActivityLogQuerySet)):
    pass


class ActivityLog(models.Model):
    """Journal d'activité — **immuable**.

    Aucune suppression physique, aucune modification : c'est la trace de référence
    des actions sensibles (authentification, rôles, preuves, finances).
    """

    class Action(models.TextChoices):
        USER_REGISTERED = "USER_REGISTERED", "Utilisateur inscrit"
        OTP_SENT = "OTP_SENT", "OTP envoyé"
        OTP_VERIFIED = "OTP_VERIFIED", "OTP vérifié"
        OTP_FAILED = "OTP_FAILED", "OTP refusé"
        OTP_RESEND = "OTP_RESEND", "OTP renvoyé"
        LOGIN_SUCCESS = "LOGIN_SUCCESS", "Connexion réussie"
        LOGIN_FAILED = "LOGIN_FAILED", "Connexion échouée"
        ACCOUNT_LOCKED = "ACCOUNT_LOCKED", "Compte verrouillé"
        LOGOUT = "LOGOUT", "Déconnexion"
        TOKEN_REFRESHED = "TOKEN_REFRESHED", "Jeton rafraîchi"
        TOKEN_REFRESH_REJECTED = "TOKEN_REFRESH_REJECTED", "Rafraîchissement refusé"
        PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED", "Réinitialisation demandée"
        PASSWORD_RESET_FAILED = "PASSWORD_RESET_FAILED", "Réinitialisation échouée"
        PASSWORD_RESET_CONFIRMED = "PASSWORD_RESET_CONFIRMED", "Mot de passe réinitialisé"
        PASSWORD_RESET_DENIED = "PASSWORD_RESET_DENIED", "Réinitialisation refusée"
        PASSWORD_CHANGED = "PASSWORD_CHANGED", "Mot de passe modifié"
        EMAIL_ADDED = "EMAIL_ADDED", "Email ajouté"
        EMAIL_VERIFIED = "EMAIL_VERIFIED", "Email vérifié"
        # Organisations, projets et membres (phase 3)
        ORG_CREATED = "ORG_CREATED", "Organisation créée"
        ORG_UPDATED = "ORG_UPDATED", "Organisation modifiée"
        PROJECT_CREATED = "PROJECT_CREATED", "Projet créé"
        PROJECT_UPDATED = "PROJECT_UPDATED", "Projet modifié"
        PROJECT_ARCHIVED = "PROJECT_ARCHIVED", "Projet archivé"
        MEMBER_ADDED = "MEMBER_ADDED", "Membre ajouté"
        MEMBER_ROLE_CHANGED = "MEMBER_ROLE_CHANGED", "Rôle modifié"
        MEMBER_REMOVED = "MEMBER_REMOVED", "Membre retiré"
        # Phase 4 — planification.
        MILESTONE_CREATED = "MILESTONE_CREATED", "Jalon créé"
        MILESTONE_UPDATED = "MILESTONE_UPDATED", "Jalon modifié"
        MILESTONE_DELETED = "MILESTONE_DELETED", "Jalon supprimé"
        TASK_CREATED = "TASK_CREATED", "Tâche créée"
        TASK_UPDATED = "TASK_UPDATED", "Tâche modifiée"
        TASK_STATUS_CHANGED = "TASK_STATUS_CHANGED", "Statut de tâche modifié"
        TASK_DELETED = "TASK_DELETED", "Tâche supprimée"
        # Phase 5 — preuves terrain.
        EVIDENCE_CAPTURED = "EVIDENCE_CAPTURED", "Preuve capturée"
        EVIDENCE_VALIDATED = "EVIDENCE_VALIDATED", "Preuve validée"
        EVIDENCE_REJECTED = "EVIDENCE_REJECTED", "Preuve rejetée"
        EVIDENCE_FLAGGED = "EVIDENCE_FLAGGED", "Preuve signalée"
        EVIDENCE_REOPENED = "EVIDENCE_REOPENED", "Preuve rouverte"
        # Phase 7 — finances.
        BUDGET_LINE_CREATED = "BUDGET_LINE_CREATED", "Poste budgétaire créé"
        BUDGET_LINE_UPDATED = "BUDGET_LINE_UPDATED", "Poste budgétaire modifié"
        BUDGET_LINE_DELETED = "BUDGET_LINE_DELETED", "Poste budgétaire supprimé"
        EXPENSE_CREATED = "EXPENSE_CREATED", "Dépense créée"
        EXPENSE_UPDATED = "EXPENSE_UPDATED", "Dépense modifiée"
        EXPENSE_SUBMITTED = "EXPENSE_SUBMITTED", "Dépense soumise"
        EXPENSE_APPROVED = "EXPENSE_APPROVED", "Dépense approuvée"
        EXPENSE_REJECTED = "EXPENSE_REJECTED", "Dépense rejetée"
        EXPENSE_CANCELLED = "EXPENSE_CANCELLED", "Dépense annulée"
        EXPENSE_RECEIPT_ATTACHED = "EXPENSE_RECEIPT_ATTACHED", "Justificatif attaché"
        PAYMENT_RECORDED = "PAYMENT_RECORDED", "Paiement enregistré"
        PAYMENT_CANCELLED = "PAYMENT_CANCELLED", "Paiement annulé"
        ADJUSTMENT_RECORDED = "ADJUSTMENT_RECORDED", "Ajustement financier"
        BUDGET_THRESHOLD_REACHED = "BUDGET_THRESHOLD_REACHED", "Seuil budgétaire atteint"
        BUDGET_EXCEEDED = "BUDGET_EXCEEDED", "Budget dépassé"

    id = models.BigAutoField(primary_key=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="acteur",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="activity_events",
    )
    action = models.CharField("action", max_length=48, choices=Action.choices, db_index=True)
    entity_type = models.CharField("type d'entité", max_length=64, blank=True, db_index=True)
    entity_id = models.CharField("identifiant d'entité", max_length=64, blank=True, db_index=True)
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="projet",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="activity_events",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        verbose_name="organisation",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="activity_events",
    )
    metadata = models.JSONField("métadonnées", default=dict, blank=True)
    ip_address = models.GenericIPAddressField("adresse IP", null=True, blank=True)
    user_agent = models.CharField("agent utilisateur", max_length=200, blank=True)
    created_at = models.DateTimeField("date", auto_now_add=True, db_index=True)

    objects = ActivityLogManager()

    class Meta:
        verbose_name = "journal d'activité"
        verbose_name_plural = "journal d'activité"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["action", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} @ {self.created_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise IntegrityError("Un événement du journal n'est pas modifiable.")
        return super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        raise IntegrityError("Un événement du journal ne peut pas être supprimé.")
