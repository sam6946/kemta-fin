"""Invalidation du cache du dashboard : toute écriture qui peut changer un indicateur."""

from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_delete, post_save

from apps.core.models import ActivityLog
from apps.dashboard.cache import bump_project_version
from apps.evidences.models import Evidence, EvidenceValidation
from apps.finance.models import BudgetLine, Expense, FinancialTransaction, Payment
from apps.projects.models import Milestone, Project, ProjectMember, Task


def _project_id(instance) -> int | None:
    if isinstance(instance, Project):
        return instance.pk
    if isinstance(instance, EvidenceValidation):
        return instance.evidence.project_id
    if isinstance(instance, Payment):
        return instance.expense.project_id
    return getattr(instance, "project_id", None)


def _invalidate(sender, instance, **kwargs) -> None:
    try:
        project_id = _project_id(instance)
    except Exception:  # objet en cours de suppression en cascade : on ignore
        return
    if project_id is None:
        return
    # Deux invalidations : immédiate (lecture suivante correcte hors transaction) et après
    # validation (une lecture concurrente n'a pas pu re-remplir le cache avec l'ancien état).
    bump_project_version(project_id)
    transaction.on_commit(lambda: bump_project_version(project_id))


for model in (
    Project,
    ProjectMember,
    Milestone,
    Task,
    Evidence,
    EvidenceValidation,
    BudgetLine,
    Expense,
    Payment,
    FinancialTransaction,
    ActivityLog,
):
    post_save.connect(
        _invalidate, sender=model, weak=False, dispatch_uid=f"dash-save-{model.__name__}"
    )
    post_delete.connect(
        _invalidate, sender=model, weak=False, dispatch_uid=f"dash-del-{model.__name__}"
    )
