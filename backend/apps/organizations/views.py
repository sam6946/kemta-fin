"""Endpoints organisations et membres (MVP-005)."""

from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.activity import log_event
from apps.core.exceptions import KemtaAPIError
from apps.organizations.models import Organization, OrganizationMember
from apps.organizations.serializers import (
    MemberWriteSerializer,
    OrganizationMemberSerializer,
    OrganizationSerializer,
)
from apps.projects.access import (
    accessible_organizations,
    organization_role,
)
from apps.projects.permissions import CanCreateOrganization, CanWriteOrganization
from apps.users.roles import Role


def paginated(request, queryset, serializer_class):
    from apps.core.pagination import DefaultPagination

    paginator = DefaultPagination()
    page = paginator.paginate_queryset(queryset, request)
    serializer = serializer_class(page, many=True, context={"request": request})
    return paginator.get_paginated_response(serializer.data)


class OrganizationListCreateView(APIView):
    """`GET /api/organizations/` (périmètre) et `POST` (création)."""

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), CanCreateOrganization()]
        return [IsAuthenticated()]

    def get(self, request):
        queryset = OrganizationSerializer.with_counts(accessible_organizations(request.user))
        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(name__icontains=search)
        queryset = queryset.order_by("name")
        return paginated(request, queryset, OrganizationSerializer)

    @transaction.atomic
    def post(self, request):
        serializer = OrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = serializer.save(owner=request.user)
        # Le créateur devient propriétaire actif de l'organisation.
        OrganizationMember.objects.create(
            organization=organization, user=request.user, role=Role.ORG_OWNER
        )
        log_event(
            "ORG_CREATED",
            actor=request.user,
            entity_type="Organization",
            entity_id=organization.pk,
            organization=organization,
            metadata={"name": organization.name, "type": organization.type},
            request=request,
        )
        return Response(
            OrganizationSerializer(organization, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class OrganizationDetailView(APIView):
    """`GET` / `PATCH` / `DELETE /api/organizations/{id}/`."""

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method in ("PATCH", "PUT", "DELETE"):
            return [IsAuthenticated(), CanWriteOrganization()]
        return [IsAuthenticated()]

    def get_object(self, request, pk) -> Organization:
        # Le queryset est filtré par périmètre : un objet hors périmètre est un 404.
        return get_object_or_404(
            OrganizationSerializer.with_counts(accessible_organizations(request.user)), pk=pk
        )

    def get(self, request, pk):
        organization = self.get_object(request, pk)
        return Response(OrganizationSerializer(organization, context={"request": request}).data)

    def patch(self, request, pk):
        organization = self.get_object(request, pk)
        self.check_object_permissions(request, organization)
        serializer = OrganizationSerializer(organization, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_event(
            "ORG_UPDATED",
            actor=request.user,
            entity_type="Organization",
            entity_id=organization.pk,
            organization=organization,
            metadata={"fields": sorted(request.data.keys())},
            request=request,
        )
        return Response(serializer.data)

    def delete(self, request, pk):
        organization = self.get_object(request, pk)
        self.check_object_permissions(request, organization)
        if organization.projects.filter(deleted_at__isnull=True).exists():
            raise KemtaAPIError(
                "organization_has_projects",
                "Impossible de supprimer une organisation contenant des projets actifs.",
                http_status=409,
                details={"projects": organization.projects.filter(deleted_at__isnull=True).count()},
            )
        organization.delete()  # suppression logique
        log_event(
            "ORG_UPDATED",
            actor=request.user,
            entity_type="Organization",
            entity_id=organization.pk,
            organization=organization,
            metadata={"status": "deleted"},
            request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrganizationMemberListCreateView(APIView):
    """`GET` / `POST /api/organizations/{id}/members/`."""

    permission_classes = [IsAuthenticated]

    def get_organization(self, request, pk) -> Organization:
        return get_object_or_404(accessible_organizations(request.user), pk=pk)

    def _require_writer(self, request, organization) -> None:
        if organization_role(request.user, organization) not in {
            Role.ORG_OWNER,
            Role.PLATFORM_ADMIN,
        }:
            raise KemtaAPIError(
                "permission_denied",
                "Seul le propriétaire de l'organisation peut gérer ses membres.",
                http_status=403,
            )

    def get(self, request, pk):
        organization = self.get_organization(request, pk)
        members = (
            OrganizationMember.objects.filter(organization=organization)
            .select_related("user")
            .order_by("created_at")
        )
        return paginated(request, members, OrganizationMemberSerializer)

    @transaction.atomic
    def post(self, request, pk):
        organization = self.get_organization(request, pk)
        self._require_writer(request, organization)

        serializer = MemberWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.user

        if OrganizationMember.objects.filter(organization=organization, user=user).exists():
            raise KemtaAPIError(
                "member_already_exists",
                "Cet utilisateur est déjà membre de l'organisation.",
                http_status=409,
            )

        member = OrganizationMember.objects.create(
            organization=organization, user=user, role=serializer.validated_data["role"]
        )
        log_event(
            "MEMBER_ADDED",
            actor=request.user,
            entity_type="OrganizationMember",
            entity_id=member.pk,
            organization=organization,
            metadata={"user_id": user.pk, "role": member.role},
            request=request,
        )
        return Response(OrganizationMemberSerializer(member).data, status=status.HTTP_201_CREATED)


class OrganizationMemberDetailView(APIView):
    """`PATCH` / `DELETE /api/organizations/{id}/members/{member_id}/`."""

    permission_classes = [IsAuthenticated]

    def get_member(self, request, pk, member_id) -> OrganizationMember:
        organization = get_object_or_404(accessible_organizations(request.user), pk=pk)
        if organization_role(request.user, organization) not in {
            Role.ORG_OWNER,
            Role.PLATFORM_ADMIN,
        }:
            raise KemtaAPIError(
                "permission_denied",
                "Seul le propriétaire de l'organisation peut gérer ses membres.",
                http_status=403,
            )
        return get_object_or_404(
            OrganizationMember.objects.select_related("user"),
            pk=member_id,
            organization=organization,
        )

    def patch(self, request, pk, member_id):
        member = self.get_member(request, pk, member_id)
        # PATCH partiel : un champ absent conserve sa valeur (sinon `default=True`
        # d'`is_active` réactiverait un membre désactivé sans qu'on le demande).
        serializer = MemberWriteSerializer(
            data={
                "phone": member.user.phone,
                "role": request.data.get("role", member.role),
                "is_active": request.data.get("is_active", member.is_active),
            }
        )
        serializer.is_valid(raise_exception=True)
        previous_role = member.role
        member.role = serializer.validated_data["role"]
        member.is_active = serializer.validated_data.get("is_active", member.is_active)
        member.save(update_fields=["role", "is_active", "updated_at"])
        if previous_role != member.role:
            log_event(
                "MEMBER_ROLE_CHANGED",
                actor=request.user,
                entity_type="OrganizationMember",
                entity_id=member.pk,
                organization=member.organization,
                metadata={
                    "user_id": member.user_id,
                    "old_role": previous_role,
                    "new_role": member.role,
                },
                request=request,
            )
        return Response(OrganizationMemberSerializer(member).data)

    def delete(self, request, pk, member_id):
        member = self.get_member(request, pk, member_id)
        if member.user_id == member.organization.owner_id:
            raise KemtaAPIError(
                "cannot_remove_owner",
                "Le propriétaire de l'organisation ne peut pas être retiré.",
                http_status=409,
            )
        member.is_active = False
        member.save(update_fields=["is_active", "updated_at"])
        log_event(
            "MEMBER_REMOVED",
            actor=request.user,
            entity_type="OrganizationMember",
            entity_id=member.pk,
            organization=member.organization,
            metadata={"user_id": member.user_id},
            request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
