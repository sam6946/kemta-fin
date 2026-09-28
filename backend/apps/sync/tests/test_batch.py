"""Lot de synchronisation : application, idempotence, conflits et isolation (MVP-009).

Critères de sortie couverts : une même clé produit un seul effet ; le rejeu renvoie le résultat
d'origine ; les refus sont classés (CONFLICT / FAILED) sans jamais faire échouer le reste du lot ;
une opération refusée libère sa clé pour que l'utilisateur puisse corriger et relancer.
"""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from apps.core.models import ActivityLog
from apps.evidences.models import Evidence, EvidenceStatus
from apps.projects.models import Milestone, Task
from apps.sync.models import SyncOperation, SyncOperationStatus
from apps.users.roles import Role

BATCH_URL = "/api/sync/batch/"


def results_by_op(response) -> dict:
    return {item["op_id"]: item for item in response.data["results"]}


# ---------------------------------------------------------------- application nominale
@pytest.mark.django_db
def test_batch_applies_a_task_update(post_batch, project_context, task):
    operation = {
        "op_id": "op-1",
        "type": "TASK_UPDATE",
        "idempotency_key": uuid.uuid4().hex,
        # Une tâche terminée exige sa date de fin réelle : le client hors ligne l'a saisie.
        "payload": {
            "task": task.pk,
            "status": "DONE",
            "progress": "100.00",
            "actual_end_date": "2026-03-01",
        },
    }
    response = post_batch(project_context["owner"], [operation])

    assert response.status_code == 200, response.data
    assert response.data["counts"]["synced"] == 1
    result = response.data["results"][0]
    assert result["status"] == "SYNCED"
    assert result["replayed"] is False
    assert result["entity_type"] == "Task"
    assert result["entity_id"] == task.pk
    assert result["entity"]["status"] == "DONE"
    assert result["entity"]["project_progress"] >= 0
    assert result["error"] is None

    task.refresh_from_db()
    assert task.status == "DONE"
    # Le service métier est partagé avec l'API interactive : le journal est écrit ici aussi.
    assert ActivityLog.objects.filter(action="TASK_STATUS_CHANGED", entity_id=task.pk).exists()


@pytest.mark.django_db
def test_batch_covers_several_entity_types_in_one_call(post_batch, project_context, project):
    operations = [
        {
            "op_id": "m-1",
            "type": "MILESTONE_CREATE",
            "idempotency_key": uuid.uuid4().hex,
            "payload": {
                "project": project.pk,
                "title": "Élévations",
                "planned_date": "2026-05-30",
                "weight": 1,
            },
        },
        {
            "op_id": "t-1",
            "type": "TASK_CREATE",
            "idempotency_key": uuid.uuid4().hex,
            "payload": {
                "project": project.pk,
                "title": "Coffrage poteaux",
                "status": "TODO",
                "planned_start_date": "2026-04-01",
                "planned_end_date": "2026-04-20",
            },
        },
    ]
    response = post_batch(project_context["owner"], operations)

    assert response.status_code == 200, response.data
    assert response.data["counts"] == {
        "synced": 2,
        "conflict": 0,
        "failed": 0,
        "replayed": 0,
    }
    assert Milestone.objects.filter(title="Élévations").exists()
    assert Task.objects.filter(title="Coffrage poteaux").exists()


@pytest.mark.django_db
def test_batch_applies_a_validation_decision(post_batch, project_context, evidence):
    operation = {
        "op_id": "v-1",
        "type": "EVIDENCE_TRANSITION",
        "idempotency_key": uuid.uuid4().hex,
        "payload": {"evidence": evidence.pk, "action": "VALIDATE", "comment": "Conforme"},
    }
    response = post_batch(project_context["validator"], [operation])

    assert response.status_code == 200, response.data
    result = response.data["results"][0]
    assert result["status"] == "SYNCED"
    assert result["entity_type"] == "Evidence"
    assert result["entity"]["status"] == "VALIDATED"
    assert result["entity"]["last_validation"]["action"] == "VALIDATE"

    evidence.refresh_from_db()
    assert evidence.status == EvidenceStatus.VALIDATED
    assert evidence.validations.count() == 1


# ---------------------------------------------------------------- idempotence
@pytest.mark.django_db
def test_replaying_a_key_returns_the_stored_result_without_second_effect(
    post_batch, project_context, task
):
    payload = {"task": task.pk, "progress": "75.00"}
    key = uuid.uuid4().hex
    first = post_batch(
        project_context["owner"],
        [{"op_id": "a", "type": "TASK_UPDATE", "idempotency_key": key, "payload": payload}],
    )
    assert first.data["results"][0]["replayed"] is False
    logs_after_first = ActivityLog.objects.count()
    updated_at_after_first = SyncOperation.objects.get(idempotency_key=key).updated_at

    # Rejeu : la même clé, après une coupure réseau supposée.
    second = post_batch(
        project_context["owner"],
        [{"op_id": "a", "type": "TASK_UPDATE", "idempotency_key": key, "payload": payload}],
    )
    result = second.data["results"][0]
    assert result["status"] == "SYNCED"
    assert result["replayed"] is True
    assert result["entity_id"] == task.pk
    assert result["entity"]["progress"] == "75.00"

    # Aucun second effet : ni journal supplémentaire, ni nouvelle écriture du registre.
    assert ActivityLog.objects.count() == logs_after_first
    assert SyncOperation.objects.filter(idempotency_key=key).count() == 1
    assert SyncOperation.objects.get(idempotency_key=key).updated_at == updated_at_after_first


@pytest.mark.django_db
def test_a_key_reused_for_another_operation_type_is_refused(post_batch, project_context, task):
    key = uuid.uuid4().hex
    post_batch(
        project_context["owner"],
        [
            {
                "op_id": "a",
                "type": "TASK_UPDATE",
                "idempotency_key": key,
                "payload": {"task": task.pk, "progress": "10.00"},
            }
        ],
    )
    response = post_batch(
        project_context["owner"],
        [
            {
                "op_id": "b",
                "type": "MILESTONE_UPDATE",
                "idempotency_key": key,
                "payload": {"milestone": task.milestone_id, "status": "DONE"},
            }
        ],
    )
    result = response.data["results"][0]
    assert result["status"] == "CONFLICT"
    assert result["error"]["code"] == "idempotency_key_conflict"
    assert result["error"]["details"]["existing_type"] == "TASK_UPDATE"


@pytest.mark.django_db
def test_an_operation_still_in_progress_is_reported_as_conflict(post_batch, project_context, task):
    key = uuid.uuid4().hex
    SyncOperation.objects.create(
        user=project_context["owner"],
        idempotency_key=key,
        operation_type="TASK_UPDATE",
        status=SyncOperationStatus.IN_PROGRESS,
    )
    response = post_batch(
        project_context["owner"],
        [
            {
                "op_id": "a",
                "type": "TASK_UPDATE",
                "idempotency_key": key,
                "payload": {"task": task.pk, "progress": "20.00"},
            }
        ],
    )
    result = response.data["results"][0]
    assert result["status"] == "CONFLICT"
    assert result["error"]["code"] == "op_in_progress"
    task.refresh_from_db()
    # La tentative n'a produit aucun effet : la tâche garde son avancement d'origine.
    assert str(task.progress) == "40.00"


@pytest.mark.django_db
def test_two_users_may_use_the_same_key(post_batch, project_context, task, member_user):
    key = uuid.uuid4().hex
    operation = {
        "op_id": "a",
        "type": "TASK_UPDATE",
        "idempotency_key": key,
        "payload": {"task": task.pk, "progress": "30.00"},
    }
    first = post_batch(project_context["owner"], [operation])
    second = post_batch(member_user(Role.ENGINEER), [{**operation, "op_id": "b"}])

    assert first.data["results"][0]["status"] == "SYNCED"
    assert second.data["results"][0]["status"] == "SYNCED"
    assert SyncOperation.objects.filter(idempotency_key=key).count() == 2


# ---------------------------------------------------------------- conflits et erreurs
@pytest.mark.django_db
def test_forbidden_operation_is_a_conflict_and_lets_the_user_retry(
    post_batch, project_context, task, member_user
):
    """Un refus de permission ne consomme pas la clé : après correction, la reprise fonctionne."""
    # Le validateur ne planifie pas : il ne peut pas renommer une tâche.
    validator = member_user(Role.VALIDATOR, can_validate_evidence=True)
    key = uuid.uuid4().hex
    response = post_batch(
        validator,
        [
            {
                "op_id": "a",
                "type": "TASK_UPDATE",
                "idempotency_key": key,
                "payload": {"task": task.pk, "title": "Titre renommé sans permission"},
            }
        ],
    )
    result = response.data["results"][0]
    assert result["status"] == "CONFLICT"
    assert result["error"]["code"] == "permission_denied"
    assert not SyncOperation.objects.filter(idempotency_key=key).exists()
    task.refresh_from_db()
    assert task.status == "IN_PROGRESS"


@pytest.mark.django_db
def test_own_evidence_validation_is_a_conflict(
    auth_client, post_batch, project_context, project, photo
):
    """Un acteur qui a lui-même déposé la preuve ne peut pas la valider (anti-fraude)."""
    upload = auth_client(project_context["owner"]).post(
        "/api/evidences/",
        {
            "project": project.pk,
            "file": photo(),
            "captured_at": timezone.now().isoformat(),
            "gps_status": "UNAVAILABLE",
        },
        format="multipart",
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert upload.status_code == 201, upload.data
    evidence = Evidence.objects.get(pk=upload.data["id"])

    response = post_batch(
        project_context["owner"],
        [
            {
                "op_id": "a",
                "type": "EVIDENCE_TRANSITION",
                "idempotency_key": uuid.uuid4().hex,
                "payload": {"evidence": evidence.pk, "action": "VALIDATE"},
            }
        ],
    )
    result = response.data["results"][0]
    assert result["status"] == "CONFLICT"
    assert result["error"]["code"] == "cannot_validate_own_evidence"
    evidence.refresh_from_db()
    assert evidence.status == EvidenceStatus.PENDING


@pytest.mark.django_db
def test_impossible_transition_is_a_conflict_with_allowed_actions(
    post_batch, project_context, evidence
):
    post_batch(
        project_context["validator"],
        [
            {
                "op_id": "a",
                "type": "EVIDENCE_TRANSITION",
                "idempotency_key": uuid.uuid4().hex,
                "payload": {"evidence": evidence.pk, "action": "VALIDATE"},
            }
        ],
    )
    response = post_batch(
        project_context["validator"],
        [
            {
                "op_id": "b",
                "type": "EVIDENCE_TRANSITION",
                "idempotency_key": uuid.uuid4().hex,
                # Une preuve validée ne peut plus l'être une seconde fois.
                "payload": {"evidence": evidence.pk, "action": "VALIDATE"},
            }
        ],
    )
    result = response.data["results"][0]
    assert result["status"] == "CONFLICT"
    assert result["error"]["code"] == "invalid_transition"
    assert "REOPEN" in result["error"]["details"]["allowed_actions"]


@pytest.mark.django_db
def test_rejection_without_comment_is_a_conflict(post_batch, project_context, evidence):
    response = post_batch(
        project_context["validator"],
        [
            {
                "op_id": "a",
                "type": "EVIDENCE_TRANSITION",
                "idempotency_key": uuid.uuid4().hex,
                "payload": {"evidence": evidence.pk, "action": "REJECT"},
            }
        ],
    )
    result = response.data["results"][0]
    assert result["status"] == "CONFLICT"
    assert result["error"]["code"] == "comment_required"
    assert Evidence.objects.get(pk=evidence.pk).status == EvidenceStatus.PENDING


@pytest.mark.django_db
def test_out_of_scope_target_is_reported_as_not_found(post_batch, project_context, task):
    response = post_batch(
        project_context["stranger"],
        [
            {
                "op_id": "a",
                "type": "TASK_UPDATE",
                "idempotency_key": uuid.uuid4().hex,
                "payload": {"task": task.pk, "progress": "50.00"},
            }
        ],
    )
    result = response.data["results"][0]
    assert result["status"] == "CONFLICT"
    assert result["error"]["code"] == "not_found"
    assert result["http_status"] == 404


@pytest.mark.django_db
def test_invalid_payload_is_reported_as_failed(post_batch, project_context):
    response = post_batch(
        project_context["owner"],
        [
            {
                "op_id": "a",
                "type": "TASK_UPDATE",
                "idempotency_key": uuid.uuid4().hex,
                "payload": {"progress": "50.00"},  # tâche non précisée
            }
        ],
    )
    result = response.data["results"][0]
    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "invalid_operation_payload"


@pytest.mark.django_db
def test_unknown_operation_type_is_refused(post_batch, project_context):
    response = post_batch(
        project_context["owner"],
        [
            {
                "op_id": "a",
                "type": "TASK_DELETE",  # la suppression n'est pas rejouable hors ligne
                "idempotency_key": uuid.uuid4().hex,
                "payload": {},
            }
        ],
    )
    assert response.status_code == 400  # refusé par le schéma du lot
    assert response.data["error"]["code"] == "validation_error"


@pytest.mark.django_db
def test_a_file_operation_cannot_travel_in_a_batch(post_batch, project_context, project):
    response = post_batch(
        project_context["agent"],
        [
            {
                "op_id": "a",
                "type": "EVIDENCE_UPLOAD",
                "idempotency_key": uuid.uuid4().hex,
                "payload": {"project": project.pk},
            }
        ],
    )
    result = response.data["results"][0]
    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "operation_requires_file"
    assert result["error"]["details"]["endpoint"] == "/api/evidences/"


@pytest.mark.django_db
def test_one_refused_operation_does_not_block_the_others(post_batch, project_context, task):
    """Un lot est un ensemble d'opérations indépendantes : l'échec de l'une n'annule pas les autres."""
    kept = {
        "op_id": "ok-1",
        "type": "TASK_UPDATE",
        "idempotency_key": uuid.uuid4().hex,
        "payload": {"task": task.pk, "progress": "60.00"},
    }
    refused = {
        "op_id": "ko",
        "type": "TASK_UPDATE",
        "idempotency_key": uuid.uuid4().hex,
        "payload": {"task": 999_999, "progress": "10.00"},
    }
    after = {
        "op_id": "ok-2",
        "type": "TASK_UPDATE",
        "idempotency_key": uuid.uuid4().hex,
        "payload": {
            "task": task.pk,
            "status": "DONE",
            "progress": "100.00",
            "actual_end_date": "2026-03-01",
        },
    }
    response = post_batch(project_context["owner"], [kept, refused, after])

    statuses = {item["op_id"]: item["status"] for item in response.data["results"]}
    assert statuses == {"ok-1": "SYNCED", "ko": "CONFLICT", "ok-2": "SYNCED"}
    assert response.data["counts"]["synced"] == 2
    assert response.data["counts"]["conflict"] == 1

    task.refresh_from_db()
    assert task.status == "DONE"
    assert task.progress == 100


# ---------------------------------------------------------------- schéma du lot
@pytest.mark.django_db
def test_batch_requires_authentication(api, project_context, task):
    response = api.post(
        BATCH_URL,
        {
            "operations": [
                {
                    "op_id": "a",
                    "type": "TASK_UPDATE",
                    "idempotency_key": uuid.uuid4().hex,
                    "payload": {"task": task.pk},
                }
            ]
        },
        format="json",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_a_duplicate_key_inside_the_batch_is_rejected(post_batch, project_context, task):
    key = uuid.uuid4().hex
    response = post_batch(
        project_context["owner"],
        [
            {
                "op_id": "a",
                "type": "TASK_UPDATE",
                "idempotency_key": key,
                "payload": {"task": task.pk, "progress": "10.00"},
            },
            {
                "op_id": "b",
                "type": "TASK_UPDATE",
                "idempotency_key": key,
                "payload": {"task": task.pk, "progress": "20.00"},
            },
        ],
    )
    assert response.status_code == 400
    assert "idempotence" in str(response.data["error"]["details"])


@pytest.mark.django_db
def test_an_empty_batch_is_rejected(post_batch, project_context):
    response = post_batch(project_context["owner"], [])
    assert response.status_code == 400


@pytest.mark.django_db
def test_batch_size_is_limited(post_batch, project_context, task, operation):
    operations = [
        operation("TASK_UPDATE", {"task": task.pk, "progress": "10.00"}) for _ in range(51)
    ]
    response = post_batch(project_context["owner"], operations)
    assert response.status_code == 400


@pytest.mark.django_db
def test_milestone_update_returns_the_new_progress(post_batch, project_context, milestone):
    response = post_batch(
        project_context["owner"],
        [
            {
                "op_id": "a",
                "type": "MILESTONE_UPDATE",
                "idempotency_key": uuid.uuid4().hex,
                "payload": {
                    "milestone": milestone.pk,
                    "status": "DONE",
                    "actual_date": "2026-03-28",
                },
            }
        ],
    )
    result = response.data["results"][0]
    assert result["status"] == "SYNCED"
    assert result["entity"]["status"] == "DONE"
    assert "project_progress" in result["entity"]
    milestone.refresh_from_db()
    assert milestone.status == "DONE"


@pytest.mark.django_db
def test_sync_status_reports_what_the_server_supports(
    post_batch, auth_client, project_context, task
):
    post_batch(
        project_context["owner"],
        [
            {
                "op_id": "a",
                "type": "TASK_UPDATE",
                "idempotency_key": uuid.uuid4().hex,
                "payload": {"task": task.pk, "progress": "15.00"},
            }
        ],
    )
    response = auth_client(project_context["owner"]).get("/api/sync/status/")

    assert response.status_code == 200, response.data
    assert "TASK_UPDATE" in response.data["supported_operations"]
    assert "EVIDENCE_UPLOAD" in response.data["file_operations"]
    assert response.data["file_operations"][0] not in response.data["supported_operations"]
    assert response.data["applied"] == 1
    assert response.data["in_progress"] == 0
    assert response.data["last_applied_at"] is not None  # ISO 8601, prêt pour l'écran de suivi
    assert response.data["batch_limit"] == 50


@pytest.mark.django_db
def test_a_stuck_key_can_be_released_manually(auth_client, project_context):
    key = uuid.uuid4().hex
    SyncOperation.objects.create(
        user=project_context["owner"],
        idempotency_key=key,
        operation_type="TASK_UPDATE",
        status=SyncOperationStatus.IN_PROGRESS,
    )
    response = auth_client(project_context["owner"]).post(
        f"/api/sync/operations/{key}/forget/", format="json"
    )
    assert response.status_code == 200
    assert not SyncOperation.objects.filter(idempotency_key=key).exists()

    again = auth_client(project_context["owner"]).post(
        f"/api/sync/operations/{key}/forget/", format="json"
    )
    assert again.status_code == 404


# ---------------------------------------------------------------- règles métier héritées
@pytest.mark.django_db
def test_offline_task_update_obeys_the_assignee_rule(
    post_batch, project_context, task, member_user
):
    """Le responsable désigné peut avancer sa tâche hors ligne, comme en ligne — pas plus."""
    # L'entreprise de travaux exécute (UPDATE_TASK) sans replanifier (pas de MANAGE_SCHEDULE).
    assignee = member_user(Role.CONTRACTOR)
    task.assignee = assignee
    task.save(update_fields=["assignee"])
    payload = {
        "task": task.pk,
        "status": "DONE",
        "progress": "100.00",
        "actual_end_date": "2026-03-01",
        "description": "Coulage terminé et contrôlé.",
    }
    response = post_batch(
        assignee,
        [
            {
                "op_id": "a",
                "type": "TASK_UPDATE",
                "idempotency_key": uuid.uuid4().hex,
                "payload": payload,
            }
        ],
    )
    assert response.data["results"][0]["status"] == "SYNCED", response.data

    # En revanche, replanifier (changer le jalon) reste réservé à `MANAGE_SCHEDULE`.
    replanning = post_batch(
        assignee,
        [
            {
                "op_id": "b",
                "type": "TASK_UPDATE",
                "idempotency_key": uuid.uuid4().hex,
                "payload": {"task": task.pk, "milestone": None},
            }
        ],
    )
    refused = replanning.data["results"][0]
    assert refused["status"] == "CONFLICT"
    assert refused["error"]["code"] == "permission_denied"


@pytest.mark.django_db
def test_task_creation_is_reserved_to_planners(post_batch, project_context, project, member_user):
    validator = member_user(Role.VALIDATOR)
    response = post_batch(
        validator,
        [
            {
                "op_id": "a",
                "type": "TASK_CREATE",
                "idempotency_key": uuid.uuid4().hex,
                "payload": {"project": project.pk, "title": "Tâche non autorisée"},
            }
        ],
    )
    result = response.data["results"][0]
    assert result["status"] == "CONFLICT"
    assert result["error"]["code"] == "permission_denied"
    assert not Task.objects.filter(title="Tâche non autorisée").exists()


@pytest.mark.django_db
def test_invalid_field_values_are_reported_with_the_field_name(post_batch, project_context, task):
    """Un refus de validation doit dire quel champ corriger : l'utilisateur est hors ligne."""
    response = post_batch(
        project_context["owner"],
        [
            {
                "op_id": "a",
                "type": "TASK_UPDATE",
                "idempotency_key": uuid.uuid4().hex,
                "payload": {"task": task.pk, "planned_start_date": "2026-03-10"},
            }
        ],
    )
    result = response.data["results"][0]
    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "validation_error"
    # Le champ fautif est nommé : l'utilisateur sait quoi corriger, même hors ligne.
    assert set(result["error"]["details"]) & {"planned_start_date", "planned_end_date"}
