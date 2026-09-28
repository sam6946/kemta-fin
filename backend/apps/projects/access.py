"""Règles d'accès aux organisations et aux projets — **autorité unique côté backend**.

Principes (voir `docs/rbac-matrix.md`) :

1. un projet n'existe, pour un utilisateur, que s'il y est autorisé : les querysets sont
   toujours filtrés, ce qui produit un **404** (et non un 403) sur un objet hors périmètre ;
2. un objet visible mais une action interdite produit un **403** ;
3. le rôle *par projet* (`ProjectMember.role`) prime sur le rôle global ;
4. `can_validate_evidence` et `can_manage_finance` n'ajoutent des droits que sur ce projet ;
5. l'administrateur plateforme et le superutilisateur voient tout (exploitation).

Le calcul des capacités est **vectorisé** : les listes résolvent tous les droits en un
nombre constant de requêtes (`build_capabilities_map`), jamais une requête par ligne.
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.organizations.models import Organization, OrganizationMember
from apps.projects.models import Project, ProjectMember
from apps.users.roles import ROLE_CAPABILITIES, Capability, Role

# Capacités exposées au frontend (celles qui pilotent l'affichage des actions).
PROJECT_CAPABILITIES = (
    Capability.EDIT_PROJECT,
    Capability.ARCHIVE_PROJECT,
    Capability.MANAGE_MEMBERS,
    Capability.CREATE_PROJECT,
    Capability.MANAGE_SCHEDULE,
    Capability.UPDATE_TASK,
    Capability.CAPTURE_EVIDENCE,
    Capability.VALIDATE_EVIDENCE,
    Capability.VIEW_FINANCE,
    Capability.MANAGE_FINANCE,
    Capability.VIEW_ACTIVITY,
)

# Capacités réellement affichées sur un projet (l'ordre suit `docs/api-contract.md`).
PROJECT_PERMISSION_KEYS = (
    Capability.EDIT_PROJECT,
    Capability.ARCHIVE_PROJECT,
    Capability.MANAGE_MEMBERS,
    Capability.MANAGE_SCHEDULE,
    Capability.UPDATE_TASK,
    Capability.CAPTURE_EVIDENCE,
    Capability.VALIDATE_EVIDENCE,
    Capability.VIEW_FINANCE,
    Capability.MANAGE_FINANCE,
    Capability.VIEW_ACTIVITY,
)


def is_platform_admin(user) -> bool:
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and (
            getattr(user, "is_superuser", False)
            or getattr(user, "role", None) == Role.PLATFORM_ADMIN
        )
    )


# ---------------------------------------------------------------------------
# Organisations
# ---------------------------------------------------------------------------
def accessible_organizations(user) -> QuerySet[Organization]:
    """Organisations visibles : celles dont l'utilisateur est propriétaire ou membre actif."""
    if is_platform_admin(user):
        return Organization.objects.all()
    return Organization.objects.filter(
        Q(owner=user) | Q(members__user=user, members__is_active=True)
    ).distinct()


def has_organization_access(user, organization: Organization) -> bool:
    if is_platform_admin(user):
        return True
    return accessible_organizations(user).filter(pk=organization.pk).exists()


def organization_role(user, organization: Organization) -> str | None:
    """Rôle effectif dans l'organisation (propriétaire > membre)."""
    if is_platform_admin(user):
        return Role.PLATFORM_ADMIN
    if organization.owner_id == getattr(user, "pk", None):
        return Role.ORG_OWNER
    return (
        OrganizationMember.objects.filter(organization=organization, user=user, is_active=True)
        .values_list("role", flat=True)
        .first()
    )


# ---------------------------------------------------------------------------
# Projets
# ---------------------------------------------------------------------------
def accessible_projects(user) -> QuerySet[Project]:
    """Projets visibles : membres actifs du projet, ou périmètre d'une organisation pilotée."""
    if is_platform_admin(user):
        return Project.objects.all()

    organization_ids = (
        accessible_organizations(user)
        .filter(
            Q(owner=user)
            | Q(members__user=user, members__is_active=True, members__role=Role.ORG_OWNER)
        )
        .values_list("id", flat=True)
    )

    return Project.objects.filter(
        Q(members__user=user, members__is_active=True) | Q(organization_id__in=organization_ids)
    ).distinct()


def has_project_access(user, project: Project) -> bool:
    if is_platform_admin(user):
        return True
    return accessible_projects(user).filter(pk=project.pk).exists()


def project_role(user, project: Project) -> str | None:
    """Rôle effectif **sur ce projet** : le rôle par projet prime sur le rôle global."""
    membership = project_membership(user, project)
    return _resolve_role(user, project, membership)


def _resolve_role(user, project: Project, membership: ProjectMember | None) -> str | None:
    if is_platform_admin(user):
        return Role.PLATFORM_ADMIN
    if membership is not None:
        return membership.role
    org_role = organization_role(user, project.organization)
    if org_role in {Role.ORG_OWNER, Role.PROJECT_OWNER}:
        return org_role
    return None


def project_membership(user, project: Project) -> ProjectMember | None:
    return (
        ProjectMember.objects.filter(project=project, user=user, is_active=True)
        .select_related("user")
        .first()
    )


def _capabilities_for(role: str | None, membership: ProjectMember | None) -> frozenset:
    """Capacités effectives, flags de membre inclus (règle unique pour tous les appels)."""
    if role is None:
        return frozenset()
    capabilities = set(ROLE_CAPABILITIES.get(role, frozenset()))
    if membership is not None:
        if membership.can_validate_evidence:
            capabilities.add(Capability.VALIDATE_EVIDENCE)
        if membership.can_manage_finance:
            capabilities.add(Capability.MANAGE_FINANCE)
            capabilities.add(Capability.VIEW_FINANCE)
    return frozenset(capabilities)


def resolve_capabilities(user, project: Project) -> dict[str, bool]:
    """Capacités de l'utilisateur sur un projet donné."""
    if is_platform_admin(user):
        return dict.fromkeys(PROJECT_CAPABILITIES, True)
    membership = project_membership(user, project)
    role = _resolve_role(user, project, membership)
    capabilities = _capabilities_for(role, membership)
    return {capability: capability in capabilities for capability in PROJECT_CAPABILITIES}


def has_project_capability(user, project: Project, capability: str) -> bool:
    if project is None:
        return False
    return resolve_capabilities(user, project).get(capability, False)


def build_project_roles_map(user, projects) -> dict[int, str | None]:
    """Rôle effectif par projet, pour **une collection**, en un nombre constant de requêtes.

    Même règle que `project_role` : le rôle par projet prime, sinon le rôle d'organisation
    s'il est de pilotage ; l'administrateur plateforme voit tout.
    """
    projects = list(projects)
    if not projects:
        return {}
    if is_platform_admin(user):
        return {project.pk: Role.PLATFORM_ADMIN for project in projects}

    project_ids = [project.pk for project in projects]
    organization_ids = {project.organization_id for project in projects}

    memberships = {
        membership.project_id: membership
        for membership in ProjectMember.objects.filter(
            user=user, project_id__in=project_ids, is_active=True
        )
    }
    organization_roles = dict(
        OrganizationMember.objects.filter(
            user=user, organization_id__in=organization_ids, is_active=True
        ).values_list("organization_id", "role")
    )
    owned_organization_ids = set(
        Organization.objects.filter(pk__in=organization_ids, owner=user).values_list(
            "id", flat=True
        )
    )

    result: dict[int, str | None] = {}
    for project in projects:
        membership = memberships.get(project.pk)
        role = membership.role if membership is not None else None
        if role is None:
            if project.organization_id in owned_organization_ids:
                role = Role.ORG_OWNER
            else:
                organization_role_value = organization_roles.get(project.organization_id)
                if organization_role_value in {Role.ORG_OWNER, Role.PROJECT_OWNER}:
                    role = organization_role_value
        result[project.pk] = role
    return result


def build_capabilities_map(user, projects) -> dict[int, dict[str, bool]]:
    """Capacités pour **une collection** de projets, en un nombre constant de requêtes.

    Utilisé par les listes : sans cette vectorisation, afficher les permissions de chaque
    projet générerait une requête par ligne (N+1 détecté et bloqué par les tests).
    """
    projects = list(projects)
    if not projects:
        return {}
    roles = build_project_roles_map(user, projects)
    memberships = (
        {}
        if is_platform_admin(user)
        else {
            membership.project_id: membership
            for membership in ProjectMember.objects.filter(
                user=user, project_id__in=[project.pk for project in projects], is_active=True
            )
        }
    )

    result: dict[int, dict[str, bool]] = {}
    for project in projects:
        capabilities = _capabilities_for(roles.get(project.pk), memberships.get(project.pk))
        result[project.pk] = {
            capability: capability in capabilities for capability in PROJECT_CAPABILITIES
        }
    return result


def permissions_payload(capabilities: dict[str, bool]) -> dict[str, bool]:
    """Sous-ensemble exposé dans le champ `permissions` d'un projet."""
    return {key: bool(capabilities.get(key)) for key in PROJECT_PERMISSION_KEYS}
