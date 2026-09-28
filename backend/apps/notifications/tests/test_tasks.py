"""Fiabilité des tâches Celery (phase 10) : succès, échec, retry, suivi et observabilité."""

from __future__ import annotations

from datetime import timedelta

import pytest
from celery.exceptions import Retry
from django.utils import timezone

from apps.core.models import ActivityLog, TaskRun
from apps.notifications import services, tasks
from apps.notifications.models import Notification
from apps.projects.models import Milestone, MilestoneStatus, Task, TaskStatus

EVENT = {
    "event_type": "MilestoneValidated",
    "project_id": None,
    "actor_id": None,
    "entity_type": "Milestone",
    "entity_id": "1",
    "payload": {"title": "Fondations"},
    "recipient_ids": [],
    "dedupe_key": "",
}


@pytest.fixture(autouse=True)
def worker_like_eager(settings):
    """Mode eager sans propagation : le comportement (retry, échec final) est celui d'un worker."""
    settings.CELERY_TASK_EAGER_PROPAGATES = False


@pytest.fixture()
def event(project):
    return {**EVENT, "project_id": project.pk}


@pytest.mark.django_db
def test_dispatch_task_success_is_tracked(event, project_context):
    result = tasks.dispatch_domain_event.delay(event)

    assert result.get()["created"] >= 1
    run = TaskRun.objects.get(name="apps.notifications.tasks.dispatch_domain_event")
    assert run.state == TaskRun.State.SUCCESS and run.retries == 0 and run.error == ""
    assert run.duration_ms is not None


@pytest.mark.django_db
def test_transient_failure_is_retried_then_succeeds(event, project_context, monkeypatch):
    real, attempts = services.dispatch, {"n": 0}

    def flaky(payload):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("base indisponible")
        return real(payload)

    monkeypatch.setattr(services, "dispatch", flaky)
    monkeypatch.setattr(tasks, "RETRY_BASE_SECONDS", 0)

    result = tasks.dispatch_domain_event.apply(args=[event], throw=False)

    assert attempts["n"] == 3
    assert result.successful() and result.result["created"] >= 1
    run = TaskRun.objects.get(name="apps.notifications.tasks.dispatch_domain_event")
    assert run.state == TaskRun.State.SUCCESS
    assert Notification.objects.filter(event_type="MilestoneValidated").count() >= 1
    # Le retry n'a pas produit de doublon : une seule notification par destinataire.
    per_user = Notification.objects.values_list("user_id", flat=True)
    assert len(set(per_user)) == len(list(per_user))


@pytest.mark.django_db
def test_permanent_failure_is_logged_with_its_error(event, project_context, monkeypatch, caplog):
    def broken(payload):
        raise RuntimeError("boom définitif")

    monkeypatch.setattr(services, "dispatch", broken)
    monkeypatch.setattr(tasks, "RETRY_BASE_SECONDS", 0)

    result = tasks.dispatch_domain_event.apply(args=[event], throw=False)

    assert result.failed() and isinstance(result.result, RuntimeError)

    run = TaskRun.objects.get(name="apps.notifications.tasks.dispatch_domain_event")
    assert run.state == TaskRun.State.FAILURE
    assert "boom définitif" in run.error
    assert run.retries == tasks.dispatch_domain_event.max_retries
    entry = ActivityLog.objects.get(action="TASK_FAILED")
    assert entry.metadata["task"] == "apps.notifications.tasks.dispatch_domain_event"
    assert "boom définitif" in entry.metadata["error"]
    assert "event_type" not in entry.metadata  # jamais les arguments de la tâche
    assert Notification.objects.count() == 0  # rien de partiel : l'opération est atomique
    assert any("échec définitif" in message for message in caplog.messages)


@pytest.mark.django_db
def test_failed_dispatch_leaves_no_partial_notifications(event, project_context, monkeypatch):
    real, calls = services._apply, {"n": 0}

    def explode_on_second(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("panne au 2e destinataire")
        return real(*args, **kwargs)

    monkeypatch.setattr(services, "_apply", explode_on_second)
    with pytest.raises(RuntimeError):
        services.dispatch(event)
    assert Notification.objects.count() == 0


@pytest.mark.django_db
def test_retry_uses_exponential_backoff(event, monkeypatch):
    seen = []
    task = tasks.dispatch_domain_event._get_current_object()

    def broken(payload):
        raise RuntimeError("x")

    def fake_retry(exc=None, countdown=None, **kwargs):
        seen.append(countdown)
        raise Retry()

    monkeypatch.setattr(services, "dispatch", broken)
    monkeypatch.setattr(task, "retry", fake_retry, raising=False)
    for retries in range(4):
        task.push_request(retries=retries)
        try:
            with pytest.raises(Retry):
                task.run(event)
        finally:
            task.pop_request()

    assert seen == [10, 20, 40, 80]
    assert task.max_retries == 5 and task.acks_late is True


@pytest.mark.django_db
def test_broker_outage_does_not_break_the_business_operation(
    project, project_context, monkeypatch, django_capture_on_commit_callbacks, caplog
):
    from apps.core.events import DomainEvent, emit

    def down(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(tasks.dispatch_domain_event, "delay", down)
    with django_capture_on_commit_callbacks(execute=True):
        emit(DomainEvent.PROJECT_DELAYED, project=project, payload={})

    assert any("Publication impossible" in message for message in caplog.messages)


# --- Détection quotidienne des retards ------------------------------------------------------


@pytest.fixture()
def late_project(project, project_context):
    today = timezone.localdate()
    Task.objects.create(
        project=project,
        title="En retard",
        status=TaskStatus.IN_PROGRESS,
        planned_end_date=today - timedelta(days=4),
        created_by=project_context["owner"],
    )
    Milestone.objects.create(
        project=project,
        title="Jalon en retard",
        status=MilestoneStatus.PLANNED,
        planned_date=today - timedelta(days=2),
        created_by=project_context["owner"],
    )
    return project


@pytest.mark.django_db
def test_detect_delays_notifies_managers_once_per_day(
    late_project, project_context, notes, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        first = tasks.detect_project_delays.delay().get()
    with django_capture_on_commit_callbacks(execute=True):
        second = tasks.detect_project_delays.delay().get()

    assert first["projects"] == 1 and second["projects"] == 1
    owner_notes = notes(project_context["owner"], event_type="ProjectDelayed")
    assert len(owner_notes) == 1 and owner_notes[0].count == 1  # idempotent
    assert "1 tâche en retard" in owner_notes[0].body and "1 jalon en retard" in owner_notes[0].body
    assert notes(project_context["engineer"], event_type="ProjectDelayed")
    assert notes(project_context["investor"], event_type="ProjectDelayed") == []
    assert notes(project_context["agent"], event_type="ProjectDelayed") == []
    assert ActivityLog.objects.filter(action="PROJECT_DELAY_DETECTED").count() == 1


@pytest.mark.django_db
def test_detect_delays_ignores_healthy_and_inactive_projects(
    project, project_context, django_capture_on_commit_callbacks
):
    Task.objects.create(
        project=project,
        title="Dans les temps",
        status=TaskStatus.TODO,
        planned_end_date=timezone.localdate() + timedelta(days=3),
        created_by=project_context["owner"],
    )
    with django_capture_on_commit_callbacks(execute=True):
        assert tasks.detect_project_delays.delay().get()["projects"] == 0
    assert Notification.objects.count() == 0


@pytest.mark.django_db
def test_detect_delays_skips_finished_projects(late_project, django_capture_on_commit_callbacks):
    type(late_project).objects.filter(pk=late_project.pk).update(status="COMPLETED")
    with django_capture_on_commit_callbacks(execute=True):
        assert tasks.detect_project_delays.delay().get()["projects"] == 0


# --- Purges ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_purge_deletes_only_old_read_notifications(project, project_context):
    user = project_context["owner"]
    old = timezone.now() - timedelta(days=200)
    make = lambda key, read_at: Notification.objects.create(  # noqa: E731
        user=user, event_type="X", group_key=key, title=key, read_at=read_at
    )
    make("old-read", old)
    make("recent-read", timezone.now())
    make("old-unread", None)

    assert tasks.purge_old_notifications.delay(90).get() == 1
    assert set(Notification.objects.values_list("group_key", flat=True)) == {
        "recent-read",
        "old-unread",
    }
