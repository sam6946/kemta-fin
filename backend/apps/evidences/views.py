"""Endpoints des preuves terrain (MVP-007, MVP-008).

Contrats appliqués (voir `docs/api-contract.md` §6) :

* upload `multipart` avec en-tête **`Idempotency-Key` obligatoire** : un renvoi après coupure
  réseau retourne la preuve déjà enregistrée (`200`, `Idempotency-Replayed: true`) au lieu de
  créer un doublon ;
* doublon de contenu (`project`, `hash_sha256`) → `409 duplicate_evidence` **avec la preuve
  existante** dans les détails, ce qui permet au terrain de comprendre et de ne pas insister ;
* fichier non conforme → `413` (taille), `415` (type réel), `400` (dimensions) ;
* GPS disponible mais hors périmètre → `422 evidence_out_of_geofence` (comportement piloté par
  `EVIDENCE_GEOFENCE_ENFORCE`) ;
* les médias ne sont **jamais** servis publiquement : `/api/evidences/{id}/file/` vérifie
  l'appartenance au projet avant de délivrer la pièce (et délègue à Nginx via
  `X-Accel-Redirect` en production).
"""

from __future__ import annotations

import mimetypes
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.activity import log_event
from apps.core.exceptions import KemtaAPIError
from apps.evidences.access import accessible_evidences
from apps.evidences.models import (
    Evidence,
    EvidenceStatus,
    SyncStatus,
)
from apps.evidences.serializers import (
    EvidenceCreateSerializer,
    EvidenceSerializer,
    EvidenceTransitionSerializer,
    EvidenceValidationSerializer,
    distance_meters,
)
from apps.evidences.services import apply_transition
from apps.evidences.storage import read_and_validate_upload, sha256_of
from apps.evidences.tasks import generate_evidence_derivatives
from apps.projects.access import accessible_projects, has_project_capability
from apps.projects.models import Task
from apps.users.roles import Capability

EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def serialize(evidence: Evidence, request) -> dict:
    return EvidenceSerializer(evidence, context={"request": request, "user": request.user}).data


class EvidenceCreateView(APIView):
    """`POST /api/evidences/` — dépôt d'une preuve depuis le terrain."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        idempotency_key = request.headers.get("Idempotency-Key") or request.META.get(
            "HTTP_IDEMPOTENCY_KEY"
        )
        if not idempotency_key:
            raise KemtaAPIError(
                "idempotency_key_required",
                "L'en-tête « Idempotency-Key » est obligatoire : il évite les doublons "
                "lorsqu'un envoi est réessayé après une coupure réseau.",
            )
        if len(idempotency_key) > 64:
            raise KemtaAPIError("idempotency_key_invalid", "Clé d'idempotence trop longue.")

        # 1. Rejeu d'un envoi déjà reçu : on renvoie la preuve existante, sans rien recréer.
        replayed = (
            Evidence.objects.filter(author=request.user, idempotency_key=idempotency_key)
            .select_related("project", "author", "task")
            .first()
        )
        if replayed is not None:
            return Response(
                serialize(replayed, request),
                status=status.HTTP_200_OK,
                headers={"Idempotency-Replayed": "true"},
            )

        # 2. Périmètre : un projet hors portée produit un 404 (jamais un 403 qui le révélerait).
        project_id = request.data.get("project")
        project = get_object_or_404(accessible_projects(request.user), pk=project_id)
        if not has_project_capability(request.user, project, Capability.CAPTURE_EVIDENCE):
            raise KemtaAPIError(
                "permission_denied",
                "Votre rôle sur ce projet ne permet pas de déposer une preuve.",
                http_status=403,
            )

        # 3. Métadonnées (formes, cohérence GPS / coordonnées, tâche du bon projet).
        serializer = EvidenceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 4. Fichier : taille puis type **réel** puis dimensions.
        payload, content_type = read_and_validate_upload(data["file"])
        digest = sha256_of(payload)

        # 5. Doublon de contenu dans le même projet.
        duplicate = (
            Evidence.objects.filter(project=project, hash_sha256=digest)
            .select_related("project", "author", "task")
            .first()
        )
        if duplicate is not None:
            return Response(
                {
                    "error": {
                        "code": "duplicate_evidence",
                        "message": "Cette photo a déjà été déposée sur ce projet.",
                        "details": {"evidence": serialize(duplicate, request)},
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )

        # 6. Périmètre géographique : on refuse une preuve manifestement prise ailleurs.
        distance = None
        if (
            data.get("latitude") is not None
            and project.latitude is not None
            and project.longitude is not None
        ):
            distance = distance_meters(
                float(data["latitude"]),
                float(data["longitude"]),
                float(project.latitude),
                float(project.longitude),
            )
            if distance > project.geofence_radius_m and settings.EVIDENCE_GEOFENCE_ENFORCE:
                raise KemtaAPIError(
                    "evidence_out_of_geofence",
                    "La photo a été prise hors du périmètre du chantier "
                    f"({distance:.0f} m du site, périmètre de {project.geofence_radius_m} m).",
                    http_status=422,
                    details={
                        "distance_m": round(distance, 1),
                        "radius_m": project.geofence_radius_m,
                    },
                )

        captured_at = data["captured_at"]
        extension = EXTENSION_BY_CONTENT_TYPE.get(content_type, "jpg")
        task: Task | None = data.get("task")

        with transaction.atomic():
            evidence = Evidence(
                project=project,
                author=request.user,
                task=task,
                captured_at=captured_at,
                latitude=data.get("latitude"),
                longitude=data.get("longitude"),
                gps_accuracy=data.get("gps_accuracy"),
                gps_status=data["gps_status"],
                device_model=(data.get("device_model") or "")[:120],
                device_platform=(data.get("device_platform") or "")[:60],
                app_version=(data.get("app_version") or "")[:32],
                description=data.get("description") or "",
                hash_sha256=digest,
                idempotency_key=idempotency_key,
                size_bytes=len(payload),
                content_type=content_type,
                status=EvidenceStatus.PENDING,
                sync_status=SyncStatus.SYNCED,
            )
            evidence.file.save(f"original.{extension}", ContentFile(payload), save=False)
            evidence.save()

            log_event(
                "EVIDENCE_CAPTURED",
                actor=request.user,
                entity_type="Evidence",
                entity_id=evidence.pk,
                organization=project.organization,
                project=project,
                metadata={
                    "hash_sha256": digest[:12],  # empreinte tronquée : suffisante pour la trace
                    "size_bytes": len(payload),
                    "content_type": content_type,
                    "gps_status": evidence.gps_status,
                    "distance_m": round(distance, 1) if distance is not None else None,
                    "inside_geofence": None
                    if distance is None
                    else distance <= project.geofence_radius_m,
                    "task": task.pk if task else None,
                    "captured_at": captured_at.isoformat(),
                },
                request=request,
            )

        # 7. Dérivées hors du cycle de requête (eager en test, Celery en production).
        generate_evidence_derivatives.delay(evidence.pk)

        evidence.refresh_from_db()
        return Response(
            serialize(evidence, request),
            status=status.HTTP_201_CREATED,
            headers={"Idempotency-Replayed": "false"},
        )


class ProjectEvidenceListView(APIView):
    """`GET /api/projects/{id}/evidences/` — galerie paginée du chantier."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        project = get_object_or_404(accessible_projects(request.user), pk=pk)
        queryset = (
            Evidence.objects.filter(project=project)
            .select_related("author", "task", "project")
            .prefetch_related("validations")
        )

        status_filter = request.query_params.get("status")
        if status_filter:
            values = [value.upper() for value in status_filter.split(",") if value]
            unknown = [value for value in values if value not in EvidenceStatus.values]
            if unknown:
                raise KemtaAPIError(
                    "invalid_status", "Statut inconnu.", details={"unknown": unknown}
                )
            queryset = queryset.filter(status__in=values)

        sync_filter = request.query_params.get("sync_status")
        if sync_filter:
            queryset = queryset.filter(sync_status=sync_filter.upper())

        author = request.query_params.get("author")
        if author:
            queryset = queryset.filter(author_id=author)

        task = request.query_params.get("task")
        if task:
            queryset = queryset.filter(task_id=task)

        pending_only = request.query_params.get("pending")
        if pending_only in {"1", "true", "True"}:
            queryset = queryset.filter(status=EvidenceStatus.PENDING)

        from apps.core.pagination import DefaultPagination

        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset.order_by("-captured_at", "-id"), request)
        serializer = EvidenceSerializer(
            page, many=True, context={"request": request, "user": request.user}
        )
        payload = paginator.get_paginated_response(serializer.data).data
        payload["counts"] = {
            "pending": Evidence.objects.filter(
                project=project, status=EvidenceStatus.PENDING
            ).count(),
            "validated": Evidence.objects.filter(
                project=project, status=EvidenceStatus.VALIDATED
            ).count(),
            "rejected": Evidence.objects.filter(
                project=project, status=EvidenceStatus.REJECTED
            ).count(),
            "flagged": Evidence.objects.filter(
                project=project, status=EvidenceStatus.FLAGGED
            ).count(),
        }
        return Response(payload)


class EvidenceDetailView(APIView):
    """`GET /api/evidences/{id}/` — détail et historique de validation."""

    permission_classes = [IsAuthenticated]

    def get_evidence(self, request, pk) -> Evidence:
        return get_object_or_404(
            accessible_evidences(request.user).prefetch_related("validations__actor"), pk=pk
        )

    def get(self, request, pk):
        evidence = self.get_evidence(request, pk)
        payload = serialize(evidence, request)
        return Response(payload)


class EvidenceHistoryView(APIView):
    """`GET /api/evidences/{id}/history/` — historique paginé (append-only)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        evidence = get_object_or_404(accessible_evidences(request.user), pk=pk)
        validations = evidence.validations.select_related("actor").order_by("created_at", "id")
        return Response(
            {
                "count": validations.count(),
                "results": EvidenceValidationSerializer(validations, many=True).data,
            }
        )


class EvidenceTransitionView(APIView):
    """`POST /api/evidences/{id}/transition/` — valider, rejeter, signaler, rouvrir.

    La règle métier vit dans `apps.evidences.services.apply_transition` : le même service sert
    la reprise hors ligne (`/api/sync/batch/`), donc aucune divergence possible entre les deux.
    """

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        evidence = get_object_or_404(
            Evidence.objects.filter(project__in=accessible_projects(request.user)).select_related(
                "project", "author"
            ),
            pk=pk,
        )
        serializer = EvidenceTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        apply_transition(
            evidence=evidence,
            actor=request.user,
            action=serializer.validated_data["action"],
            comment=serializer.validated_data.get("comment") or "",
            request=request,
        )

        payload = serialize(evidence, request)
        payload["last_validation"] = EvidenceValidationSerializer(
            evidence.validations.order_by("-created_at").first()
        ).data
        return Response(payload, status=status.HTTP_200_OK)


class EvidenceFileView(APIView):
    """`GET /api/evidences/{id}/file/` et `/thumbnail/` — accès contrôlé aux médias.

    Le fichier n'est jamais exposé par une URL publique : l'appartenance au projet est
    vérifiée à chaque requête. En production, `MEDIA_X_ACCEL_REDIRECT` délègue l'envoi à
    Nginx (la réponse est alors un `X-Accel-Redirect` vide de contenu).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk, variant: str):
        evidence = get_object_or_404(accessible_evidences(request.user), pk=pk)
        if variant == "thumbnail" and evidence.thumbnail:
            field = evidence.thumbnail
            # La miniature peut être un WebP (ou un JPEG de repli) : c'est son extension qui
            # fait foi, jamais le type de la photo d'origine.
            content_type = (
                mimetypes.guess_type(field.name)[0]
                or evidence.content_type
                or "application/octet-stream"
            )
        else:
            field = evidence.file
            content_type = (
                evidence.content_type
                or mimetypes.guess_type(field.name)[0]
                or "application/octet-stream"
            )

        if not field:
            raise KemtaAPIError("file_not_available", "Fichier indisponible.", http_status=404)

        if settings.MEDIA_X_ACCEL_REDIRECT:
            response = HttpResponse(status=200)
            response["X-Accel-Redirect"] = f"/protected-media/{field.name}"
            response["Content-Type"] = content_type
            return response

        response = FileResponse(field.open("rb"), content_type=content_type)
        response["Content-Disposition"] = f'inline; filename="{field.name.rsplit("/", 1)[-1]}"'
        # Les preuves sont privées : aucun cache partagé ne doit les conserver.
        response["Cache-Control"] = "private, max-age=300"
        return response


class EvidencePendingCountView(APIView):
    """`GET /api/evidences/pending/` — file d'attente du validateur, tous projets confondus.

    Sert directement l'écran « À valider » : le validateur voit ce qui l'attend sans ouvrir
    chaque projet, et la limite de temps (`?older_than_hours=`) met en avant les preuves qui
    traînent.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = accessible_evidences(request.user).filter(status=EvidenceStatus.PENDING)
        older_than = request.query_params.get("older_than_hours")
        if older_than:
            try:
                hours = int(older_than)
            except ValueError as exc:
                raise KemtaAPIError(
                    "invalid_parameter", "« older_than_hours » doit être un entier."
                ) from exc
            queryset = queryset.filter(captured_at__lt=timezone.now() - timedelta(hours=hours))

        # Seules les preuves que l'utilisateur a le droit de valider (capacité calculée).
        validatable = [
            evidence
            for evidence in queryset.order_by("captured_at")
            if has_project_capability(request.user, evidence.project, Capability.VALIDATE_EVIDENCE)
            and evidence.author_id != request.user.pk
        ]
        serializer = EvidenceSerializer(
            validatable, many=True, context={"request": request, "user": request.user}
        )
        return Response({"count": len(serializer.data), "results": serializer.data})
