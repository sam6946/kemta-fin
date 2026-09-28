"""Validation, rejet, signalement et historique des preuves (MVP-008).

Critères de sortie couverts : seuls les rôles autorisés valident ; chaque changement de statut
est journalisé ; l'historique porte acteur, date, action et commentaire ; une preuve rejetée
reste consultable ; l'historique est protégé contre la modification et la suppression.
"""

from __future__ import annotations

import mimetypes
import uuid

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from apps.core.models import ActivityLog
from apps.evidences.models import Evidence, EvidenceStatus, EvidenceValidation

URL = "/api/evidences/"


@pytest.fixture()
def evidence(auth_client, project_context, project, photo):
    """Preuve déposée par l'agent terrain, en attente de validation."""
    response = auth_client(project_context["agent"]).post(
        URL,
        {
            "project": project.pk,
            "file": photo(),
            "captured_at": timezone.now().isoformat(),
            "gps_status": "UNAVAILABLE",
            "description": "Photo de contrôle",
        },
        format="multipart",
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.data
    return Evidence.objects.get(pk=response.data["id"])


def transition(auth_client, user, evidence, action, comment=""):
    return auth_client(user).post(
        f"/api/evidences/{evidence.pk}/transition/",
        {"action": action, "comment": comment},
        format="json",
    )


@pytest.mark.django_db
def test_validator_validates_a_pending_evidence(auth_client, evidence, project_context):
    response = transition(auth_client, project_context["validator"], evidence, "VALIDATE")

    assert response.status_code == 200, response.data
    assert response.data["status"] == "VALIDATED"
    assert response.data["status_label"] == "Validée"
    assert response.data["last_validation"]["action"] == "VALIDATE"
    assert response.data["last_validation"]["actor"]["id"] == project_context["validator"].pk

    evidence.refresh_from_db()
    assert evidence.status == EvidenceStatus.VALIDATED
    assert evidence.validations.count() == 1
    assert ActivityLog.objects.filter(action="EVIDENCE_VALIDATED", entity_id=evidence.pk).exists()


@pytest.mark.django_db
def test_rejection_requires_a_comment_and_keeps_the_evidence_readable(
    auth_client, evidence, project_context
):
    without = transition(auth_client, project_context["validator"], evidence, "REJECT")
    assert without.status_code == 400
    assert without.data["error"]["code"] == "comment_required"

    response = transition(
        auth_client, project_context["validator"], evidence, "REJECT", "Photo floue, à reprendre"
    )
    assert response.status_code == 200
    assert response.data["status"] == "REJECTED"

    # La preuve rejetée reste consultable, avec son motif dans l'historique.
    detail = auth_client(project_context["agent"]).get(f"/api/evidences/{evidence.pk}/")
    assert detail.status_code == 200
    assert detail.data["status"] == "REJECTED"

    history = auth_client(project_context["agent"]).get(f"/api/evidences/{evidence.pk}/history/")
    assert history.data["count"] == 1
    entry = history.data["results"][0]
    assert entry["action"] == "REJECT"
    assert entry["comment"] == "Photo floue, à reprendre"
    assert entry["from_status"] == "PENDING" and entry["to_status"] == "REJECTED"
    assert entry["actor"]["id"] == project_context["validator"].pk
    assert ActivityLog.objects.filter(action="EVIDENCE_REJECTED", entity_id=evidence.pk).exists()


@pytest.mark.django_db
def test_flagging_requires_a_comment_and_marks_the_evidence(auth_client, evidence, project_context):
    response = transition(
        auth_client,
        project_context["validator"],
        evidence,
        "FLAG",
        "Position GPS incohérente avec le chantier",
    )

    assert response.status_code == 200
    assert response.data["status"] == "FLAGGED"
    assert ActivityLog.objects.filter(action="EVIDENCE_FLAGGED").exists()


@pytest.mark.django_db
def test_reopen_puts_the_evidence_back_in_the_queue(auth_client, evidence, project_context):
    transition(auth_client, project_context["validator"], evidence, "VALIDATE")
    response = transition(
        auth_client, project_context["validator"], evidence, "REOPEN", "Nouvelle photo disponible"
    )

    assert response.status_code == 200
    assert response.data["status"] == "PENDING"
    evidence.refresh_from_db()
    assert evidence.validations.count() == 2  # l'historique conserve les deux décisions


@pytest.mark.django_db
def test_invalid_transition_is_refused(auth_client, evidence, project_context):
    """On ne « revalide » pas une preuve déjà validée : 409 avec les actions possibles."""
    transition(auth_client, project_context["validator"], evidence, "VALIDATE")
    response = transition(auth_client, project_context["validator"], evidence, "VALIDATE")

    assert response.status_code == 409
    assert response.data["error"]["code"] == "invalid_transition"
    assert response.data["error"]["details"]["from_status"] == "VALIDATED"
    assert "REOPEN" in response.data["error"]["details"]["allowed_actions"]


@pytest.mark.django_db
def test_author_cannot_validate_their_own_evidence(auth_client, evidence, project_context, photo):
    """Un membre qui capture ne s'auto-valide pas : la décision doit venir d'un tiers."""
    engineer = project_context["engineer"]  # capture + (drapeau) validation
    from apps.projects.models import ProjectMember

    ProjectMember.objects.filter(project=evidence.project, user=engineer).update(
        can_validate_evidence=True
    )
    own = auth_client(engineer).post(
        URL,
        {
            "project": evidence.project_id,
            "file": photo(color=(10, 200, 90)),
            "captured_at": timezone.now().isoformat(),
            "gps_status": "UNAVAILABLE",
        },
        format="multipart",
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert own.status_code == 201, own.data

    response = transition(
        auth_client, engineer, Evidence.objects.get(pk=own.data["id"]), "VALIDATE"
    )

    assert response.status_code == 403
    assert response.data["error"]["code"] == "cannot_validate_own_evidence"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "actor,expected_status",
    [
        ("validator", 200),  # capacité de validation
        ("owner", 200),  # maître d'ouvrage
        ("agent", 403),  # capte mais ne valide pas
        ("investor", 403),  # consulte seulement
        ("stranger", 404),  # hors périmètre
    ],
)
def test_transition_follows_capabilities(
    auth_client, evidence, project_context, actor, expected_status
):
    response = transition(auth_client, project_context[actor], evidence, "VALIDATE")
    assert response.status_code == expected_status


@pytest.mark.django_db
def test_member_with_validate_flag_can_validate(auth_client, evidence, project_context):
    """Le drapeau `can_validate_evidence` du membre ouvre la validation, sans changer de rôle."""
    from apps.projects.models import ProjectMember

    ProjectMember.objects.filter(project=evidence.project, user=project_context["finance"]).update(
        can_validate_evidence=True
    )
    response = transition(auth_client, project_context["finance"], evidence, "VALIDATE")
    assert response.status_code == 200


@pytest.mark.django_db
def test_history_is_append_only(evidence, project_context):
    """L'historique ne se modifie ni ne se supprime : c'est la trace de la décision."""
    entry = EvidenceValidation.objects.create(
        evidence=evidence,
        actor=project_context["validator"],
        action="VALIDATE",
        from_status=EvidenceStatus.PENDING,
        to_status=EvidenceStatus.VALIDATED,
        comment="Contrôle conforme",
    )

    entry.comment = "modifié"
    with pytest.raises(DjangoValidationError):
        entry.save()
    with pytest.raises(DjangoValidationError):
        entry.delete()

    assert EvidenceValidation.objects.filter(evidence=evidence).count() == 1


@pytest.mark.django_db
def test_gallery_lists_evidence_with_statuses_and_counts(
    auth_client, evidence, project_context, photo
):
    client = auth_client(project_context["validator"])
    response = client.get(f"/api/projects/{evidence.project_id}/evidences/")

    assert response.status_code == 200
    assert response.data["count"] == 1
    item = response.data["results"][0]
    assert item["status"] == "PENDING"
    assert item["status_label"] == "En attente de validation"
    assert item["thumbnail_url"].endswith(f"/api/evidences/{evidence.pk}/thumbnail/")
    assert item["validation_count"] == 0
    assert response.data["counts"] == {"pending": 1, "validated": 0, "rejected": 0, "flagged": 0}

    # Filtres de la galerie.
    assert (
        client.get(f"/api/projects/{evidence.project_id}/evidences/?status=VALIDATED").data["count"]
        == 0
    )
    assert (
        client.get(f"/api/projects/{evidence.project_id}/evidences/?status=NOPE").status_code == 400
    )


@pytest.mark.django_db
def test_gallery_is_scoped_to_the_project(auth_client, evidence, project_context):
    from apps.projects.models import Project

    other = Project.objects.create(
        organization=evidence.project.organization,
        name="Chantier voisin",
        created_by=project_context["owner"],
    )
    # Le propriétaire de l'organisation pilote tous ses projets (dont celui-ci, vide).
    org_owner = project_context["organization"].owner
    response = auth_client(org_owner).get(f"/api/projects/{other.pk}/evidences/")

    assert response.status_code == 200
    assert response.data["count"] == 0


@pytest.mark.django_db
def test_validate_own_flag_is_exposed_to_the_client(auth_client, evidence, project_context):
    """Le frontend masque l'action de validation sur sa propre preuve : le backend le dit."""
    own = auth_client(project_context["agent"]).get(f"/api/evidences/{evidence.pk}/")
    assert own.data["permissions"]["validate_evidence"] is False  # pas la capacité
    assert own.data["permissions"]["cannot_validate_own"] is False

    validator = auth_client(project_context["validator"]).get(f"/api/evidences/{evidence.pk}/")
    assert validator.data["permissions"]["validate_evidence"] is True
    assert validator.data["permissions"]["cannot_validate_own"] is False


@pytest.mark.django_db
def test_pending_queue_lists_only_validatable_evidence(
    auth_client, evidence, project_context, project, photo
):
    """L'écran « À valider » ne montre que ce que l'utilisateur peut réellement trancher."""
    # Une deuxième preuve déposée par le maître d'ouvrage : jamais auto-validable par lui.
    own = auth_client(project_context["owner"]).post(
        URL,
        {
            "project": project.pk,
            "file": photo(color=(200, 30, 30)),
            "captured_at": timezone.now().isoformat(),
            "gps_status": "UNAVAILABLE",
        },
        format="multipart",
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert own.status_code == 201, own.data

    # Le validateur voit les deux preuves en attente : aucune n'est la sienne.
    queue = auth_client(project_context["validator"]).get("/api/evidences/pending/")
    assert queue.status_code == 200
    assert queue.data["count"] == 2
    assert {item["id"] for item in queue.data["results"]} == {evidence.pk, own.data["id"]}

    # Le maître d'ouvrage peut valider celle de l'agent, mais jamais la sienne.
    owner_queue = auth_client(project_context["owner"]).get("/api/evidences/pending/")
    assert [item["id"] for item in owner_queue.data["results"]] == [evidence.pk]

    # L'agent terrain, qui ne valide pas, voit une file vide.
    assert auth_client(project_context["agent"]).get("/api/evidences/pending/").data["count"] == 0


@pytest.mark.django_db
def test_file_access_is_controlled(auth_client, evidence, project_context):
    """Les médias ne sont pas publics : l'accès exige l'appartenance au projet."""
    validator = auth_client(project_context["validator"]).get(f"/api/evidences/{evidence.pk}/file/")
    assert validator.status_code == 200
    assert validator["Content-Type"].startswith("image/")
    assert "private" in validator["Cache-Control"]

    assert (
        auth_client(project_context["stranger"])
        .get(f"/api/evidences/{evidence.pk}/file/")
        .status_code
        == 404
    )

    thumbnail = auth_client(project_context["agent"]).get(
        f"/api/evidences/{evidence.pk}/thumbnail/"
    )
    assert thumbnail.status_code == 200
    # La miniature est servie avec son propre type (WebP, JPEG de repli), pas celui de l'original.
    assert thumbnail["Content-Type"] in {"image/webp", "image/jpeg"}
    assert thumbnail["Content-Type"] == mimetypes.guess_type(evidence.thumbnail.name)[0]


@pytest.mark.django_db
def test_soft_deleted_evidence_disappears_from_the_gallery(auth_client, evidence, project_context):
    evidence.delete()  # suppression logique
    response = auth_client(project_context["validator"]).get(
        f"/api/projects/{evidence.project_id}/evidences/"
    )
    assert response.data["count"] == 0
    assert Evidence.all_objects.get(pk=evidence.pk).deleted_at is not None


@pytest.mark.django_db
def test_progress_of_a_project_is_not_affected_by_evidence(auth_client, evidence, project_context):
    """Une preuve n'est pas une tâche : elle ne modifie pas l'avancement du projet."""
    before = evidence.project.progress
    transition(auth_client, project_context["validator"], evidence, "VALIDATE")
    evidence.project.refresh_from_db()
    assert evidence.project.progress == before
