"""Dépendances entre tâches : graphe **acyclique** (voir `docs/data-model.md` §3).

`Task.depends_on` porte les edges « cette tâche dépend de … ». Refuser les cycles au
moment de l'écriture évite qu'un planning bloque définitivement (A attend B qui attend A) :
le calcul d'avancement et l'ordonnancement restent alors toujours résolubles.
"""

from __future__ import annotations

from apps.projects.models import Task

# Garde-fou : un planning MVP reste petit ; au-delà, on refuse plutôt que de boucler.
MAX_TRAVERSED_TASKS = 500


def dependency_closure(task_ids) -> set[int]:
    """Identifiants atteignables en suivant `depends_on` depuis `task_ids` (exclut le départ)."""
    seen: set[int] = set()
    stack = [int(pk) for pk in task_ids]
    while stack:
        if len(seen) > MAX_TRAVERSED_TASKS:
            break
        current = stack.pop()
        for dependency_id in Task.objects.filter(pk=current).values_list(
            "depends_on__id", flat=True
        ):
            if dependency_id is not None and dependency_id not in seen:
                seen.add(dependency_id)
                stack.append(dependency_id)
    return seen


def creates_cycle(task: Task, new_dependencies) -> bool:
    """`True` si ajouter ces dépendances à `task` referme une boucle."""
    if not new_dependencies:
        return False
    ids = {dependency.pk for dependency in new_dependencies}
    if task.pk in ids:
        return True
    return task.pk in dependency_closure(ids)
