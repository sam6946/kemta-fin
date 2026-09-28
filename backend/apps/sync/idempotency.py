"""Application idempotente d'une opération de synchronisation.

Le même mécanisme sert le lot (`/api/sync/batch/`). Le déroulé d'une opération :

1. la clé est **réservée** (`IN_PROGRESS`) : si elle existe déjà et est `DONE`, on rejoue la
   réponse d'origine ; si elle est `IN_PROGRESS`, on refuse (`409 op_in_progress`) ;
2. l'opération est appliquée dans la transaction de l'appelant ;
3. en cas de succès, la clé passe `DONE` avec la réponse d'origine ;
4. en cas de **refus métier** (4xx), la clé est **libérée** : l'utilisateur peut corriger et
   relancer la même opération (le conflit est présenté, jamais silencieux) ;
5. en cas d'erreur inattendue, la transaction est annulée — donc la clé l'est aussi : un nouvel
   essai est possible et rien n'a été appliqué.

Deux clients qui utilisent la même clé pour des opérations différentes ne peuvent pas se
marcher dessus : le type d'opération est vérifié (`409 idempotency_key_conflict`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from django.db import IntegrityError, transaction

from apps.core.exceptions import KemtaAPIError
from apps.sync.models import SyncOperation, SyncOperationStatus


@dataclass
class Replay:
    """Résultat déjà produit par une opération antérieure (rejoué tel quel)."""

    http_status: int
    body: dict
    entity_type: str
    entity_id: int | None


def claim(
    *, user, idempotency_key: str, operation_type: str
) -> tuple[SyncOperation | None, Replay | None]:
    """Réserve la clé. Renvoie `(operation, None)` ou `(None, replay)` à rejouer."""
    try:
        with transaction.atomic():
            operation = SyncOperation.objects.create(
                user=user,
                idempotency_key=idempotency_key,
                operation_type=operation_type,
                status=SyncOperationStatus.IN_PROGRESS,
            )
        return operation, None
    except IntegrityError:
        existing = SyncOperation.objects.filter(user=user, idempotency_key=idempotency_key).first()
        if existing is None:  # pragma: no cover - course improbable
            raise

        if existing.operation_type != operation_type:
            raise KemtaAPIError(
                "idempotency_key_conflict",
                "Cette clé d'idempotence a déjà servi pour une autre opération.",
                http_status=409,
                details={
                    "existing_type": existing.operation_type,
                    "requested_type": operation_type,
                },
            ) from None

        if existing.status == SyncOperationStatus.IN_PROGRESS:
            # Un premier envoi est peut-être en cours de traitement : on ne double pas l'effet.
            raise KemtaAPIError(
                "op_in_progress",
                "Cette opération est déjà en cours de traitement : elle sera confirmée "
                "au prochain essai.",
                http_status=409,
                details={"operation_type": existing.operation_type},
            ) from None

        existing.entity_type = existing.entity_type or ""
        return None, Replay(
            http_status=existing.http_status or 200,
            body=existing.response_body or {},
            entity_type=existing.entity_type,
            entity_id=existing.entity_id,
        )


def mark_done(
    operation: SyncOperation,
    *,
    http_status: int,
    body: dict | None = None,
    entity_type: str = "",
    entity_id: int | None = None,
) -> None:
    """Clôt la clé : la réponse d'origine est conservée pour les renvois."""
    operation.status = SyncOperationStatus.DONE
    operation.http_status = http_status
    operation.entity_type = entity_type
    operation.entity_id = entity_id
    # La réponse d'origine est rejouée telle quelle : on la stocke sous forme JSON sûre.
    operation.response_body = json.loads(json.dumps(body or {}, default=str))
    operation.save(
        update_fields=[
            "status",
            "http_status",
            "entity_type",
            "entity_id",
            "response_body",
            "updated_at",
        ]
    )


def release(operation: SyncOperation) -> None:
    """Libère la clé après un refus métier : l'utilisateur peut corriger puis relancer."""
    operation.delete()
