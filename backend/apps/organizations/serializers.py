"""Sérialiseurs organisations et membres d'organisation."""

from __future__ import annotations

from django.db.models import Count, Q
from rest_framework import serializers

from apps.core.exceptions import KemtaAPIError
from apps.core.serializers import ModelValidationMixin
from apps.organizations.models import Organization, OrganizationMember, OrganizationType
from apps.users.models import User
from apps.users.roles import ALL_ROLES, ROLE_LABELS
from apps.users.serializers import UserSerializer


class OrganizationSerializer(ModelValidationMixin, serializers.ModelSerializer):
    owner = UserSerializer(read_only=True)
    type_label = serializers.CharField(source="get_type_display", read_only=True)
    project_count = serializers.IntegerField(read_only=True, default=0)
    member_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "slug",
            "type",
            "type_label",
            "country",
            "city",
            "address",
            "contact_phone",
            "owner",
            "is_active",
            "project_count",
            "member_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "slug", "owner", "created_at", "updated_at"]

    def validate_name(self, value: str) -> str:
        name = (value or "").strip()
        if len(name) < 3:
            raise serializers.ValidationError("Le nom doit contenir au moins 3 caractères.")
        return name

    def validate_type(self, value: str) -> str:
        if value not in OrganizationType.values:
            raise serializers.ValidationError("Type d'organisation inconnu.")
        return value

    @staticmethod
    def with_counts(queryset):
        """Évite toute requête supplémentaire par ligne (pas de N+1)."""
        return queryset.select_related("owner").annotate(
            project_count=Count(
                "projects", filter=Q(projects__deleted_at__isnull=True), distinct=True
            ),
            member_count=Count("members", filter=Q(members__is_active=True), distinct=True),
        )


class OrganizationMemberSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = OrganizationMember
        fields = ["id", "user", "role", "role_label", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class MemberWriteSerializer(serializers.Serializer):
    """Ajout d'un membre : le compte doit **déjà exister** (voir `docs/flows/project.md`).

    L'invitation d'un numéro inconnu (SMS d'invitation) est hors périmètre MVP.
    """

    phone = serializers.CharField(max_length=32, trim_whitespace=True)
    role = serializers.ChoiceField(choices=[(role, role) for role in ALL_ROLES])
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_phone(self, value: str) -> str:
        from apps.users.services.phone import normalize_phone

        normalized, error = normalize_phone(value)
        if error:
            raise serializers.ValidationError("Numéro de téléphone invalide.", code=error)
        self._user = User.objects.filter(phone=normalized).first()
        if self._user is None:
            raise KemtaAPIError(
                "user_not_found",
                "Aucun compte KEMTA ne correspond à ce numéro. "
                "L'utilisateur doit d'abord s'inscrire.",
                http_status=404,
            )
        if not self._user.is_active:
            raise KemtaAPIError(
                "user_not_activated",
                "Ce compte n'est pas encore activé : demandez-lui de valider son code SMS.",
                http_status=409,
            )
        return normalized

    def validate_role(self, value: str) -> str:
        if value not in ROLE_LABELS:
            raise serializers.ValidationError("Rôle inconnu.")
        return value

    @property
    def user(self) -> User:
        return self._user
