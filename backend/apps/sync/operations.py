"""Exécution des opérations rejouées depuis la file hors ligne (MVP-009).

Chaque opération réutilise **le service métier de l'API interactive** : les permissions, la
machine à états, le recalcul d'avancement et la journalisation sont identiques, qu'une action
arrive en direct ou après une coupure réseau. Aucune règle n'est réimplémentée ici.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException as DRFAPIException
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.core.exceptions import KemtaAPIError
from apps.evidences.access import accessible_evidences
from apps.evidences.serializers import EvidenceSerializer, EvidenceValidationSerializer
from apps.evidences.services import apply_transition
from apps.projects.access import accessible_projects
from apps.projects.models import Milestone, Task
from apps.projects.serializers import MilestoneSerializer, TaskSerializer
from apps.projects.services import (
    create_milestone,
    create_task,
    update_milestone,
    update_task,
)

# Types traités par le lot (opérations **sans fichier**). Les photos suivent leur propre
# endpoint idempotent : `POST /api/evidences/`.
BATCHABLE_OPERATIONS = (
    "EVIDENCE_TRANSITION",
    "TASK_UPDATE",
    "TASK_CREATE",
    "MILESTONE_UPDATE",
    "MILESTONE_CREATE",
)

# Types connus du client mais qui ne peuvent pas voyager dans un lot JSON.
FILE_OPERATIONS = ("EVIDENCE_UPLOAD",)

SUPPORTED_OPERATIONS = BATCHABLE_OPERATIONS + FILE_OPERATIONS


@dataclass
class OperationResult:
    """Effet produit par une opération : entité touchée + réponse à renvoyer au client."""

    entity_type: str
    entity_id: int
    http_status: int = 200
    body: dict = field(default_factory=dict)


def _accessible_tasks(user):
    return Task.objects.filter(project__in=accessible_projects(user)).select_related(
        "milestone", "assignee"
    )


def _accessible_milestones(user):
    return Milestone.objects.filter(project__in=accessible_projects(user))


def _payload_fields(payload: dict, *reserved: str) -> dict:
    """Champs métier du payload : on retire les clés de routage (`task`, `project`…)."""
    return {key: value for key, value in payload.items() if key not in reserved}


def _evidence_transition(payload: dict, actor, request) -> OperationResult:
    evidence_id = payload.get("evidence") or payload.get("evidence_id")
    action = payload.get("action")
    if not evidence_id or not action:
        raise KemtaAPIError(
            "invalid_operation_payload",
            "Une décision de validation exige « evidence » et « action ».",
            http_status=400,
        )
    evidence = get_object_or_404(accessible_evidences(actor), pk=evidence_id)
    evidence = apply_transition(
        evidence=evidence,
        actor=actor,
        action=action,
        comment=payload.get("comment") or "",
        request=request,
    )
    body = EvidenceSerializer(evidence, context={"request": request, "user": actor}).data
    last = evidence.validations.order_by("-created_at").first()
    body["last_validation"] = EvidenceValidationSerializer(last).data if last else None
    return OperationResult(entity_type="Evidence", entity_id=evidence.pk, body=body)


def _task_update(payload: dict, actor, request) -> OperationResult:
    task_id = payload.get("task") or payload.get("task_id")
    if not task_id:
        raise KemtaAPIError(
            "invalid_operation_payload",
            "Une mise à jour de tâche exige « task ».",
            http_status=400,
        )
    task = get_object_or_404(_accessible_tasks(actor), pk=task_id)
    task, progress = update_task(
        task=task, actor=actor, data=_payload_fields(payload, "task", "task_id"), request=request
    )
    body = TaskSerializer(task, context={"request": request, "project": task.project}).data
    body["project_progress"] = float(progress)
    return OperationResult(entity_type="Task", entity_id=task.pk, body=body)


def _task_create(payload: dict, actor, request) -> OperationResult:
    project_id = payload.get("project") or payload.get("project_id")
    if not project_id:
        raise KemtaAPIError(
            "invalid_operation_payload",
            "Une création de tâche exige « project ».",
            http_status=400,
        )
    project = get_object_or_404(accessible_projects(actor), pk=project_id)
    task, progress = create_task(
        project=project,
        actor=actor,
        data=_payload_fields(payload, "project", "project_id"),
        request=request,
    )
    body = TaskSerializer(task, context={"request": request, "project": project}).data
    body["project_progress"] = float(progress)
    return OperationResult(entity_type="Task", entity_id=task.pk, http_status=201, body=body)


def _milestone_update(payload: dict, actor, request) -> OperationResult:
    milestone_id = payload.get("milestone") or payload.get("milestone_id")
    if not milestone_id:
        raise KemtaAPIError(
            "invalid_operation_payload",
            "Une mise à jour de jalon exige « milestone ».",
            http_status=400,
        )
    milestone = get_object_or_404(_accessible_milestones(actor), pk=milestone_id)
    milestone, progress = update_milestone(
        milestone=milestone,
        actor=actor,
        data=_payload_fields(payload, "milestone", "milestone_id"),
        request=request,
    )
    body = MilestoneSerializer(milestone, context={"request": request}).data
    body["project_progress"] = float(progress)
    return OperationResult(entity_type="Milestone", entity_id=milestone.pk, body=body)


def _milestone_create(payload: dict, actor, request) -> OperationResult:
    project_id = payload.get("project") or payload.get("project_id")
    if not project_id:
        raise KemtaAPIError(
            "invalid_operation_payload",
            "Une création de jalon exige « project ».",
            http_status=400,
        )
    project = get_object_or_404(accessible_projects(actor), pk=project_id)
    milestone, progress = create_milestone(
        project=project,
        actor=actor,
        data=_payload_fields(payload, "project", "project_id"),
        request=request,
    )
    body = MilestoneSerializer(milestone, context={"request": request}).data
    body["project_progress"] = float(progress)
    return OperationResult(
        entity_type="Milestone", entity_id=milestone.pk, http_status=201, body=body
    )


HANDLERS = {
    "EVIDENCE_TRANSITION": _evidence_transition,
    "TASK_UPDATE": _task_update,
    "TASK_CREATE": _task_create,
    "MILESTONE_UPDATE": _milestone_update,
    "MILESTONE_CREATE": _milestone_create,
}


def run_operation(operation_type: str, payload: dict, actor, request) -> OperationResult:
    """Applique une opération du lot, ou explique pourquoi elle ne peut pas l'être."""
    if operation_type in FILE_OPERATIONS:
        raise KemtaAPIError(
            "operation_requires_file",
            "Cette opération porte un fichier : envoyez-la sur POST /api/evidences/ "
            "avec sa clé d'idempotence.",
            http_status=400,
            details={"type": operation_type, "endpoint": "/api/evidences/"},
        )
    handler = HANDLERS.get(operation_type)
    if handler is None:
        raise KemtaAPIError(
            "unsupported_operation",
            f"Opération inconnue : « {operation_type} ».",
            http_status=400,
            details={"supported": list(SUPPORTED_OPERATIONS)},
        )
    return handler(payload, actor, request)


CONFLICT_CODES = {
    "permission_denied",
    "cannot_validate_own_evidence",
    "invalid_transition",
    "comment_required",
    "duplicate_evidence",
    "op_in_progress",
    "idempotency_key_conflict",
    "project_not_active",
    # L'élément visé a disparu ou n'est plus dans le périmètre : l'utilisateur décide
    # s'il abandonne l'opération ou conserve sa copie locale.
    "not_found",
}


def classify_error(error: Exception) -> tuple[str, KemtaAPIError]:
    """Traduit une erreur métier en résultat pour le client (voir `docs/offline-sync.md`).

    * **CONFLICT** — l'état du serveur ne permet pas l'opération telle quelle : l'utilisateur
      doit décider (permissions, transition impossible, projet archivé, doublon…). L'opération
      reste affichée avec son motif, jamais appliquée en silence.
    * **FAILED** — l'opération est mal formée ou définitivement refusée : la corriger ou
      l'abandonner.
    """
    if isinstance(error, KemtaAPIError):
        api_error = error
    elif isinstance(error, Http404):
        api_error = KemtaAPIError(
            "not_found",
            "L'élément visé par cette opération est introuvable : supprimé, ou hors de "
            "votre périmètre.",
            http_status=404,
        )
    elif isinstance(error, DRFValidationError):
        api_error = KemtaAPIError(
            "validation_error",
            "Les données de cette opération sont invalides.",
            http_status=400,
            details=dict(error.detail) if isinstance(error.detail, dict) else {},
        )
    elif isinstance(error, DRFAPIException):
        api_error = KemtaAPIError(
            "validation_error", str(error.detail), http_status=error.status_code
        )
    elif isinstance(error, DjangoValidationError):
        api_error = KemtaAPIError("validation_error", " ".join(error.messages), http_status=400)
    else:  # pragma: no cover - garde-fou
        api_error = KemtaAPIError("server_error", "Erreur inattendue.", http_status=500)

    status = "CONFLICT" if api_error.code in CONFLICT_CODES else "FAILED"
    return status, api_error
