"""Fixtures du lot de synchronisation : jalon, tâche et opérations prêtes à rejouer."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from apps.evidences.tests.conftest import make_image
from apps.projects.models import Milestone, ProjectMember, Task
from apps.users.roles import Role

BATCH_URL = "/api/sync/batch/"


@pytest.fixture()
def photo():
    """Upload d'une vraie image (réutilise la fabrique des tests de preuves)."""

    def _photo(**kwargs):
        from django.core.files.uploadedfile import SimpleUploadedFile

        image_format = kwargs.get("image_format", "JPEG")
        payload = make_image(**kwargs)
        extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[image_format]
        return SimpleUploadedFile(
            f"chantier.{extension}", payload, content_type=f"image/{extension}"
        )

    return _photo


@pytest.fixture()
def milestone(project, project_context):
    return Milestone.objects.create(
        project=project,
        title="Fondations",
        status="IN_PROGRESS",
        planned_date=date(2026, 3, 31),
        order=1,
        weight=2,
        created_by=project_context["owner"],
    )


@pytest.fixture()
def task(project, project_context, milestone):
    return Task.objects.create(
        project=project,
        milestone=milestone,
        title="Coulage dalle",
        created_by=project_context["owner"],
        status="IN_PROGRESS",
        progress="40.00",
        planned_start_date=date(2026, 2, 1),
        planned_end_date=date(2026, 2, 28),
        assignee=project_context["engineer"],
    )


@pytest.fixture()
def operation():
    """Fabrique d'opération de lot : `operation("TASK_UPDATE", {...})`."""

    def _operation(operation_type: str, payload: dict, **extra) -> dict:
        return {
            "op_id": uuid.uuid4().hex,
            "type": operation_type,
            "idempotency_key": uuid.uuid4().hex,
            "payload": payload,
            **extra,
        }

    return _operation


@pytest.fixture()
def post_batch(auth_client):
    def _post(user, operations: list[dict]):
        return auth_client(user).post(BATCH_URL, {"operations": operations}, format="json")

    return _post


@pytest.fixture()
def evidence(auth_client, project, project_context, photo):
    """Preuve déposée par l'agent terrain, en attente de validation."""
    from django.utils import timezone

    response = auth_client(project_context["agent"]).post(
        "/api/evidences/",
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
    from apps.evidences.models import Evidence

    return Evidence.objects.get(pk=response.data["id"])


@pytest.fixture()
def member_user(db, project, make_user):
    """Membre du projet avec un rôle donné (raccourci de test)."""

    def _make(role: str = Role.ENGINEER, **flags):
        # Numéro unique : plusieurs tests créent le même rôle dans une même base de test.
        user = make_user(role, phone_number=f"+237698{uuid.uuid4().int % 10_000_000:07d}")
        ProjectMember.objects.create(project=project, user=user, role=role, **flags)
        return user

    return _make
