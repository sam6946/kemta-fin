"""Dépôt d'une preuve terrain : fichiers, GPS, idempotence, doublons (MVP-007).

Critères de sortie couverts : une preuve contient projet, auteur, horodatage et statut ;
le serveur valide les métadonnées ; le hash est calculé et enregistré ; le GPS absent est
géré explicitement ; les fichiers non conformes sont refusés.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from apps.core.models import ActivityLog
from apps.evidences.models import Evidence, GpsStatus
from apps.evidences.storage import sha256_of
from apps.users.roles import Role

URL = "/api/evidences/"


def _place_project(project, *, latitude: str, longitude: str, radius_m: int = 500):
    """Positionne le chantier sans modifier la fabrique partagée de `conftest.py`."""
    from apps.projects.models import Project

    Project.objects.filter(pk=project.pk).update(
        latitude=latitude, longitude=longitude, geofence_radius_m=radius_m
    )
    project.refresh_from_db()


def post(auth_client, user, payload, key=None, **extra):
    """Dépôt multipart avec la clé d'idempotence attendue par le contrat."""
    return auth_client(user).post(
        URL,
        payload,
        format="multipart",
        headers={"Idempotency-Key": key or uuid.uuid4().hex},
        **extra,
    )


@pytest.mark.django_db
def test_agent_captures_a_photo_with_gps(auth_client, project, project_context, capture_payload):
    payload = capture_payload(
        latitude="4.089100",
        longitude="9.740600",
        gps_accuracy=8.5,
        gps_status=GpsStatus.AVAILABLE,
        device_model="Tecno Spark 10",
        device_platform="Android 13",
        app_version="0.5.0",
        description="Ferraillage avant coulage",
    )

    response = post(auth_client, project_context["agent"], payload)

    assert response.status_code == 201, response.data
    assert response.data["status"] == "PENDING"
    assert response.data["status_label"] == "En attente de validation"
    assert response.data["sync_status"] == "SYNCED"
    assert response.data["gps_status"] == "AVAILABLE"
    assert response.data["author"]["id"] == project_context["agent"].pk
    assert response.data["project"] == project.pk
    assert response.data["description"] == "Ferraillage avant coulage"
    assert len(response.data["hash_sha256"]) == 64
    assert response.data["device_model"] == "Tecno Spark 10"
    # Chemins **relatifs** : derrière un proxy, une URL absolue renverrait le navigateur du
    # terrain vers l'hôte interne de l'API.
    assert response.data["file_url"] == f"/api/evidences/{response.data['id']}/file/"
    assert response.data["thumbnail_url"] == f"/api/evidences/{response.data['id']}/thumbnail/"

    evidence = Evidence.objects.get(pk=response.data["id"])
    # Le chemin est régénéré côté serveur : le nom envoyé par le client est ignoré.
    assert evidence.file.name.startswith(f"evidences/{project.pk}/")
    assert "chantier" not in evidence.file.name
    # Les dérivées sont produites (eager en test) : miniature et version liste présentes.
    assert evidence.thumbnail and evidence.list_version
    with Image.open(evidence.thumbnail.path) as thumb:
        assert max(thumb.size) <= 320

    event = ActivityLog.objects.get(action="EVIDENCE_CAPTURED", entity_id=evidence.pk)
    assert event.project == project and event.organization == project.organization
    assert event.metadata["hash_sha256"] == evidence.hash_sha256[:12]  # jamais l'empreinte entière


@pytest.mark.django_db
def test_hash_is_computed_server_side(auth_client, project_context, photo, project):
    """L'empreinte enregistrée est celle du fichier reçu, pas une valeur déclarée."""
    raw = photo()
    expected = sha256_of(raw.read())
    raw.seek(0)

    response = post(
        auth_client,
        project_context["agent"],
        {
            "project": project.pk,
            "file": raw,
            "captured_at": timezone.now().isoformat(),
            "gps_status": "UNAVAILABLE",
        },
    )

    assert response.status_code == 201, response.data
    assert response.data["hash_sha256"] == expected


@pytest.mark.django_db
def test_gps_absent_is_recorded_explicitly(auth_client, project_context, capture_payload):
    """Sans GPS, la preuve part quand même : le statut le dit, rien n'est inventé."""
    response = post(
        auth_client,
        project_context["agent"],
        capture_payload(gps_status=GpsStatus.UNAVAILABLE),
    )

    assert response.status_code == 201, response.data
    assert response.data["gps_status"] == "UNAVAILABLE"
    assert response.data["gps_status_label"] == "Indisponible"
    assert response.data["latitude"] is None
    assert response.data["distance_from_site_m"] is None
    assert response.data["inside_geofence"] is None  # indéterminable, pas « faux »


@pytest.mark.django_db
def test_gps_denied_is_accepted_and_traceable(
    auth_client, project_context, capture_payload, project
):
    response = post(
        auth_client, project_context["agent"], capture_payload(gps_status=GpsStatus.DENIED)
    )

    assert response.status_code == 201, response.data
    assert response.data["gps_status"] == "DENIED"
    assert Evidence.objects.get(pk=response.data["id"]).gps_status == "DENIED"


@pytest.mark.django_db
def test_partial_coordinates_are_rejected(auth_client, project_context, capture_payload):
    response = post(auth_client, project_context["agent"], capture_payload(latitude="4.0891"))

    assert response.status_code == 400
    assert "latitude" in response.data["error"]["details"]
    assert "longitude" in response.data["error"]["details"]


@pytest.mark.django_db
def test_coordinates_out_of_range_are_rejected(auth_client, project_context, capture_payload):
    response = post(
        auth_client,
        project_context["agent"],
        capture_payload(latitude="120.0", longitude="9.74", gps_status=GpsStatus.AVAILABLE),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_idempotency_key_is_required(auth_client, project_context, capture_payload):
    response = auth_client(project_context["agent"]).post(
        URL, capture_payload(), format="multipart"
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "idempotency_key_required"


@pytest.mark.django_db
def test_replayed_upload_returns_the_same_evidence(
    auth_client, project_context, capture_payload, photo
):
    """Une coupure réseau au moment de la réponse ne doit pas créer de doublon."""
    key = uuid.uuid4().hex
    payload = capture_payload(file=photo())
    first = post(auth_client, project_context["agent"], payload, key=key)
    # Deuxième envoi : même clé, **fichier différent** (le terrain a repris une photo).
    second = post(auth_client, project_context["agent"], capture_payload(), key=key)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotency-Replayed"] == "true"
    assert second.data["id"] == first.data["id"]
    assert Evidence.objects.count() == 1


@pytest.mark.django_db
def test_duplicate_content_in_the_same_project_returns_the_existing_evidence(
    auth_client, project_context, capture_payload
):
    """Le même cliché envoyé deux fois n'entre pas deux fois dans la galerie."""
    first = post(auth_client, project_context["agent"], capture_payload())
    assert first.status_code == 201

    second = post(auth_client, project_context["engineer"], capture_payload())

    assert second.status_code == 409
    assert second.data["error"]["code"] == "duplicate_evidence"
    assert second.data["error"]["details"]["evidence"]["id"] == first.data["id"]
    assert Evidence.objects.count() == 1


@pytest.mark.django_db
def test_unknown_file_type_is_rejected(auth_client, project_context, capture_payload):
    """Un PDF renommé « .jpg » est refusé : le type est lu dans le contenu, pas dans le nom."""
    fake = SimpleUploadedFile("photo.jpg", b"%PDF-1.7\ncontrefacon\n", content_type="image/jpeg")

    response = post(auth_client, project_context["agent"], capture_payload(file=fake))

    assert response.status_code == 415
    assert response.data["error"]["code"] == "unsupported_media_type"


@pytest.mark.django_db
def test_oversized_file_is_rejected(auth_client, project_context, capture_payload, settings):
    settings.MAX_UPLOAD_SIZE_MB = 1
    big = SimpleUploadedFile("gros.jpg", b"x" * (1024 * 1024 + 10), content_type="image/jpeg")

    response = post(auth_client, project_context["agent"], capture_payload(file=big))

    assert response.status_code == 413
    assert response.data["error"]["code"] == "file_too_large"


@pytest.mark.django_db
def test_too_large_image_is_rejected(auth_client, project_context, capture_payload, photo):
    response = post(
        auth_client, project_context["agent"], capture_payload(file=photo(width=4200, height=100))
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "image_dimensions_too_large"


@pytest.mark.django_db
def test_empty_file_is_rejected(auth_client, project_context, capture_payload):
    empty = SimpleUploadedFile("vide.jpg", b"", content_type="image/jpeg")
    response = post(auth_client, project_context["agent"], capture_payload(file=empty))

    assert response.status_code in {400, 415}
    assert response.data["error"]["code"] in {
        "file_empty",
        "unsupported_media_type",
        "validation_error",  # DRF refuse un fichier vide avant d'atteindre le service
    }


@pytest.mark.django_db
def test_capture_out_of_geofence_is_refused(auth_client, project_context, capture_payload, project):
    """GPS disponible et à des kilomètres du chantier : refus explicite, code dédié."""
    _place_project(project, latitude="4.089100", longitude="9.740600")
    response = post(
        auth_client,
        project_context["agent"],
        capture_payload(latitude="3.848000", longitude="11.502000", gps_status=GpsStatus.AVAILABLE),
    )

    assert response.status_code == 422
    assert response.data["error"]["code"] == "evidence_out_of_geofence"
    assert response.data["error"]["details"]["distance_m"] > project.geofence_radius_m
    assert Evidence.objects.count() == 0


@pytest.mark.django_db
def test_capture_far_away_is_accepted_when_the_check_is_relaxed(
    auth_client, project_context, capture_payload, settings, project
):
    """Le contrôle peut être assoupli en exploitation : la preuve est alors traçable."""
    settings.EVIDENCE_GEOFENCE_ENFORCE = False
    _place_project(project, latitude="4.089100", longitude="9.740600")
    response = post(
        auth_client,
        project_context["agent"],
        capture_payload(latitude="3.848000", longitude="11.502000", gps_status=GpsStatus.AVAILABLE),
    )

    assert response.status_code == 201, response.data
    assert response.data["inside_geofence"] is False
    assert response.data["distance_from_site_m"] > project.geofence_radius_m


@pytest.mark.django_db
def test_capture_near_the_site_is_inside_the_geofence(
    auth_client, project_context, capture_payload, project
):
    _place_project(project, latitude="4.089100", longitude="9.740600")
    response = post(
        auth_client,
        project_context["agent"],
        capture_payload(latitude="4.089300", longitude="9.740800", gps_status=GpsStatus.AVAILABLE),
    )

    assert response.status_code == 201, response.data
    assert response.data["inside_geofence"] is True
    # Quelques dizaines de mètres : largement à l'intérieur du périmètre de 500 m.
    assert response.data["distance_from_site_m"] < 100


@pytest.mark.django_db
def test_timestamp_in_the_future_is_refused(auth_client, project_context, capture_payload):
    response = post(
        auth_client,
        project_context["agent"],
        capture_payload(captured_at=(timezone.now() + timedelta(hours=3)).isoformat()),
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "captured_at_in_future"


@pytest.mark.django_db
def test_task_must_belong_to_the_project(auth_client, project_context, capture_payload, make_user):
    from apps.projects.models import Project, Task

    other_project = Project.objects.create(
        organization=project_context["organization"],
        name="Projet voisin",
        created_by=project_context["owner"],
    )
    foreign_task = Task.objects.create(
        project=other_project,
        title="Tâche d'un autre chantier",
        created_by=make_user(Role.ENGINEER, phone_number="+237699555111"),
    )

    response = post(auth_client, project_context["agent"], capture_payload(task=foreign_task.pk))

    assert response.status_code == 400
    assert "task" in response.data["error"]["details"]


@pytest.mark.django_db
def test_capture_on_a_foreign_project_is_invisible(auth_client, project_context, capture_payload):
    """Un non-membre obtient 404 : il ne peut ni déposer ni deviner le projet."""
    response = post(auth_client, project_context["stranger"], capture_payload())

    assert response.status_code == 404
    assert Evidence.objects.count() == 0


@pytest.mark.django_db
def test_role_without_capture_capability_is_rejected(auth_client, project_context, capture_payload):
    """Le validateur ne capture pas : il contrôle (403 explicite sur un projet visible)."""
    response = post(auth_client, project_context["validator"], capture_payload())

    assert response.status_code == 403
    assert response.data["error"]["code"] == "permission_denied"
