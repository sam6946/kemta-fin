"""Sérialiseurs d'authentification (inscription, OTP, connexion, mot de passe)."""

from __future__ import annotations

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.core.exceptions import KemtaAPIError
from apps.users.models import OTPCode, User
from apps.users.services.phone import mask_phone, normalize_phone


def _validate_new_password(password: str, user: User | None) -> None:
    """Applique la politique de mot de passe **côté serveur**."""
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        raise KemtaAPIError(
            "password_too_weak",
            "Mot de passe non conforme.",
            details={"messages": list(exc.messages)},
        ) from exc


class RegisterSerializer(serializers.Serializer):
    """Inscription : téléphone + mot de passe. **Aucune adresse email demandée.**"""

    phone = serializers.CharField(max_length=32, trim_whitespace=True)
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    password_confirm = serializers.CharField(write_only=True, style={"input_type": "password"})
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)

    def validate_phone(self, value: str) -> str:
        normalized, error = normalize_phone(value)
        if error:
            raise serializers.ValidationError("Numéro de téléphone invalide.", code=error)
        existing = User.all_objects.filter(phone=normalized).first()
        if existing is not None and existing.deleted_at is None:
            if existing.is_active:
                raise KemtaAPIError(
                    "phone_already_used",
                    "Ce numéro est déjà utilisé. Connectez-vous ou réinitialisez votre mot de passe.",
                    http_status=409,
                )
            raise KemtaAPIError(
                "phone_pending_activation",
                "Ce numéro est déjà inscrit mais n'a pas encore été activé.",
                http_status=409,
                details={"can_resend": True},
            )
        return normalized

    def validate_first_name(self, value: str) -> str:
        return (value or "").strip()

    def validate_last_name(self, value: str) -> str:
        return (value or "").strip()

    def validate(self, attrs: dict) -> dict:
        if attrs["password"] != attrs["password_confirm"]:
            raise KemtaAPIError("password_mismatch", "Les mots de passe ne correspondent pas.")
        _validate_new_password(
            attrs["password"],
            User(
                phone=attrs["phone"],
                first_name=attrs["first_name"],
                last_name=attrs["last_name"],
            ),
        )
        return attrs

    def create(self, validated_data: dict) -> User:
        user = User(
            phone=validated_data["phone"],
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            is_active=False,  # activé uniquement après validation de l'OTP
            is_phone_verified=False,
        )
        user.set_password(validated_data["password"])
        user.save()
        return user


class OTPVerifySerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32, trim_whitespace=True)
    code = serializers.CharField(min_length=4, max_length=8, trim_whitespace=True)
    purpose = serializers.ChoiceField(
        choices=[OTPCode.Purpose.SIGNUP], default=OTPCode.Purpose.SIGNUP
    )

    def validate_phone(self, value: str) -> str:
        normalized, error = normalize_phone(value)
        if error:
            raise serializers.ValidationError("Numéro de téléphone invalide.", code=error)
        return normalized


class OTPResendSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32, trim_whitespace=True)
    purpose = serializers.ChoiceField(
        choices=[OTPCode.Purpose.SIGNUP], default=OTPCode.Purpose.SIGNUP
    )

    def validate_phone(self, value: str) -> str:
        normalized, error = normalize_phone(value)
        if error:
            raise serializers.ValidationError("Numéro de téléphone invalide.", code=error)
        return normalized


class LoginSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32, trim_whitespace=True)
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate_phone(self, value: str) -> str:
        normalized, error = normalize_phone(value)
        if error:
            raise serializers.ValidationError("Numéro de téléphone invalide.", code=error)
        return normalized


class PasswordResetRequestSerializer(serializers.Serializer):
    """Demande de réinitialisation : le numéro de téléphone suffit."""

    phone = serializers.CharField(max_length=32, trim_whitespace=True)

    def validate_phone(self, value: str) -> str:
        normalized, error = normalize_phone(value)
        if error:
            # Réponse neutre : un numéro invalide ne doit pas révéler d'information.
            return ""
        return normalized


class PasswordResetConfirmSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32, trim_whitespace=True)
    code = serializers.CharField(min_length=4, max_length=8, trim_whitespace=True)
    new_password = serializers.CharField(write_only=True, style={"input_type": "password"})
    new_password_confirm = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate_phone(self, value: str) -> str:
        normalized, error = normalize_phone(value)
        if error:
            raise serializers.ValidationError("Numéro de téléphone invalide.", code=error)
        return normalized

    def validate(self, attrs: dict) -> dict:
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise KemtaAPIError("password_mismatch", "Les mots de passe ne correspondent pas.")
        return attrs


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, style={"input_type": "password"})
    new_password = serializers.CharField(write_only=True, style={"input_type": "password"})
    new_password_confirm = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate_current_password(self, value: str) -> str:
        user = self.context["request"].user
        if not user.check_password(value):
            raise KemtaAPIError(
                "invalid_credentials", "Mot de passe actuel incorrect.", http_status=400
            )
        return value

    def validate(self, attrs: dict) -> dict:
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise KemtaAPIError("password_mismatch", "Les mots de passe ne correspondent pas.")
        user = self.context["request"].user
        if user.check_password(attrs["new_password"]):
            raise KemtaAPIError(
                "password_reused",
                "Le nouveau mot de passe doit être différent de l'actuel.",
            )
        _validate_new_password(attrs["new_password"], user)
        return attrs


class EmailRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class EmailConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(min_length=4, max_length=8, trim_whitespace=True)


class UserSerializer(serializers.ModelSerializer):
    """Profil exposé au frontend (jamais de donnée sensible)."""

    role_label = serializers.CharField(source="get_role_display", read_only=True)
    phone_masked = serializers.SerializerMethodField()
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "phone",
            "phone_masked",
            "email",
            "first_name",
            "last_name",
            "role",
            "role_label",
            "language",
            "is_phone_verified",
            "email_verified_at",
            "capabilities",
        ]
        read_only_fields = [
            "id",
            "phone",
            "role",
            "is_phone_verified",
            "email_verified_at",
        ]

    def get_phone_masked(self, obj: User) -> str:
        return mask_phone(obj.phone)

    def get_capabilities(self, obj: User) -> list[str]:
        if obj.is_superuser:
            from apps.users.roles import ALL_CAPABILITIES

            return sorted(ALL_CAPABILITIES)
        from apps.users.roles import ROLE_CAPABILITIES

        return sorted(ROLE_CAPABILITIES.get(obj.role, frozenset()))
