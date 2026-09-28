"""Permissions financières — **autorité unique** (voir `docs/rbac-matrix.md` §6).

Deux niveaux, volontairement distincts :

* `can_view_finance` — lire le budget, les dépenses et le grand livre ;
* `can_manage_finance` — créer et corriger une dépense avant approbation (un contractant peut
  recevoir `can_manage_finance` sur un projet) ;
* `can_settle_finance` — approuver, rejeter, annuler, enregistrer un paiement et modifier le
  budget : capacités réservées aux rôles de pilotage (`can_manage_finance` seul, accordé par
  drapeau, ne suffit pas — décision documentée dans `docs/flows/finance.md`).
"""

from __future__ import annotations

from apps.projects.access import (
    build_capabilities_map,
    build_project_roles_map,
    has_project_capability,
    project_role,
)
from apps.users.roles import Capability, Role

# Rôles habilités à engager de l'argent (approbation, paiement, budget).
SETTLEMENT_ROLES = frozenset(
    {Role.PLATFORM_ADMIN, Role.ORG_OWNER, Role.PROJECT_OWNER, Role.FINANCE}
)


def can_view_finance(user, project) -> bool:
    return has_project_capability(user, project, Capability.VIEW_FINANCE)


def can_manage_finance(user, project) -> bool:
    return has_project_capability(user, project, Capability.MANAGE_FINANCE)


def can_settle_finance(user, project) -> bool:
    """Approbation, rejet, annulation, paiement, budget : rôles de pilotage uniquement."""
    if not has_project_capability(user, project, Capability.MANAGE_FINANCE):
        return False
    return project_role(user, project) in SETTLEMENT_ROLES


def finance_permissions_map(user, projects) -> dict[int, dict[str, bool]]:
    """Permissions financières de **plusieurs** projets, en un nombre constant de requêtes.

    Sans cette vectorisation, afficher 40 postes budgétaires résoudrait 40 fois les droits du
    même utilisateur sur le même projet (N+1). Les capacités dépendant de l'objet (auteur de la
    dépense, statut) restent affinées par les sérialiseurs.
    """
    projects = list(projects)
    if not projects:
        return {}
    capabilities = build_capabilities_map(user, projects)
    roles = build_project_roles_map(user, projects)
    result: dict[int, dict[str, bool]] = {}
    for project in projects:
        granted = capabilities.get(project.pk, {})
        manage = bool(granted.get(Capability.MANAGE_FINANCE))
        settle = manage and roles.get(project.pk) in SETTLEMENT_ROLES
        result[project.pk] = {
            "view_finance": bool(granted.get(Capability.VIEW_FINANCE)),
            "manage_finance": manage,
            "settle_finance": settle,
            "can_approve": settle,
            "can_pay": settle,
            "can_edit": manage,
            "can_cancel": settle,
        }
    return result
