"""Endpoints de synchronisation hors ligne (MVP-009).

`POST /api/sync/batch/` rejoue un lot d'opérations mises en file par l'appareil :

* chaque opération est **isolée** (transaction + gestion d'erreur propres) : un refus métier
  n'annule jamais les autres opérations du lot ;
* chaque opération est **idempotente** (`Idempotency-Key` côté client) : une reprise après
  coupure ne produit jamais deux effets ;
* le résultat par opération est explicite : `SYNCED` (appliquée ou rejouée), `CONFLICT`
  (l'utilisateur doit décider) ou `FAILED` (opération à corriger) — le client ne devine rien.

Le lot ne transporte **aucun fichier** : les photos partent une par une sur
`POST /api/evidences/`, avec la même garantie d'idempotence.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import KemtaAPIError
from apps.sync.idempotency import claim, mark_done
from apps.sync.models import SyncOperation, SyncOperationStatus
from apps.sync.operations import (
    BATCHABLE_OPERATIONS,
    FILE_OPERATIONS,
    classify_error,
    run_operation,
)
from apps.sync.serializers import SyncBatchInputSerializer

logger = logging.getLogger("kemta.sync")


class SyncBatchView(APIView):
    """`POST /api/sync/batch/` — reprise de la file hors ligne."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SyncBatchInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        results = []
        counts = {"synced": 0, "conflict": 0, "failed": 0, "replayed": 0}

        for operation in serializer.validated_data["operations"]:
            result = self._apply(operation, request)
            results.append(result)
            counts[result["status"].lower()] += 1
            if result.get("replayed"):
                counts["replayed"] += 1

        return Response(
            {
                "results": results,
                "counts": counts,
                "server_time": timezone.now().isoformat(),
            }
        )

    def _apply(self, operation: dict, request) -> dict:
        op_id = operation["op_id"]
        operation_type = operation["type"]
        key = operation["idempotency_key"]
        payload = operation.get("payload") or {}

        try:
            with transaction.atomic():
                record, replay = claim(
                    user=request.user,
                    idempotency_key=key,
                    operation_type=operation_type,
                )
                if replay is not None:
                    # Déjà appliquée lors d'un envoi précédent : on renvoie le même résultat.
                    return {
                        "op_id": op_id,
                        "type": operation_type,
                        "status": "SYNCED",
                        "replayed": True,
                        "http_status": replay.http_status,
                        "entity_type": replay.entity_type or None,
                        "entity_id": replay.entity_id,
                        "entity": replay.body or None,
                        "error": None,
                    }

                result = run_operation(operation_type, payload, request.user, request)
                mark_done(
                    record,
                    http_status=result.http_status,
                    body=result.body,
                    entity_type=result.entity_type,
                    entity_id=result.entity_id,
                )
                return {
                    "op_id": op_id,
                    "type": operation_type,
                    "status": "SYNCED",
                    "replayed": False,
                    "http_status": result.http_status,
                    "entity_type": result.entity_type,
                    "entity_id": result.entity_id,
                    "entity": result.body,
                    "error": None,
                }
        except Exception as caught:  # chaque opération est classée, jamais propagée
            outcome, api_error = classify_error(caught)

            # Refus métier : la clé est libérée pour que l'utilisateur puisse corriger et
            # relancer. Le motif remonte tel quel au client (aucun échec silencieux).
            SyncOperation.objects.filter(
                user=request.user, idempotency_key=key, status=SyncOperationStatus.IN_PROGRESS
            ).delete()
            logger.warning(
                "Opération de synchronisation %s refusée (%s) : %s",
                operation_type,
                api_error.code,
                api_error.message,
            )
            return {
                "op_id": op_id,
                "type": operation_type,
                "status": outcome,
                "replayed": False,
                "http_status": api_error.status_code,
                "entity_type": None,
                "entity_id": None,
                "entity": None,
                "error": {
                    "code": api_error.code,
                    "message": api_error.message,
                    "details": api_error.details,
                },
            }


class SyncStatusView(APIView):
    """`GET /api/sync/status/` — état du service de synchronisation pour l'appareil.

    Permet à l'écran de suivi de savoir ce que le serveur attend (types supportés) et à
    l'exploitation de repérer des opérations restées `IN_PROGRESS` (verrous orphelins).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        operations = SyncOperation.objects.filter(user=request.user)
        in_progress = operations.filter(status=SyncOperationStatus.IN_PROGRESS)
        last_done = (
            operations.filter(status=SyncOperationStatus.DONE).order_by("-updated_at").first()
        )
        return Response(
            {
                "server_time": timezone.now().isoformat(),
                "supported_operations": list(BATCHABLE_OPERATIONS),
                "file_operations": list(FILE_OPERATIONS),
                "in_progress": in_progress.count(),
                "applied": operations.filter(status=SyncOperationStatus.DONE).count(),
                "last_applied_at": last_done.updated_at if last_done else None,
                "batch_limit": 50,
            }
        )


class SyncOperationReplayView(APIView):
    """`POST /api/sync/operations/{idempotency_key}/forget/` — libère une clé restée bloquée.

    Un appareil peut disparaître en plein traitement : la clé reste alors `IN_PROGRESS` et
    bloquerait la reprise. L'utilisateur peut la libérer explicitement (jamais automatique :
    on ne veut pas risquer d'appliquer deux fois la même opération par inadvertance).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, idempotency_key):
        deleted, _ = SyncOperation.objects.filter(
            user=request.user,
            idempotency_key=idempotency_key,
            status=SyncOperationStatus.IN_PROGRESS,
        ).delete()
        if not deleted:
            raise KemtaAPIError(
                "operation_not_found",
                "Aucune opération en cours avec cette clé.",
                http_status=404,
            )
        return Response({"released": True}, status=status.HTTP_200_OK)
