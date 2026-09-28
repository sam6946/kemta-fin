"""Événements métier (phase 10) : le code métier *annonce*, un traitement asynchrone *réagit*.

Le domaine (jalons, dépenses, preuves, budget) appelle `emit(...)` au moment où une condition
est remplie ; il ne sait rien des notifications. L'événement est publié **après validation de
la transaction** (`transaction.on_commit`) : une opération annulée (rollback) n'envoie jamais
de notification, et l'utilisateur n'attend pas le traitement (tâche Celery).

Événements du périmètre MVP (`docs/BACKLOG_MVP.md`, phase 10) :

| Événement                | Condition d'émission                                             |
|--------------------------|------------------------------------------------------------------|
| `MilestoneValidated`     | un jalon passe au statut « Terminé »                             |
| `ExpenseSubmitted`       | une dépense est soumise à validation                             |
| `EvidenceRejected`       | une preuve terrain est rejetée                                   |
| `BudgetThresholdReached` | le budget engagé franchit 80 % puis 100 % (une fois par seuil)   |
| `ProjectDelayed`         | la planification détecte des tâches/jalons en retard (quotidien) |
"""

from __future__ import annotations

import logging

from django.db import transaction

from . import metrics


class DomainEvent:
    MILESTONE_VALIDATED = "MilestoneValidated"
    EXPENSE_SUBMITTED = "ExpenseSubmitted"
    EVIDENCE_REJECTED = "EvidenceRejected"
    BUDGET_THRESHOLD_REACHED = "BudgetThresholdReached"
    PROJECT_DELAYED = "ProjectDelayed"


ALL_EVENTS = (
    DomainEvent.MILESTONE_VALIDATED,
    DomainEvent.EXPENSE_SUBMITTED,
    DomainEvent.EVIDENCE_REJECTED,
    DomainEvent.BUDGET_THRESHOLD_REACHED,
    DomainEvent.PROJECT_DELAYED,
)

logger = logging.getLogger("kemta.events")


def emit(
    event_type: str,
    *,
    project,
    actor=None,
    entity_type: str = "",
    entity_id=None,
    payload: dict | None = None,
    recipient_ids: list[int] | None = None,
    dedupe_key: str = "",
) -> None:
    """Publie un événement métier après validation de la transaction courante.

    `recipient_ids` ajoute des destinataires ciblés (ex. l'auteur d'une preuve rejetée) aux
    destinataires de rôle ; `dedupe_key` garantit qu'un même événement (ex. le retard du jour)
    ne produit jamais deux notifications pour un destinataire.

    Ne lève jamais : une panne du broker ne doit pas faire échouer l'opération métier ; elle
    est journalisée avec le type d'événement pour permettre un rejeu manuel.
    """
    if event_type not in ALL_EVENTS:
        raise ValueError(f"Événement inconnu : {event_type}")

    body = {
        "event_type": event_type,
        "project_id": project.pk,
        "actor_id": getattr(actor, "pk", None),
        "entity_type": entity_type,
        "entity_id": str(entity_id) if entity_id is not None else "",
        "payload": payload or {},
        "recipient_ids": list(recipient_ids or []),
        "dedupe_key": dedupe_key,
    }

    def _publish() -> None:
        from apps.notifications.tasks import dispatch_domain_event

        metrics.incr("kemta_domain_events_total", event=event_type)
        try:
            dispatch_domain_event.delay(body)
        except Exception:  # broker indisponible : on trace, on ne casse pas l'opération
            logger.exception("Publication impossible de l'événement %s", event_type)

    transaction.on_commit(_publish)
