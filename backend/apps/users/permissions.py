"""Permissions DRF dérivées de la matrice des rôles (docs/rbac-matrix.md)."""

from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.users.roles import Capability, user_has


class HasCapability(BasePermission):
    """Autorise si le rôle de l'utilisateur possède la capacité demandée."""

    capability: str = ""

    def has_permission(self, request, view) -> bool:
        return user_has(request.user, self.capability)


class CanCreateOrganization(HasCapability):
    capability = Capability.CREATE_ORGANIZATION


class CanCreateProject(HasCapability):
    capability = Capability.CREATE_PROJECT


class CanManageMembers(HasCapability):
    capability = Capability.MANAGE_MEMBERS


class CanValidateEvidence(HasCapability):
    capability = Capability.VALIDATE_EVIDENCE


class CanManageFinance(HasCapability):
    capability = Capability.MANAGE_FINANCE


class CanViewFinance(HasCapability):
    """Lecture financière : `GET` autorisé, écriture réservée à `manage_finance`."""

    capability = Capability.VIEW_FINANCE

    def has_permission(self, request, view) -> bool:
        if request.method in SAFE_METHODS:
            return user_has(request.user, Capability.VIEW_FINANCE)
        return user_has(request.user, Capability.MANAGE_FINANCE)


class CanViewActivity(HasCapability):
    capability = Capability.VIEW_ACTIVITY


class CanViewAuthLogs(HasCapability):
    capability = Capability.VIEW_AUTH_LOGS


class CanViewOperations(HasCapability):
    capability = Capability.VIEW_OPERATIONS
