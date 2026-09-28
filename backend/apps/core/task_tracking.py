"""Suivi des tâches Celery : état, tentatives, durée, erreur (phase 10).

Les signaux Celery alimentent `TaskRun`, les métriques Prometheus et le journal. Une tâche
échouée est journalisée **avec son erreur** (logs applicatifs, `TaskRun.error` et un
événement `TASK_FAILED` dans le journal d'activité) ; les arguments ne sont jamais écrits.
"""

from __future__ import annotations

import logging
import time

from celery.signals import task_failure, task_postrun, task_prerun, task_retry
from django.utils import timezone

from . import metrics

logger = logging.getLogger("kemta.tasks")

_started: dict[str, float] = {}

# Tâches de suivi elles-mêmes : inutile (et récursif) de tracer leur propre nettoyage.
IGNORED_TASKS = frozenset({"apps.core.tasks.purge_old_task_runs"})


def _truncate(exc) -> str:
    return f"{exc.__class__.__name__}: {exc}"[:500]


def _upsert(task_id: str, name: str, **fields) -> None:
    from .models import TaskRun

    try:
        TaskRun.objects.update_or_create(task_id=task_id, defaults={"name": name, **fields})
    except Exception:  # le suivi ne doit jamais faire échouer la tâche
        logger.warning("Suivi de tâche impossible pour %s", name, exc_info=True)


@task_prerun.connect(weak=False)
def on_task_prerun(task_id=None, task=None, **_):
    if task is None or task.name in IGNORED_TASKS:
        return
    _started[task_id] = time.perf_counter()
    from .models import TaskRun

    _upsert(
        task_id,
        task.name,
        state=TaskRun.State.STARTED,
        retries=getattr(task.request, "retries", 0) or 0,
        started_at=timezone.now(),
    )


@task_postrun.connect(weak=False)
def on_task_postrun(task_id=None, task=None, state=None, **_):
    if task is None or task.name in IGNORED_TASKS:
        return
    elapsed = time.perf_counter() - _started.pop(task_id, time.perf_counter())
    from .models import TaskRun

    metrics.incr("kemta_celery_tasks_total", task=task.name, state=state or "UNKNOWN")
    metrics.observe("kemta_celery_task_duration_seconds", elapsed, task=task.name)
    if state == "SUCCESS":
        _upsert(
            task_id,
            task.name,
            state=TaskRun.State.SUCCESS,
            finished_at=timezone.now(),
            duration_ms=int(elapsed * 1000),
            error="",
        )


@task_retry.connect(weak=False)
def on_task_retry(request=None, reason=None, **_):
    if request is None or request.task in IGNORED_TASKS:
        return
    from .models import TaskRun

    logger.warning(
        "Tâche %s : nouvelle tentative %s (%s)",
        request.task,
        (request.retries or 0) + 1,
        reason.__class__.__name__ if reason is not None else "?",
    )
    _upsert(
        request.id,
        request.task,
        state=TaskRun.State.RETRY,
        retries=(request.retries or 0) + 1,
        error=_truncate(reason) if reason is not None else "",
    )


@task_failure.connect(weak=False)
def on_task_failure(task_id=None, exception=None, sender=None, **_):
    name = getattr(sender, "name", "") or "unknown"
    if name in IGNORED_TASKS:
        return
    from .models import ActivityLog, TaskRun

    elapsed = time.perf_counter() - _started.get(task_id, time.perf_counter())
    message = _truncate(exception) if exception is not None else ""
    logger.error("Tâche %s en échec définitif : %s", name, message)
    _upsert(
        task_id,
        name,
        state=TaskRun.State.FAILURE,
        error=message,
        finished_at=timezone.now(),
        duration_ms=int(elapsed * 1000),
        retries=getattr(getattr(sender, "request", None), "retries", 0) or 0,
    )
    try:
        from .activity import log_event

        log_event(
            ActivityLog.Action.TASK_FAILED,
            entity_type="CeleryTask",
            entity_id=task_id,
            metadata={"task": name, "error": message},
        )
    except Exception:  # pragma: no cover - garde-fou
        logger.warning("Journalisation de l'échec impossible", exc_info=True)
