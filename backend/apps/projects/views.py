"""Endpoints projets et membres de projet (MVP-005)."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.activity import log_event
from apps.core.exceptions import KemtaAPIError
from apps.projects.access import (
    accessible_projects,
    build_capabilities_map,
    has_project_capability,
    permissions_payload,
    resolve_capabilities,
)
from apps.projects.models import Project, ProjectMember, ProjectStatus
from apps.projects.permissions import CanCreateProject
from apps.projects.serializers import (
    ProjectMemberSerializer,
    ProjectMemberWriteSerializer,
    ProjectSerializer,
)
from apps.users.roles import Capability, Role

ORDERING_FIELDS = {
    "created_at",
    "-created_at",
    "name",
    "-name",
    "status",
    "budget_total",
    "-budget_total",
}


def paginated(request, queryset, serializer_class, **context):
    from apps.core.pagination import DefaultPagination

    paginator = DefaultPagination()
    page = paginator.paginate_queryset(queryset, request)
    context.setdefault("request", request)
    serializer = serializer_class(page, many=True, context=context)
    return paginator.get_paginated_response(serializer.data)


def project_permissions(user, project: Project) -> dict:
    """Capacités exposées au frontend pour **ce** projet (aucune décision côté client)."""
    return permissions_payload(resolve_capabilities(user, project))


class ProjectListCreateView(APIView):
    """`GET /api/projects/` et `POST /api/projects/`."""

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), CanCreateProject()]
        return [IsAuthenticated()]

    def get(self, request):
        queryset = ProjectSerializer.with_counts(accessible_projects(request.user))

        status_filter = request.query_params.get("status")
        if status_filter:
            values = [value.upper() for value in status_filter.split(",") if value]
            unknown = [value for value in values if value not in ProjectStatus.values]
            if unknown:
                raise KemtaAPIError(
                    "invalid_status", "Statut inconnu.", details={"unknown": unknown}
                )
            queryset = queryset.filter(status__in=values)

        organization = request.query_params.get("organization")
        if organization:
            queryset = queryset.filter(organization_id=organization)

        search = (request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(code__icontains=search)
                | Q(city__icontains=search)
                | Q(location_label__icontains=search)
            )

        ordering = request.query_params.get("ordering", "-created_at")
        if ordering not in ORDERING_FIELDS:
            raise KemtaAPIError(
                "invalid_ordering",
                "Tri non supporté.",
                details={"supported": sorted(ORDERING_FIELDS)},
            )
        queryset = queryset.order_by(ordering)

        # Les permissions de toute la page sont calculées en une passe : pas de N+1.
        page = list(queryset[:100])
        capabilities_map = build_capabilities_map(request.user, page)
        return paginated(
            request, queryset, ProjectSerializer, capabilities_by_project=capabilities_map
        )

    @transaction.atomic
    def post(self, request):
        serializer = ProjectSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        project = serializer.save(created_by=request.user)

        # Le créateur est membre actif du projet avec le rôle de maître d'ouvrage.
        ProjectMember.objects.create(
            project=project,
            user=request.user,
            role=Role.PROJECT_OWNER,
            can_validate_evidence=True,
            can_manage_finance=True,
        )
        log_event(
            "PROJECT_CREATED",
            actor=request.user,
            entity_type="Project",
            entity_id=project.pk,
            organization=project.organization,
            project=project,
            metadata={
                "name": project.name,
                "code": project.code,
                "currency": project.currency,
                "budget_total": int(project.budget_total),
                "status": project.status,
            },
            request=request,
        )
        data = ProjectSerializer(project, context={"request": request}).data
        data["permissions"] = project_permissions(request.user, project)
        return Response(data, status=status.HTTP_201_CREATED)


class ProjectDetailView(APIView):
    """`GET` / `PATCH` / `DELETE /api/projects/{id}/`."""

    permission_classes = [IsAuthenticated]

    def get_project(self, request, pk) -> Project:
        return get_object_or_404(
            ProjectSerializer.with_counts(accessible_projects(request.user)), pk=pk
        )

    def _require(self, request, project, capability: str, message: str) -> None:
        if not has_project_capability(request.user, project, capability):
            raise KemtaAPIError("permission_denied", message, http_status=403)

    def get(self, request, pk):
        project = self.get_project(request, pk)
        data = ProjectSerializer(project, context={"request": request}).data
        data["permissions"] = project_permissions(request.user, project)
        return Response(data)

    def patch(self, request, pk):
        project = self.get_project(request, pk)
        self._require(
            request,
            project,
            Capability.EDIT_PROJECT,
            "Vous n'avez pas la permission de modifier ce projet.",
        )
        before = {
            "status": project.status,
            "budget_total": int(project.budget_total),
            "planned_end_date": str(project.planned_end_date) if project.planned_end_date else None,
        }
        serializer = ProjectSerializer(
            project, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        changed = {
            field: {"old": before[field], "new": value}
            for field, value in (
                ("status", project.status),
                ("budget_total", int(project.budget_total)),
                (
                    "planned_end_date",
                    str(project.planned_end_date) if project.planned_end_date else None,
                ),
            )
            if before[field] != value
        }
        log_event(
            "PROJECT_UPDATED",
            actor=request.user,
            entity_type="Project",
            entity_id=project.pk,
            organization=project.organization,
            project=project,
            metadata={"changed": changed},
            request=request,
        )
        data = ProjectSerializer(project, context={"request": request}).data
        data["permissions"] = project_permissions(request.user, project)
        return Response(data)

    def delete(self, request, pk):
        project = self.get_project(request, pk)
        self._require(
            request,
            project,
            Capability.ARCHIVE_PROJECT,
            "Vous n'avez pas la permission d'archiver ce projet.",
        )
        project.status = ProjectStatus.ARCHIVED
        project.save(update_fields=["status", "updated_at"])
        project.delete()  # suppression logique : l'historique reste intact
        log_event(
            "PROJECT_ARCHIVED",
            actor=request.user,
            entity_type="Project",
            entity_id=project.pk,
            organization=project.organization,
            project=project,
            metadata={"name": project.name},
            request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProjectMemberListCreateView(APIView):
    """`GET` / `POST /api/projects/{id}/members/`."""

    permission_classes = [IsAuthenticated]

    def get_project(self, request, pk) -> Project:
        return get_object_or_404(accessible_projects(request.user), pk=pk)

    def get(self, request, pk):
        project = self.get_project(request, pk)
        members = (
            ProjectMember.objects.filter(project=project)
            .select_related("user")
            .order_by("created_at")
        )
        return paginated(request, members, ProjectMemberSerializer)

    @transaction.atomic
    def post(self, request, pk):
        project = self.get_project(request, pk)
        if not has_project_capability(request.user, project, Capability.MANAGE_MEMBERS):
            raise KemtaAPIError(
                "permission_denied",
                "Vous n'avez pas la permission de gérer les membres de ce projet.",
                http_status=403,
            )

        serializer = ProjectMemberWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.user
        data = serializer.validated_data

        existing = ProjectMember.objects.filter(project=project, user=user).first()
        if existing is not None and existing.is_active:
            raise KemtaAPIError(
                "member_already_exists",
                "Cet utilisateur est déjà membre du projet.",
                http_status=409,
            )

        if existing is not None:  # réactivation d'un ancien membre
            member = existing
            member.is_active = True
            member.role = data["role"]
            member.can_validate_evidence = data.get("can_validate_evidence", False)
            member.can_manage_finance = data.get("can_manage_finance", False)
            member.save()
        else:
            member = ProjectMember.objects.create(
                project=project,
                user=user,
                role=data["role"],
                can_validate_evidence=data.get("can_validate_evidence", False),
                can_manage_finance=data.get("can_manage_finance", False),
            )

        log_event(
            "MEMBER_ADDED",
            actor=request.user,
            entity_type="ProjectMember",
            entity_id=member.pk,
            organization=project.organization,
            project=project,
            metadata={"user_id": user.pk, "role": member.role},
            request=request,
        )
        return Response(ProjectMemberSerializer(member).data, status=status.HTTP_201_CREATED)


class ProjectMemberDetailView(APIView):
    """`PATCH` / `DELETE /api/projects/{id}/members/{member_id}/`."""

    permission_classes = [IsAuthenticated]

    def get_member(self, request, pk, member_id) -> ProjectMember:
        project = get_object_or_404(accessible_projects(request.user), pk=pk)
        if not has_project_capability(request.user, project, Capability.MANAGE_MEMBERS):
            raise KemtaAPIError(
                "permission_denied",
                "Vous n'avez pas la permission de gérer les membres de ce projet.",
                http_status=403,
            )
        return get_object_or_404(
            ProjectMember.objects.select_related("user", "project"),
            pk=member_id,
            project=project,
        )

    def _managers_left(self, project: Project, excluding: ProjectMember | None = None) -> int:
        queryset = ProjectMember.objects.filter(project=project, is_active=True).filter(
            Q(role__in=[Role.PROJECT_OWNER, Role.ORG_OWNER])
            | Q(is_active=True, role=Role.PLATFORM_ADMIN)
        )
        if excluding is not None:
            queryset = queryset.exclude(pk=excluding.pk)
        return queryset.count()

    def patch(self, request, pk, member_id):
        member = self.get_member(request, pk, member_id)
        # PATCH partiel : les champs absents conservent leur valeur actuelle. Sans cette
        # fusion explicite, `default=False` des capacités écraserait silencieusement les
        # droits accordés (un simple changement de rôle les aurait révoqués).
        serializer = ProjectMemberWriteSerializer(
            data={
                # Le numéro identifie le compte au moment de l'ajout ; sur un PATCH il est
                # ignoré (pour viser un autre compte, retirer puis ajouter le membre).
                "phone": member.user.phone,
                "role": request.data.get("role", member.role),
                "can_validate_evidence": request.data.get(
                    "can_validate_evidence", member.can_validate_evidence
                ),
                "can_manage_finance": request.data.get(
                    "can_manage_finance", member.can_manage_finance
                ),
                "is_active": request.data.get("is_active", member.is_active),
            }
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        previous = {
            "role": member.role,
            "can_validate_evidence": member.can_validate_evidence,
            "can_manage_finance": member.can_manage_finance,
        }
        new_role = data["role"]
        keeps_management = new_role in {Role.PROJECT_OWNER, Role.ORG_OWNER}
        loses_management = (
            previous["role"]
            in {
                Role.PROJECT_OWNER,
                Role.ORG_OWNER,
            }
            and not keeps_management
        )
        if loses_management and self._managers_left(member.project, excluding=member) == 0:
            raise KemtaAPIError(
                "last_manager",
                "Le projet doit conserver au moins un responsable : "
                "ajoutez un autre responsable avant de changer ce rôle.",
                http_status=409,
            )

        member.role = new_role
        member.can_validate_evidence = data.get(
            "can_validate_evidence", member.can_validate_evidence
        )
        member.can_manage_finance = data.get("can_manage_finance", member.can_manage_finance)
        member.is_active = data.get("is_active", member.is_active)
        member.save()

        after = {
            "role": member.role,
            "can_validate_evidence": member.can_validate_evidence,
            "can_manage_finance": member.can_manage_finance,
        }
        changed = {
            key: {"old": previous[key], "new": after[key]}
            for key in after
            if previous[key] != after[key]
        }
        if changed:
            log_event(
                "MEMBER_ROLE_CHANGED",
                actor=request.user,
                entity_type="ProjectMember",
                entity_id=member.pk,
                organization=member.project.organization,
                project=member.project,
                metadata={"user_id": member.user_id, "changed": changed},
                request=request,
            )
        return Response(ProjectMemberSerializer(member).data)

    def delete(self, request, pk, member_id):
        member = self.get_member(request, pk, member_id)
        if member.user_id == member.project.created_by_id:
            raise KemtaAPIError(
                "cannot_remove_creator",
                "Le créateur du projet ne peut pas être retiré.",
                http_status=409,
            )
        if (
            member.role in {Role.PROJECT_OWNER, Role.ORG_OWNER}
            and self._managers_left(member.project, excluding=member) == 0
        ):
            raise KemtaAPIError(
                "last_manager",
                "Le projet doit conserver au moins un responsable.",
                http_status=409,
            )
        member.is_active = False
        member.save(update_fields=["is_active", "updated_at"])
        log_event(
            "MEMBER_REMOVED",
            actor=request.user,
            entity_type="ProjectMember",
            entity_id=member.pk,
            organization=member.project.organization,
            project=member.project,
            metadata={"user_id": member.user_id, "role": member.role},
            request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
