"""Organisations : espace de travail racine de tout projet."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.text import slugify

from apps.core.models import SoftDeleteModel, TimeStampedModel
from apps.users.roles import ROLE_CHOICES


class OrganizationType(models.TextChoices):
    PROMOTER = "PROMOTER", "Promoteur immobilier"
    PME = "PME", "PME / entreprise de travaux"
    ENGINEERING_FIRM = "ENGINEERING_FIRM", "Bureau d'études"
    INVESTOR = "INVESTOR", "Investisseur / bailleur"
    PUBLIC = "PUBLIC", "Maître d'ouvrage public"


class Organization(TimeStampedModel, SoftDeleteModel):
    """Organisation (promoteur, PME, bailleur…) — racine de l'arborescence métier."""

    name = models.CharField("nom", max_length=160)
    slug = models.SlugField("identifiant", max_length=180, unique=True)
    type = models.CharField(
        "type", max_length=24, choices=OrganizationType.choices, default=OrganizationType.PROMOTER
    )
    country = models.CharField("pays", max_length=2, default="CM")
    city = models.CharField("ville", max_length=80, blank=True)
    address = models.CharField("adresse", max_length=200, blank=True)
    contact_phone = models.CharField("téléphone de contact", max_length=20, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="propriétaire",
        on_delete=models.PROTECT,
        related_name="owned_organizations",
    )
    is_active = models.BooleanField("active", default=True)

    class Meta:
        verbose_name = "organisation"
        verbose_name_plural = "organisations"
        ordering = ["name"]
        indexes = [models.Index(fields=["owner", "is_active"])]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._build_unique_slug()
        return super().save(*args, **kwargs)

    def _build_unique_slug(self) -> str:
        base = slugify(self.name)[:150] or "organisation"
        candidate = base
        suffix = 1
        while Organization.all_objects.filter(slug=candidate).exclude(pk=self.pk).exists():
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate


class OrganizationMember(TimeStampedModel):
    """Appartenance à une organisation : donne accès aux projets qu'elle contient."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organization_memberships"
    )
    role = models.CharField("rôle", max_length=32, choices=ROLE_CHOICES)
    is_active = models.BooleanField("actif", default=True)

    class Meta:
        verbose_name = "membre d'organisation"
        verbose_name_plural = "membres d'organisation"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"], name="uniq_organization_member"
            )
        ]
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self) -> str:
        return f"{self.user} @ {self.organization} ({self.role})"
