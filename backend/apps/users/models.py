"""Utilisateurs (identifiant = téléphone) et codes OTP (jamais en clair)."""

from __future__ import annotations

import secrets
from datetime import timedelta
from hashlib import sha256

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import SoftDeleteModel
from apps.users.roles import ROLE_CHOICES, default_role


class UserManager(BaseUserManager):
    """Manager excluant par défaut les utilisateurs supprimés logiquement."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)

    def _create_user(self, phone, password=None, **extra_fields):
        if not phone:
            raise ValueError("Le numéro de téléphone est obligatoire.")
        user = self.model(phone=phone, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(phone, password, **extra_fields)

    def create_superuser(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_phone_verified", True)
        return self._create_user(phone, password, **extra_fields)


class User(SoftDeleteModel, AbstractUser):
    """Identifiant principal : le **numéro de téléphone** (E.164). L'email est facultatif."""

    username = None

    phone = models.CharField("téléphone", max_length=20, unique=True, db_index=True)
    email = models.EmailField("email", blank=True, null=True, unique=True)
    email_verified_at = models.DateTimeField("email vérifié le", null=True, blank=True)
    is_phone_verified = models.BooleanField("téléphone vérifié", default=False)

    role = models.CharField("rôle", max_length=32, choices=ROLE_CHOICES, default=default_role)
    language = models.CharField("langue", max_length=5, default="fr")

    password_changed_at = models.DateTimeField(
        "mot de passe modifié le", default=timezone.now, db_index=True
    )
    failed_login_count = models.PositiveSmallIntegerField("échecs de connexion", default=0)
    locked_until = models.DateTimeField("verrouillé jusqu'au", null=True, blank=True)
    last_login_ip = models.GenericIPAddressField("dernière IP", null=True, blank=True)

    objects = UserManager()
    all_objects = BaseUserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = "utilisateur"
        verbose_name_plural = "utilisateurs"
        constraints = [
            # Le numéro et l'email restent uniques ; la suppression logique libère
            # les identifiants (voir `User.delete`) pour permettre une réinscription.
            models.UniqueConstraint(
                fields=["email"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_user_email_active",
            ),
        ]
        indexes = [
            models.Index(fields=["role"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_full_name() or self.phone} ({self.phone})"

    def save(self, *args, **kwargs):
        self.phone = (self.phone or "").strip()
        if self.email == "":
            self.email = None
        return super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        """Suppression logique : les identifiants sont libérés (marqués) pour permettre
        une éventuelle réinscription, sans effacer l'historique critique."""
        self.deleted_at = timezone.now()
        self.is_active = False
        self.phone = f"{self.phone}#deleted#{secrets.token_hex(4)}"
        self.email = None
        self.save()

    # -- Verrouillage anti brute-force -------------------------------------
    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())

    def register_login_failure(self) -> None:
        self.failed_login_count = models.F("failed_login_count") + 1
        self.save(update_fields=["failed_login_count"])
        self.refresh_from_db(fields=["failed_login_count"])
        if self.failed_login_count >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
            self.locked_until = timezone.now() + timedelta(seconds=settings.LOGIN_LOCK_SECONDS)
            self.failed_login_count = 0
            self.save(update_fields=["locked_until", "failed_login_count"])
            return True
        return False

    def register_login_success(self) -> None:
        self.failed_login_count = 0
        self.locked_until = None
        self.save(update_fields=["failed_login_count", "locked_until"])

    def touch_password_changed(self) -> None:
        self.password_changed_at = timezone.now()
        self.save(update_fields=["password_changed_at"])


class OTPCode(models.Model):
    """Code à usage unique. **Le code en clair n'est jamais stocké.**"""

    class Purpose(models.TextChoices):
        SIGNUP = "SIGNUP", "Activation du compte"
        PASSWORD_RESET = "PASSWORD_RESET", "Réinitialisation du mot de passe"
        EMAIL_VERIFY = "EMAIL_VERIFY", "Vérification de l'email"

    class Channel(models.TextChoices):
        SMS = "SMS", "SMS"
        EMAIL = "EMAIL", "Email"

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="otp_codes",
    )
    phone = models.CharField("téléphone", max_length=20, blank=True, db_index=True)
    email = models.EmailField("email", blank=True, null=True, unique=True)
    channel = models.CharField("canal", max_length=8, choices=Channel.choices, default=Channel.SMS)
    purpose = models.CharField("usage", max_length=24, choices=Purpose.choices, db_index=True)
    code_hash = models.CharField("empreinte du code", max_length=128)
    salt = models.CharField("sel", max_length=64)
    expires_at = models.DateTimeField("expire le", db_index=True)
    attempts = models.PositiveSmallIntegerField("tentatives", default=0)
    consumed_at = models.DateTimeField("consommé le", null=True, blank=True)
    ip_address = models.GenericIPAddressField("adresse IP", null=True, blank=True)
    user_agent = models.CharField("agent utilisateur", max_length=200, blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "code OTP"
        verbose_name_plural = "codes OTP"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["phone", "purpose", "consumed_at"]),
        ]

    def __str__(self) -> str:
        return f"OTP({self.purpose}, {self.phone or self.email}) exp {self.expires_at:%H:%M}"

    # -- Cycle de vie -------------------------------------------------------
    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    @property
    def is_usable(self) -> bool:
        return not self.is_expired and not self.is_consumed

    def consume(self) -> None:
        self.consumed_at = timezone.now()
        self.save(update_fields=["consumed_at"])

    @staticmethod
    def hash_code(code: str, salt: str) -> str:
        return sha256(f"{salt}{code}".encode()).hexdigest()

    def set_code(self, code: str) -> None:
        self.salt = secrets.token_hex(32)
        self.code_hash = self.hash_code(code, self.salt)

    def verify(self, code: str) -> bool:
        """Comparaison en temps constant, sans jamais journaliser le code."""
        from hmac import compare_digest

        return compare_digest(self.code_hash, self.hash_code(str(code or ""), self.salt))
