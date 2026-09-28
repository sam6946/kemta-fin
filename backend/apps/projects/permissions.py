"""Permissions DRF adossées à `apps.projects.access` (objet visible → 403, sinon 404)."""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from apps.projects.access import (
    has_organization_access,
    has_project_access,
    has_project_capability,
)
from apps.users.roles import Capability, user_has


class CanCreateOrganization(BasePermission):
    def has_permission(self, request, view) -> bool:
        return user_has(request.user, Capability.CREATE_ORGANIZATION)


class CanCreateProject(BasePermission):
    def has_permission(self, request, view) -> bool:
        return user_has(request.user, Capability.CREATE_PROJECT)


class OrganizationScopedPermission(BasePermission):
    """L'organisation doit être dans le périmètre de l'utilisateur.

    Le filtrage amont des querysets produit un 404 ; cette classe sert de garde
    pour les routes imbriquées (`/organizations/{id}/members/`).
    """

    def has_object_permission(self, request, view, obj) -> bool:
        return has_organization_access(request.user, obj)


class CanWriteOrganization(BasePermission):
    def has_object_permission(self, request, view, obj) -> bool:
        from apps.projects.access import organization_role
        from apps.users.roles import Role

        return organization_role(request.user, obj) in {Role.ORG_OWNER, Role.PLATFORM_ADMIN}


class ProjectAccessPermission(BasePermission):
    """Lecture : appartenance au projet (ou périmètre d'organisation pilotée)."""

    def has_object_permission(self, request, view, obj) -> bool:
        return has_project_access(request.user, obj)


class CanEditProject(BasePermission):
    def has_object_permission(self, request, view, obj) -> bool:
        return has_project_capability(request.user, obj, Capability.EDIT_PROJECT)


class CanManageProjectMembers(BasePermission):
    """Gestion des membres : la capacité dépend du projet, donc de l'objet.

    Les vues imbriquées (`/projects/{id}/members/`) résolvent le projet puis appellent
    `has_project_capability` : le contrôle reste identique, sans dépendre d'une
    méthode interne de la vue.
    """

    def has_object_permission(self, request, view, obj) -> bool:
        project = getattr(obj, "project", None) or obj
        return has_project_capability(request.user, project, Capability.MANAGE_MEMBERS)
