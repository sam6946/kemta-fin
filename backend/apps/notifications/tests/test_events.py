"""MVP-013/014 — événements métier → notifications : destinataires, regroupement, fiabilité."""

from __future__ import annotations

import uuid

import pytest
from django.db import transaction
from django.utils import timezone

from apps.core.events import ALL_EVENTS, DomainEvent, emit
from apps.core.models import ActivityLog
from apps.evidences.models import Evidence
from apps.finance import services as finance
from apps.notifications import services
from apps.notifications.models import Notification
from apps.projects.models import Milestone, MilestoneStatus


@pytest.fixture()
def run_on_commit(django_capture_on_commit_callbacks):
    """Exécute les callbacks `on_commit` (le rollback de test ne les déclenche jamais)."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with django_capture_on_commit_callbacks(execute=True):
            yield

    return _ctx


@pytest.fixture()
def milestone(project, project_context):
    return Milestone.objects.create(
        project=project,
        title="Dalle R+1",
        status=MilestoneStatus.IN_PROGRESS,
        planned_date=timezone.localdate(),
        created_by=project_context["owner"],
    )


def finish_milestone(auth_client, user, milestone):
    return auth_client(user).patch(
        f"/api/milestones/{milestone.pk}/",
        {"status": "DONE", "actual_date": str(timezone.localdate())},
        format="json",
    )


_counter = iter(range(1, 10_000))


def new_evidence(auth_client, project, agent, photo):
    shade = next(_counter)
    response = auth_client(agent).post(
        "/api/evidences/",
        {
            "project": project.pk,
            "file": photo(color=(shade % 256, (shade * 7) % 256, 90)),
            "captured_at": timezone.now().isoformat(),
            "gps_status": "UNAVAILABLE",
        },
        format="multipart",
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.data
    return Evidence.objects.get(pk=response.data["id"])


def reject(auth_client, validator, evidence, comment="Photo floue"):
    return auth_client(validator).post(
        f"/api/evidences/{evidence.pk}/transition/",
        {"action": "REJECT", "comment": comment},
        format="json",
    )


# --- MilestoneValidated -------------------------------------------------------------------


@pytest.mark.django_db
def test_milestone_validated_notifies_the_team_but_not_the_actor(
    auth_client, project_context, milestone, run_on_commit, notes
):
    with run_on_commit():
        response = finish_milestone(auth_client, project_context["engineer"], milestone)
    assert response.status_code == 200, response.data

    assert len(notes(project_context["owner"], event_type="MilestoneValidated")) == 1
    assert len(notes(project_context["investor"], event_type="MilestoneValidated")) == 1
    assert notes(project_context["engineer"]) == []  # l'acteur n'est pas notifié de son geste
    assert notes(project_context["stranger"]) == []
    note = notes(project_context["owner"])[0]
    assert note.title == "Jalon validé : Dalle R+1"
    assert note.project_id == milestone.project_id


@pytest.mark.django_db
def test_no_event_when_a_milestone_is_edited_but_not_finished(
    auth_client, project_context, milestone, run_on_commit
):
    with run_on_commit():
        auth_client(project_context["owner"]).patch(
            f"/api/milestones/{milestone.pk}/", {"title": "Renommé"}, format="json"
        )
    assert Notification.objects.count() == 0


# --- ExpenseSubmitted ---------------------------------------------------------------------


@pytest.mark.django_db
def test_expense_submitted_notifies_finance_deciders_only(
    auth_client, project_context, expense, transition, run_on_commit, notes
):
    with run_on_commit():
        response = transition(project_context["finance"], expense, "SUBMIT")
    assert response.status_code == 200, response.data

    assert len(notes(project_context["owner"])) == 1
    assert notes(project_context["finance"]) == []  # c'est lui qui a soumis
    assert notes(project_context["agent"]) == []
    assert notes(project_context["investor"]) == []
    body = notes(project_context["owner"])[0]
    assert body.event_type == "ExpenseSubmitted" and "1 200 000 FCFA" in body.body


# --- EvidenceRejected ---------------------------------------------------------------------


@pytest.mark.django_db
def test_evidence_rejected_notifies_the_author_and_managers(
    auth_client, project, project_context, photo, run_on_commit, notes
):
    evidence = new_evidence(auth_client, project, project_context["agent"], photo)
    with run_on_commit():
        assert reject(auth_client, project_context["validator"], evidence).status_code == 200

    author_notes = notes(project_context["agent"])
    assert len(author_notes) == 1 and author_notes[0].event_type == "EvidenceRejected"
    assert "Photo floue" in author_notes[0].body
    assert len(notes(project_context["owner"])) == 1
    assert notes(project_context["validator"]) == []
    assert notes(project_context["investor"]) == []


@pytest.mark.django_db
def test_validating_an_evidence_sends_no_rejection_event(
    auth_client, project, project_context, photo, run_on_commit
):
    evidence = new_evidence(auth_client, project, project_context["agent"], photo)
    with run_on_commit():
        auth_client(project_context["validator"]).post(
            f"/api/evidences/{evidence.pk}/transition/", {"action": "VALIDATE"}, format="json"
        )
    assert Notification.objects.count() == 0


# --- BudgetThresholdReached ---------------------------------------------------------------


@pytest.mark.django_db
def test_budget_threshold_events_fire_once_per_threshold(
    project, project_context, run_on_commit, notes
):
    owner, fin = project_context["owner"], project_context["finance"]

    def spend(amount, title):
        item = finance.create_expense(
            project=project,
            actor=fin,
            data={"title": title, "amount": amount, "incurred_on": "2026-03-01"},
        )
        finance.transition_expense(expense=item, actor=fin, action="SUBMIT")
        with run_on_commit():
            finance.transition_expense(expense=item, actor=owner, action="APPROVE")

    spend(20_000_000, "Petit")
    assert notes(project_context["investor"], event_type="BudgetThresholdReached") == []

    spend(21_000_000, "Franchit 80")  # 41 / 50 = 82 %
    spend(1_000_000, "Reste sous 100")
    investor_notes = notes(project_context["investor"], event_type="BudgetThresholdReached")
    assert len(investor_notes) == 1
    assert "80 %" in investor_notes[0].title

    spend(8_000_000, "Franchit 100")  # 50 / 50
    titles = [
        n.title for n in notes(project_context["investor"], event_type="BudgetThresholdReached")
    ]
    assert len(titles) == 2 and "dépassé" in titles[1]
    assert (
        ActivityLog.objects.filter(action="BUDGET_THRESHOLD_REACHED", project=project).count() == 1
    )


# --- Regroupement, dédoublonnage, transaction ---------------------------------------------


@pytest.mark.django_db
def test_similar_unread_events_are_grouped_then_a_new_notification_starts_after_reading(
    auth_client, project, project_context, photo, run_on_commit, notes
):
    agent, validator = project_context["agent"], project_context["validator"]
    for _ in range(3):
        evidence = new_evidence(auth_client, project, agent, photo)
        with run_on_commit():
            reject(auth_client, validator, evidence)

    grouped = notes(agent)
    assert len(grouped) == 1
    assert grouped[0].count == 3
    assert grouped[0].title == "3 preuves rejetées"
    assert len(grouped[0].data["items"]) == 3

    Notification.objects.filter(pk=grouped[0].pk).update(read_at=timezone.now())
    with run_on_commit():
        reject(auth_client, validator, new_evidence(auth_client, project, agent, photo))
    assert len(notes(agent)) == 2
    assert notes(agent, read_at__isnull=True)[0].count == 1


@pytest.mark.django_db
def test_dedupe_key_prevents_double_notification(project, project_context):
    event = {
        "event_type": DomainEvent.PROJECT_DELAYED,
        "project_id": project.pk,
        "actor_id": None,
        "payload": {"tasks_late": 2},
        "dedupe_key": f"delay:{project.pk}:2026-09-29",
    }
    first = services.dispatch(event)
    second = services.dispatch(event)

    assert first["created"] >= 1
    assert second["created"] == 0 and second["skipped"] == first["created"]


@pytest.mark.django_db
def test_rolled_back_operation_publishes_nothing(project, project_context, run_on_commit):
    with run_on_commit(), pytest.raises(RuntimeError), transaction.atomic():
        emit(DomainEvent.PROJECT_DELAYED, project=project, payload={"tasks_late": 1})
        raise RuntimeError("annulé")
    assert Notification.objects.count() == 0


@pytest.mark.django_db
def test_unknown_event_is_refused(project):
    with pytest.raises(ValueError):
        emit("Inconnu", project=project)


@pytest.mark.django_db
def test_every_declared_event_has_recipients_and_a_renderer(project):
    for event_type in ALL_EVENTS:
        assert event_type in services.RECIPIENT_ROLES
        title, _ = services.render(event_type, project, {"title": "x", "threshold_percent": 80}, 2)
        assert title and title != event_type


@pytest.mark.django_db
def test_deactivated_users_are_skipped(project, project_context):
    project_context["investor"].is_active = False
    project_context["investor"].save(update_fields=["is_active"])

    result = services.dispatch(
        {"event_type": DomainEvent.MILESTONE_VALIDATED, "project_id": project.pk, "payload": {}}
    )

    assert result["created"] > 0
    assert not Notification.objects.filter(user=project_context["investor"]).exists()


@pytest.mark.django_db
def test_soft_deleted_project_produces_no_notification(project, project_context):
    project.delete()  # suppression logique
    result = services.dispatch(
        {"event_type": DomainEvent.MILESTONE_VALIDATED, "project_id": project.pk, "payload": {}}
    )
    assert result["recipients"] == 0 and Notification.objects.count() == 0
