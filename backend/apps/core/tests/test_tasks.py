"""Suivi et entretien des tâches Celery (phase 10)."""

from __future__ import annotations

import os
import time
from datetime import timedelta

import pytest
from celery import shared_task
from django.utils import timezone

from apps.core import tasks
from apps.core.models import ActivityLog, TaskRun


@shared_task(bind=True, max_retries=2)
def _flaky(self, fail_times: int = 0, boom: bool = False):
    if boom or self.request.retries < fail_times:
        try:
            raise ValueError("secret-arg-should-not-leak" if boom else "temporaire")
        except ValueError as exc:
            raise self.retry(exc=exc, countdown=0) from exc
    return "ok"


@pytest.fixture(autouse=True)
def worker_like(settings):
    settings.CELERY_TASK_EAGER_PROPAGATES = False


@pytest.mark.django_db
def test_task_run_records_success_duration_and_state():
    result = _flaky.apply(throw=False)

    run = TaskRun.objects.get(task_id=result.id)
    assert result.result == "ok"
    assert run.state == TaskRun.State.SUCCESS and run.finished_at and run.duration_ms is not None


@pytest.mark.django_db
def test_retry_then_success_leaves_a_trace_of_the_attempts():
    result = _flaky.apply(kwargs={"fail_times": 2}, throw=False)

    run = TaskRun.objects.get(task_id=result.id)
    assert result.successful()
    assert run.state == TaskRun.State.SUCCESS and run.retries == 2


@pytest.mark.django_db
def test_final_failure_is_recorded_and_journaled_without_arguments():
    result = _flaky.apply(kwargs={"boom": True}, throw=False)

    run = TaskRun.objects.get(task_id=result.id)
    assert result.failed() and run.state == TaskRun.State.FAILURE
    assert run.retries == 2 and "ValueError" in run.error
    entry = ActivityLog.objects.get(action="TASK_FAILED", entity_id=result.id)
    assert entry.metadata["task"].endswith("_flaky")
    assert "boom" not in str(entry.metadata) and "kwargs" not in entry.metadata


@pytest.mark.django_db
def test_purge_keeps_recent_runs_and_keeps_failures_four_times_longer():
    now = timezone.now()

    def run(task_id, state, days):
        return TaskRun.objects.create(
            task_id=task_id,
            name="t",
            state=state,
            started_at=now - timedelta(days=days),
            finished_at=now - timedelta(days=days),
        )

    run("recent-ok", TaskRun.State.SUCCESS, 1)
    run("old-ok", TaskRun.State.SUCCESS, 60)
    run("old-failure", TaskRun.State.FAILURE, 60)  # < 4 × 30 j : conservée
    run("ancient-failure", TaskRun.State.FAILURE, 150)

    assert tasks.purge_old_task_runs.apply(args=[30], throw=False).result == 2
    assert set(TaskRun.objects.values_list("task_id", flat=True)) == {"recent-ok", "old-failure"}


@pytest.mark.django_db
def test_cleanup_removes_only_old_regular_files_under_tmp(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    (tmp_path / "tmp").mkdir()
    (tmp_path / "evidences").mkdir()
    old = tmp_path / "tmp" / "old.part"
    fresh = tmp_path / "tmp" / "fresh.part"
    keep = tmp_path / "evidences" / "photo.jpg"
    for path in (old, fresh, keep):
        path.write_bytes(b"x")
    stale = time.time() - 48 * 3600
    os.utime(old, (stale, stale))
    os.utime(keep, (stale, stale))
    link = tmp_path / "tmp" / "link"
    link.symlink_to(keep)
    os.utime(link, (stale, stale), follow_symlinks=False)

    assert tasks.cleanup_temp_files.apply(args=[24], throw=False).result == 1

    assert not old.exists()
    assert fresh.exists() and keep.exists() and link.is_symlink()


@pytest.mark.django_db
def test_cleanup_without_tmp_directory_is_a_noop(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / "absent")
    assert tasks.cleanup_temp_files.apply(throw=False).result == 0


def test_beat_schedule_declares_the_recurring_jobs(settings):
    schedule = settings.CELERY_BEAT_SCHEDULE
    names = {entry["task"] for entry in schedule.values()}
    assert {
        "apps.notifications.tasks.detect_project_delays",
        "apps.notifications.tasks.purge_old_notifications",
        "apps.core.tasks.purge_old_task_runs",
        "apps.core.tasks.cleanup_temp_files",
    } <= names
    assert settings.CELERY_TASK_ACKS_LATE is True
    assert settings.CELERY_WORKER_PREFETCH_MULTIPLIER == 1
    assert settings.CELERY_TASK_TIME_LIMIT > settings.CELERY_TASK_SOFT_TIME_LIMIT
