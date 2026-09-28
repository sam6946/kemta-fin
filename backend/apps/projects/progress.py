"""Calcul de l'avancement — **règle serveur unique** (voir `docs/data-model.md` §3).

Avancement d'une tâche  : `progress` (0-100), forcé à 100 si le statut est `DONE`.
Avancement d'un jalon   : moyenne pondérée de ses tâches (poids `weight`) ; si le jalon
                          n'a aucune tâche, 100 % s'il est terminé, 0 % sinon.
Avancement du projet    : moyenne pondérée des jalons non annulés (poids `weight`),
                          plus un « groupe » pour les tâches sans jalon.
                          Aucun jalon et aucune tâche → 0 %.

Ce calcul n'est **jamais** accepté depuis le client : `Project.progress` est en lecture
seule dans l'API et recalculé après chaque écriture de jalon ou de tâche.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from apps.projects.models import Milestone, MilestoneStatus, Project, Task, TaskStatus

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def quantize(progress: Decimal) -> Decimal:
    """Arrondi commercial à deux décimales (jamais de dépassement)."""
    return min(HUNDRED, max(ZERO, progress)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def weighted_average(items: list[tuple[Decimal, Decimal]]) -> Decimal | None:
    """Moyenne pondérée de couples `(valeur, poids)` ; `None` si aucun poids."""
    total_weight = sum((weight for _, weight in items), ZERO)
    if not items or total_weight <= ZERO:
        return None
    total = sum((value * weight for value, weight in items), ZERO)
    return total / total_weight


def task_progress(task: Task) -> Decimal:
    if task.status == TaskStatus.DONE:
        return HUNDRED
    if task.status == TaskStatus.CANCELLED:
        return ZERO
    return Decimal(task.progress or ZERO)


def tasks_progress(tasks: list[Task]) -> Decimal:
    """Moyenne pondérée des tâches **hors tâches annulées**.

    Une tâche annulée ne pénalise pas l'avancement : elle est retirée du ratio
    (numérateur *et* dénominateur), sinon abandonner du travail ferait chuter le
    pourcentage du chantier sans raison métier.
    """
    active = [task for task in tasks if task.status != TaskStatus.CANCELLED]
    result = weighted_average([(task_progress(task), Decimal(task.weight)) for task in active])
    return quantize(result) if result is not None else ZERO


def milestone_progress(milestone: Milestone, tasks: list[Task] | None = None) -> Decimal:
    """Avancement d'un jalon : ses tâches décident, sinon son statut."""
    milestone_tasks = tasks if tasks is not None else list(milestone.tasks.all())
    active = [task for task in milestone_tasks if task.status != TaskStatus.CANCELLED]
    if active:
        return tasks_progress(active)
    return HUNDRED if milestone.status == MilestoneStatus.DONE else ZERO


def compute_project_progress(
    project: Project,
    *,
    milestones: list[Milestone] | None = None,
    tasks: list[Task] | None = None,
) -> Decimal:
    """Avancement global du projet (0-100), calculé côté serveur.

    `milestones` et `tasks` peuvent être fournis **déjà chargés** : la vue planning les
    passe pour rester à un nombre constant de requêtes (aucune requête par jalon).
    """
    active_milestones = [
        milestone
        for milestone in (milestones if milestones is not None else project.milestones.all())
        if milestone.status != MilestoneStatus.CANCELLED
    ]
    active_tasks = [
        task
        for task in (tasks if tasks is not None else project.tasks.all())
        if task.status != TaskStatus.CANCELLED
    ]

    entries: list[tuple[Decimal, Decimal]] = []
    for milestone in active_milestones:
        milestone_tasks = [task for task in active_tasks if task.milestone_id == milestone.id]
        entries.append(
            (milestone_progress(milestone, milestone_tasks), Decimal(milestone.weight or 1))
        )

    orphan_tasks = [task for task in active_tasks if task.milestone_id is None]
    if orphan_tasks:
        entries.append(
            (
                tasks_progress(orphan_tasks),
                sum((Decimal(task.weight or 1) for task in orphan_tasks), ZERO),
            )
        )

    result = weighted_average(entries)
    return quantize(result) if result is not None else ZERO


def recalculate_project_progress(project: Project, *, save: bool = True) -> Decimal:
    """Recalcule et persiste l'avancement du projet ; renvoie la nouvelle valeur."""
    value = compute_project_progress(project)
    if Decimal(project.progress or ZERO) != value:
        project.progress = value
        if save:
            Project.objects.filter(pk=project.pk).update(progress=value)
    return value
