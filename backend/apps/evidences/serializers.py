"""Sérialiseurs des preuves terrain (MVP-007, MVP-008)."""

from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from apps.core.exceptions import KemtaAPIError
from apps.evidences.models import Evidence, EvidenceValidation, GpsStatus
from apps.projects.access import has_project_capability
from apps.projects.models import Project, Task
from apps.users.roles import Capability
from apps.users.serializers import UserSerializer


def distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique (Haversine) : suffisante pour un contrôle de périmètre."""
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


class EvidenceValidationSerializer(serializers.ModelSerializer):
    actor = UserSerializer(read_only=True)
    action_label = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = EvidenceValidation
        fields = [
            "id",
            "actor",
            "action",
            "action_label",
            "from_status",
            "to_status",
            "comment",
            "created_at",
        ]
        read_only_fields = fields


class EvidenceSerializer(serializers.ModelSerializer):
    """Lecture d'une preuve : le client n'a rien à recalculer (URL, statut, permissions)."""

    author = UserSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    sync_status_label = serializers.CharField(source="get_sync_status_display", read_only=True)
    gps_status_label = serializers.CharField(source="get_gps_status_display", read_only=True)
    task_title = serializers.CharField(source="task.title", read_only=True, default=None)
    file_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    distance_from_site_m = serializers.SerializerMethodField()
    inside_geofence = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()
    validation_count = serializers.SerializerMethodField()

    class Meta:
        model = Evidence
        fields = [
            "id",
            "project",
            "task",
            "task_title",
            "author",
            "status",
            "status_label",
            "sync_status",
            "sync_status_label",
            "captured_at",
            "received_at",
            "latitude",
            "longitude",
            "gps_accuracy",
            "gps_status",
            "gps_status_label",
            "device_model",
            "device_platform",
            "app_version",
            "description",
            "hash_sha256",
            "size_bytes",
            "content_type",
            "file_url",
            "thumbnail_url",
            "distance_from_site_m",
            "inside_geofence",
            "validation_count",
            "permissions",
            "created_at",
        ]

    def _url(self, name: str, obj: Evidence) -> str:
        """Chemin **relatif** : l'application cliente le résout sur son propre hôte.

        Une URL absolue construite depuis la requête casserait l'accès aux fichiers derrière un
        proxy (le navigateur du terrain n'est jamais sur le domaine interne de l'API).
        """
        return f"/api/evidences/{obj.pk}/{name}/"

    def get_file_url(self, obj: Evidence) -> str:
        return self._url("file", obj)

    def get_thumbnail_url(self, obj: Evidence) -> str:
        """Les listes utilisent la miniature ; tant qu'elle n'est pas prête, l'original sert."""
        return self._url("thumbnail" if obj.thumbnail else "file", obj)

    def _distance(self, obj: Evidence) -> float | None:
        project: Project = obj.project
        if (
            obj.latitude is None
            or obj.longitude is None
            or project.latitude is None
            or project.longitude is None
        ):
            return None
        return round(
            distance_meters(
                float(obj.latitude),
                float(obj.longitude),
                float(project.latitude),
                float(project.longitude),
            ),
            1,
        )

    def get_distance_from_site_m(self, obj: Evidence) -> float | None:
        return self._distance(obj)

    def get_inside_geofence(self, obj: Evidence) -> bool | None:
        """`None` = indéterminable (pas de GPS) : l'UI affiche alors « position inconnue »."""
        distance = self._distance(obj)
        if distance is None:
            return None
        return distance <= obj.project.geofence_radius_m

    def get_permissions(self, obj: Evidence) -> dict:
        """Capacités **calculées par le backend** pour cette preuve et cet utilisateur."""
        user = self.context.get("user")
        if user is None:
            return {}
        may_validate = has_project_capability(user, obj.project, Capability.VALIDATE_EVIDENCE)
        is_author = obj.author_id == getattr(user, "pk", None)
        return {
            "validate_evidence": bool(may_validate and not is_author),
            "cannot_validate_own": bool(may_validate and is_author),
            "can_see_location": bool(
                may_validate
                or has_project_capability(user, obj.project, Capability.VIEW_ACTIVITY)
                or is_author
            ),
        }

    def get_validation_count(self, obj: Evidence) -> int:
        return obj.validations.count()


class EvidenceCreateSerializer(serializers.Serializer):
    """Entrée d'un upload : fichier + métadonnées de terrain.

    Le fichier est validé dans la vue (taille, type réel, dimensions) ; ici on ne contrôle que
    la forme des métadonnées, de sorte que les codes d'erreur restent explicites.
    """

    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    task = serializers.PrimaryKeyRelatedField(
        queryset=Task.objects.all(), required=False, allow_null=True
    )
    file = serializers.FileField()
    captured_at = serializers.DateTimeField()
    latitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True
    )
    longitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True
    )
    gps_accuracy = serializers.FloatField(required=False, allow_null=True, min_value=0)
    gps_status = serializers.ChoiceField(choices=GpsStatus.choices, required=False)
    device_model = serializers.CharField(max_length=120, required=False, allow_blank=True)
    device_platform = serializers.CharField(max_length=60, required=False, allow_blank=True)
    app_version = serializers.CharField(max_length=32, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)

    def validate_captured_at(self, value):
        now = timezone.now()
        # Tolérance d'horloge : un téléphone mal réglé ne doit pas bloquer le chantier, mais un
        # horodatage manifestement futur est refusé (il fausserait l'ordre des preuves).
        if value > now + timedelta(minutes=10):
            raise KemtaAPIError(
                "captured_at_in_future",
                "L'horodatage de la photo est dans le futur : vérifiez l'heure du téléphone.",
                details={"server_time": now.isoformat()},
            )
        return value

    def validate(self, attrs):
        latitude = attrs.get("latitude")
        longitude = attrs.get("longitude")

        # Bornes géographiques : vérifiées ici pour produire un 400 lisible (le modèle garde
        # la même règle en défense en profondeur).
        if latitude is not None and not (Decimal("-90") <= latitude <= Decimal("90")):
            raise serializers.ValidationError(
                {"latitude": ["La latitude doit être comprise entre -90 et 90."]}
            )
        if longitude is not None and not (Decimal("-180") <= longitude <= Decimal("180")):
            raise serializers.ValidationError(
                {"longitude": ["La longitude doit être comprise entre -180 et 180."]}
            )

        if (latitude is None) != (longitude is None):
            raise serializers.ValidationError(
                {
                    "latitude": ["Latitude et longitude doivent être fournies ensemble."],
                    "longitude": ["Latitude et longitude doivent être fournies ensemble."],
                }
            )

        task = attrs.get("task")
        project = attrs["project"]
        if task is not None and task.project_id != project.pk:
            raise serializers.ValidationError(
                {"task": ["La tâche doit appartenir au projet de la preuve."]}
            )

        gps_status = attrs.get("gps_status")
        if latitude is not None and gps_status in {GpsStatus.DENIED, GpsStatus.UNAVAILABLE}:
            raise serializers.ValidationError(
                {"gps_status": ["Des coordonnées sont fournies : le GPS est donc disponible."]}
            )
        if gps_status is None:
            attrs["gps_status"] = (
                GpsStatus.AVAILABLE if latitude is not None else GpsStatus.UNAVAILABLE
            )
        return attrs


class EvidenceTransitionSerializer(serializers.Serializer):
    """Action de validation : `VALIDATE`, `REJECT`, `FLAG` ou `REOPEN`."""

    action = serializers.ChoiceField(choices=["VALIDATE", "REJECT", "FLAG", "REOPEN"])
    comment = serializers.CharField(required=False, allow_blank=True, max_length=2000)
