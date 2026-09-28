"""Services métier des preuves terrain (MVP-007, MVP-008, réutilisés en phase 6).

Le même code sert l'API interactive (`POST /api/evidences/{id}/transition/`) et la reprise
hors ligne (`POST /api/sync/batch/`) : une décision de validation appliquée depuis la file de
synchronisation suit **exactement** les mêmes règles (permissions, machine à états, journal)
qu'une décision appliquée en direct.
"""

from __future__ import annotations

from apps.core.activity import log_event
from apps.core.exceptions import KemtaAPIError
from apps.evidences.models import (
    ACTIONS_REQUIRING_COMMENT,
    Evidence,
    EvidenceValidation,
)
from apps.projects.access import has_project_capability, is_platform_admin
from apps.users.roles import Capability

ACTION_TO_EVENT = {
    "VALIDATE": "EVIDENCE_VALIDATED",
    "REJECT": "EVIDENCE_REJECTED",
    "FLAG": "EVIDENCE_FLAGGED",
    "REOPEN": "EVIDENCE_REOPENED",
}

ALLOWED_ACTIONS = ("VALIDATE", "REJECT", "FLAG", "REOPEN")


def allowed_actions_for(current_status: str) -> list[str]:
    """Actions réellement applicables depuis un statut (l'UI n'en invente aucune)."""
    return [
        action
        for action in ALLOWED_ACTIONS
        if EvidenceValidation.next_status(current_status, action) is not None
    ]


def apply_transition(
    *,
    evidence: Evidence,
    actor,
    action: str,
    comment: str = "",
    request=None,
) -> Evidence:
    """Applique une décision de validation et renvoie la preuve mise à jour.

    Contrôles identiques quel que soit le chemin d'appel : capacité `VALIDATE_EVIDENCE` sur le
    projet, interdiction de valider sa propre preuve (sauf administration plateforme),
    commentaire obligatoire pour un rejet ou un signalement, transition autorisée.
    """
    if not has_project_capability(actor, evidence.project, Capability.VALIDATE_EVIDENCE):
        raise KemtaAPIError(
            "permission_denied",
            "Vous n'avez pas la permission de valider les preuves de ce projet.",
            http_status=403,
        )

    # Anti-fraude : on ne valide pas sa propre preuve (l'administration plateforme excepte).
    if evidence.author_id == actor.pk and not is_platform_admin(actor):
        raise KemtaAPIError(
            "cannot_validate_own_evidence",
            "Vous ne pouvez pas valider ou rejeter votre propre preuve : "
            "un autre validateur doit statuer.",
            http_status=403,
        )

    comment = (comment or "").strip()
    if action in ACTIONS_REQUIRING_COMMENT and not comment:
        raise KemtaAPIError(
            "comment_required",
            "Un commentaire est obligatoire pour un rejet ou un signalement.",
            details={"action": action},
        )

    next_status = EvidenceValidation.next_status(evidence.status, action)
    if next_status is None:
        raise KemtaAPIError(
            "invalid_transition",
            f"Action « {action} » impossible depuis le statut « {evidence.get_status_display()} ».",
            http_status=409,
            details={
                "from_status": evidence.status,
                "action": action,
                "allowed_actions": allowed_actions_for(evidence.status),
            },
        )

    previous_status = evidence.status
    EvidenceValidation.objects.create(
        evidence=evidence,
        actor=actor,
        action=action,
        from_status=previous_status,
        to_status=next_status,
        comment=comment,
    )
    evidence.status = next_status
    evidence.save(update_fields=["status", "updated_at"])

    log_event(
        ACTION_TO_EVENT[action],
        actor=actor,
        entity_type="Evidence",
        entity_id=evidence.pk,
        organization=evidence.project.organization,
        project=evidence.project,
        metadata={
            "from_status": previous_status,
            "to_status": next_status,
            "comment": comment[:280],
            "author_id": evidence.author_id,
        },
        request=request,
    )
    return evidence
