"""Projets et membres de projet : périmètre d'accès et de contrôle des données."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import SoftDeleteModel, TimeStampedModel
from apps.users.roles import ROLE_CHOICES


class ProjectStatus(models.TextChoices):
    DRAFT = "DRAFT", "Brouillon"
    ACTIVE = "ACTIVE", "En cours"
    ON_HOLD = "ON_HOLD", "Suspendu"
    COMPLETED = "COMPLETED", "Terminé"
    ARCHIVED = "ARCHIVED", "Archivé"


class Currency(models.TextChoices):
    XAF = "XAF", "Franc CFA (FCFA)"


# Montant FCFA : entier, jamais de centimes (voir docs/data-model.md, conventions).
MAX_AMOUNT = Decimal("999999999999999")


class Project(TimeStampedModel, SoftDeleteModel):
    """Projet de chantier. **Toujours rattaché à une organisation.**"""

    organization = models.ForeignKey(
        "organizations.Organization",
        verbose_name="organisation",
        on_delete=models.PROTECT,
        related_name="projects",
    )
    name = models.CharField("nom", max_length=180)
    code = models.CharField("code", max_length=32, blank=True)
    description = models.TextField("description", blank=True)
    location_label = models.CharField("localisation", max_length=200, blank=True)
    city = models.CharField("ville", max_length=80, blank=True)
    region = models.CharField("région", max_length=80, blank=True)
    latitude = models.DecimalField(
        "latitude", max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        "longitude", max_digits=9, decimal_places=6, null=True, blank=True
    )
    geofence_radius_m = models.PositiveIntegerField("rayon de périmètre (m)", default=500)

    currency = models.CharField(
        "devise", max_length=3, choices=Currency.choices, default=Currency.XAF
    )
    budget_total = models.DecimalField(
        "budget prévu (FCFA)", max_digits=15, decimal_places=0, default=0
    )
    status = models.CharField(
        "statut", max_length=16, choices=ProjectStatus.choices, default=ProjectStatus.DRAFT
    )
    # Avancement calculé **côté serveur** (jalons/tâches à partir de la phase 4).
    progress = models.DecimalField(
        "avancement (%)", max_digits=5, decimal_places=2, default=Decimal("0")
    )

    planned_start_date = models.DateField("début prévu", null=True, blank=True)
    planned_end_date = models.DateField("fin prévue", null=True, blank=True)
    actual_start_date = models.DateField("début réel", null=True, blank=True)
    actual_end_date = models.DateField("fin réelle", null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="créé par",
        on_delete=models.PROTECT,
        related_name="created_projects",
    )

    class Meta:
        verbose_name = "projet"
        verbose_name_plural = "projets"
        ordering = ["-created_at"]
        constraints = [
            # Le code est unique au sein d'une organisation (parmi les projets non supprimés).
            models.UniqueConstraint(
                fields=["organization", "code"],
                condition=Q(deleted_at__isnull=True) & ~Q(code=""),
                name="uniq_project_code_per_organization",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["status", "deleted_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code or 'sans code'})"

    # -- Validation métier --------------------------------------------------
    def clean(self) -> None:
        errors: dict[str, str] = {}
        if (
            self.planned_start_date
            and self.planned_end_date
            and self.planned_start_date > self.planned_end_date
        ):
            errors["planned_end_date"] = "La fin prévue doit suivre le début prévu."
        if (
            self.actual_start_date
            and self.actual_end_date
            and self.actual_start_date > self.actual_end_date
        ):
            errors["actual_end_date"] = "La fin réelle doit suivre le début réel."
        if self.budget_total is not None:
            # La valeur peut arriver en `int`, `str` ou `Decimal` selon le point d'entrée.
            try:
                amount = Decimal(str(self.budget_total))
            except (ArithmeticError, ValueError):
                errors["budget_total"] = "Montant invalide."
                amount = None
            if amount is not None:
                self.budget_total = amount
                if amount < 0:
                    errors["budget_total"] = "Le budget ne peut pas être négatif."
                elif amount != amount.to_integral_value():
                    errors["budget_total"] = (
                        "Les montants sont exprimés en FCFA entiers (sans centimes)."
                    )
                elif amount > MAX_AMOUNT:
                    errors["budget_total"] = "Montant hors limites."
        for field, minimum, maximum in (
            ("latitude", Decimal("-90"), Decimal("90")),
            ("longitude", Decimal("-180"), Decimal("180")),
        ):
            coordinate = getattr(self, field)
            if coordinate is None:
                continue
            try:
                value = Decimal(str(coordinate))
            except (ArithmeticError, ValueError):
                errors[field] = "Coordonnées invalides."
                continue
            setattr(self, field, value)
            if not minimum <= value <= maximum:
                errors[field] = "Coordonnées hors limites."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.currency = (self.currency or Currency.XAF).upper()
        self.clean()
        return super().save(*args, **kwargs)


class ProjectMember(TimeStampedModel):
    """Accès d'un utilisateur à un projet : rôle projet + capacités fines.

    Le rôle du membre est **prioritaire** sur le rôle global pour tout ce qui concerne
    ce projet (voir `docs/rbac-matrix.md` §1).
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="project_memberships"
    )
    role = models.CharField("rôle sur le projet", max_length=32, choices=ROLE_CHOICES)
    can_validate_evidence = models.BooleanField("peut valider les preuves", default=False)
    can_manage_finance = models.BooleanField("peut gérer les finances", default=False)
    is_active = models.BooleanField("actif", default=True)

    class Meta:
        verbose_name = "membre de projet"
        verbose_name_plural = "membres de projet"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "user"], name="uniq_project_member")
        ]
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self) -> str:
        return f"{self.user} @ {self.project} ({self.role})"


# ---------------------------------------------------------------------------
# Phase 4 — jalons et tâches
# ---------------------------------------------------------------------------
class MilestoneStatus(models.TextChoices):
    PLANNED = "PLANNED", "Planifié"
    IN_PROGRESS = "IN_PROGRESS", "En cours"
    DONE = "DONE", "Terminé"
    BLOCKED = "BLOCKED", "Bloqué"
    CANCELLED = "CANCELLED", "Annulé"


class TaskStatus(models.TextChoices):
    TODO = "TODO", "À faire"
    IN_PROGRESS = "IN_PROGRESS", "En cours"
    DONE = "DONE", "Terminée"
    BLOCKED = "BLOCKED", "Bloquée"
    CANCELLED = "CANCELLED", "Annulée"


def _as_date(value):
    """Normalise une date potentiellement encore au format `str` (instance non rechargée)."""
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# Statuts terminaux : ni retard possible, ni date réelle à saisir avant.
FINAL_STATUSES = frozenset({MilestoneStatus.DONE, MilestoneStatus.CANCELLED})
FINAL_TASK_STATUSES = frozenset({TaskStatus.DONE, TaskStatus.CANCELLED})
# Statuts « ouverts » : une tâche qui n'est ni terminée ni annulée peut être en retard.
OPEN_TASK_STATUSES = tuple(
    status for status in TaskStatus.values if status not in FINAL_TASK_STATUSES
)


def _validate_progress(value, *, field: str, errors: dict[str, str]) -> Decimal | None:
    """Avancement : pourcentage entre 0 et 100, arrondi à deux décimales.

    Les valeurs décimales sont acceptées (12,5 % est un avancement légitime) mais
    bornées : ni négatif, ni au-delà de 100 %.
    """
    if value is None:
        return None
    try:
        progress = Decimal(str(value)).quantize(Decimal("0.01"))
    except (ArithmeticError, ValueError):
        errors[field] = "Avancement invalide."
        return None
    if progress < 0 or progress > 100:
        errors[field] = "L'avancement doit être compris entre 0 et 100."
        return None
    return progress


def _validate_dates(
    start, end, *, start_field: str, end_field: str, errors: dict[str, str]
) -> None:
    if start and end and start > end:
        errors[end_field] = "La fin doit suivre le début."


class Milestone(TimeStampedModel, SoftDeleteModel):
    """Jalon du projet : étape datée qui porte la pondération de l'avancement."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="milestones")
    title = models.CharField("titre", max_length=180)
    description = models.TextField("description", blank=True)
    status = models.CharField(
        "statut", max_length=16, choices=MilestoneStatus.choices, default=MilestoneStatus.PLANNED
    )
    planned_date = models.DateField("date prévue", null=True, blank=True)
    actual_date = models.DateField("date réelle", null=True, blank=True)
    order = models.PositiveIntegerField("ordre", default=0)
    # Pondération dans le calcul d'avancement du projet (1 = poids neutre).
    weight = models.DecimalField("poids", max_digits=6, decimal_places=2, default=Decimal("1"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="créé par",
        on_delete=models.PROTECT,
        related_name="created_milestones",
    )

    class Meta:
        verbose_name = "jalon"
        verbose_name_plural = "jalons"
        ordering = ["order", "planned_date", "created_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "deleted_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_status_display()})"

    # -- Validation métier --------------------------------------------------
    def clean(self) -> None:
        errors: dict[str, str] = {}
        try:
            weight = Decimal(str(self.weight))
        except (ArithmeticError, ValueError):
            errors["weight"] = "Poids invalide."
        else:
            self.weight = weight
            if weight <= 0:
                errors["weight"] = "Le poids doit être strictement positif."
        if self.actual_date and self.status not in FINAL_STATUSES:
            errors["actual_date"] = "La date réelle suppose un jalon terminé ou annulé."
        if self.status == MilestoneStatus.DONE and not self.actual_date:
            errors["actual_date"] = "Renseignez la date réelle d'un jalon terminé."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    # -- Lecture ------------------------------------------------------------
    @property
    def is_late(self) -> bool:
        """Jalon en retard : date prévue dépassée et jalon non terminal."""
        planned = _as_date(self.planned_date)
        return bool(
            planned and self.status not in FINAL_STATUSES and planned < timezone.localdate()
        )

    @property
    def days_late(self) -> int:
        planned = _as_date(self.planned_date)
        if not self.is_late or planned is None:
            return 0
        return (timezone.localdate() - planned).days


class Task(TimeStampedModel, SoftDeleteModel):
    """Tâche de chantier, éventuellement rattachée à un jalon et à un responsable."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    milestone = models.ForeignKey(
        Milestone,
        verbose_name="jalon",
        on_delete=models.SET_NULL,
        related_name="tasks",
        null=True,
        blank=True,
    )
    title = models.CharField("titre", max_length=180)
    description = models.TextField("description", blank=True)
    status = models.CharField(
        "statut", max_length=16, choices=TaskStatus.choices, default=TaskStatus.TODO
    )
    planned_start_date = models.DateField("début prévu", null=True, blank=True)
    planned_end_date = models.DateField("fin prévue", null=True, blank=True)
    actual_start_date = models.DateField("début réel", null=True, blank=True)
    actual_end_date = models.DateField("fin réelle", null=True, blank=True)
    progress = models.DecimalField(
        "avancement (%)", max_digits=5, decimal_places=2, default=Decimal("0")
    )
    weight = models.DecimalField("poids", max_digits=6, decimal_places=2, default=Decimal("1"))
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="responsable",
        on_delete=models.SET_NULL,
        related_name="assigned_tasks",
        null=True,
        blank=True,
    )
    depends_on = models.ManyToManyField(
        "self", verbose_name="dépend de", symmetrical=False, related_name="blocking", blank=True
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="créé par",
        on_delete=models.PROTECT,
        related_name="created_tasks",
    )

    class Meta:
        verbose_name = "tâche"
        verbose_name_plural = "tâches"
        ordering = ["planned_start_date", "created_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "deleted_at"]),
            models.Index(fields=["assignee", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_status_display()})"

    # -- Validation métier --------------------------------------------------
    def clean(self) -> None:
        errors: dict[str, str] = {}
        _validate_dates(
            self.planned_start_date,
            self.planned_end_date,
            start_field="planned_start_date",
            end_field="planned_end_date",
            errors=errors,
        )
        _validate_dates(
            self.actual_start_date,
            self.actual_end_date,
            start_field="actual_start_date",
            end_field="actual_end_date",
            errors=errors,
        )
        normalized_progress = _validate_progress(self.progress, field="progress", errors=errors)
        if normalized_progress is not None:
            self.progress = normalized_progress
        try:
            weight = Decimal(str(self.weight))
        except (ArithmeticError, ValueError):
            errors["weight"] = "Poids invalide."
        else:
            self.weight = weight
            if weight <= 0:
                errors["weight"] = "Le poids doit être strictement positif."

        # Cohérence statut / dates réelles / avancement (règles du data-model).
        if self.status == TaskStatus.DONE:
            if not self.actual_end_date:
                errors["actual_end_date"] = "Renseignez la fin réelle d'une tâche terminée."
            self.progress = Decimal("100")
        elif self.actual_end_date:
            errors["actual_end_date"] = "Retirez la fin réelle ou terminez la tâche."
        if self.status == TaskStatus.TODO and self.actual_start_date:
            errors["actual_start_date"] = "Une tâche « à faire » ne peut pas avoir de début réel."
        if self.status in FINAL_TASK_STATUSES and self.progress != Decimal("100"):
            self.progress = Decimal("0") if self.status == TaskStatus.CANCELLED else self.progress
        if self.milestone is not None and self.milestone.project_id != self.project_id:
            errors["milestone"] = "Le jalon doit appartenir au même projet."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.progress is not None:
            self.progress = Decimal(str(self.progress))
        self.clean()
        return super().save(*args, **kwargs)

    # -- Lecture ------------------------------------------------------------
    @property
    def is_late(self) -> bool:
        """Tâche en retard : fin prévue dépassée et tâche non terminale."""
        planned = _as_date(self.planned_end_date)
        return bool(
            planned and self.status not in FINAL_TASK_STATUSES and planned < timezone.localdate()
        )

    @property
    def days_late(self) -> int:
        planned = _as_date(self.planned_end_date)
        if not self.is_late or planned is None:
            return 0
        return (timezone.localdate() - planned).days
