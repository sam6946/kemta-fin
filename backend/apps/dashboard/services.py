"""Calcul du dashboard projet et de l'espace de travail (phase 8, MVP-011).

Règles non négociables :

* **le backend calcule tout** : avancement, budget, solde, alertes — le frontend affiche ;
* **nombre de requêtes constant** : aucune requête par jalon, tâche, preuve ou dépense ; le
  test `test_dashboard_queries.py` le verrouille (même nombre de requêtes avec 3 ou 60 lignes) ;
* **collections bornées** : dernières preuves/dépenses/activités limitées par constante ;
* **alertes déterministes** : mêmes données + même date → mêmes alertes, sans aléa ni horloge
  cachée (la date de référence est fournie et renvoyée) ;
* **le rôle décide de ce qui est visible** : un investisseur ne voit ni la saisie terrain brute
  ni les dépenses non approuvées ; un agent terrain ne voit pas le budget.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from apps.core.models import ActivityLog
from apps.evidences.models import Evidence, EvidenceStatus
from apps.finance.access import finance_permissions_map
from apps.finance.models import COMMITTED_STATUSES, Expense, ExpenseStatus
from apps.finance.services import budget_summary
from apps.projects.access import (
    build_capabilities_map,
    build_project_roles_map,
    permissions_payload,
    project_role,
    resolve_capabilities,
)
from apps.projects.models import (
    FINAL_STATUSES,
    FINAL_TASK_STATUSES,
    Milestone,
    MilestoneStatus,
    Project,
    ProjectStatus,
    Task,
    TaskStatus,
)
from apps.projects.progress import compute_project_progress
from apps.users.roles import Capability, Role

LATEST_LIMIT = 5
ACTIVITY_LIMIT = 10
ALERT_LIMIT = 15
TASK_ALERT_LIMIT = 5
STALE_EVIDENCE_HOURS = 48
CRITICAL_TASK_DAYS = 14
CRITICAL_MILESTONE_DAYS = 7

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# Ce qu'un investisseur voit du journal : l'avancement et les décisions, pas la saisie.
INVESTOR_ACTIVITY_ACTIONS = (
    "MILESTONE_CREATED",
    "MILESTONE_UPDATED",
    "EVIDENCE_VALIDATED",
    "EXPENSE_APPROVED",
    "PAYMENT_RECORDED",
    "BUDGET_THRESHOLD_REACHED",
    "BUDGET_EXCEEDED",
    "PROJECT_UPDATED",
)


def audience_for(role: str | None) -> str:
    """Profil d'affichage déduit du rôle **sur ce projet**."""
    return {
        Role.INVESTOR: "investor",
        Role.ENGINEER: "engineer",
        Role.CONTRACTOR: "engineer",
        Role.FIELD_AGENT: "field",
        Role.VALIDATOR: "validator",
        Role.FINANCE: "finance",
    }.get(role or "", "manager")


def _person(user) -> dict | None:
    if user is None:
        return None
    return {"id": user.pk, "name": f"{user.first_name} {user.last_name}".strip()}


def _milestone_payload(milestone: Milestone, today) -> dict:
    return {
        "id": milestone.pk,
        "title": milestone.title,
        "status": milestone.status,
        "status_label": milestone.get_status_display(),
        "planned_date": milestone.planned_date,
        "actual_date": milestone.actual_date,
        "days_late": (today - milestone.planned_date).days
        if milestone.planned_date
        and milestone.planned_date < today
        and milestone.status not in FINAL_STATUSES
        else 0,
    }


def _schedule_section(project: Project, today) -> tuple[dict, dict, list[dict]]:
    """Avancement, derniers/prochains jalons et alertes de retard — 2 requêtes au total."""
    milestones = list(
        Milestone.objects.filter(project=project).order_by("order", "planned_date", "id")
    )
    tasks = list(
        Task.objects.filter(project=project).only(
            "id", "title", "status", "progress", "weight", "milestone_id", "planned_end_date"
        )
    )
    progress = compute_project_progress(project, milestones=milestones, tasks=tasks)

    late_tasks = sorted(
        (
            (task, (today - task.planned_end_date).days)
            for task in tasks
            if task.planned_end_date
            and task.planned_end_date < today
            and task.status not in FINAL_TASK_STATUSES
        ),
        key=lambda pair: (-pair[1], pair[0].pk),
    )
    late_milestones = sorted(
        (
            (milestone, (today - milestone.planned_date).days)
            for milestone in milestones
            if milestone.planned_date
            and milestone.planned_date < today
            and milestone.status not in FINAL_STATUSES
        ),
        key=lambda pair: (-pair[1], pair[0].pk),
    )

    done = [m for m in milestones if m.status == MilestoneStatus.DONE]
    last_done = max(
        done, key=lambda m: (m.actual_date or m.planned_date or today, m.pk), default=None
    )
    upcoming = [
        m
        for m in milestones
        if m.status not in FINAL_STATUSES and (m.planned_date is None or m.planned_date >= today)
    ]
    next_milestone = min(
        upcoming,
        key=lambda m: (m.planned_date is None, m.planned_date or today, m.order, m.pk),
        default=None,
    )

    summary = {
        "progress": float(progress),
        "milestones_total": len(milestones),
        "milestones_done": len(done),
        "milestones_late": len(late_milestones),
        "tasks_total": len(tasks),
        "tasks_done": len([t for t in tasks if t.status == TaskStatus.DONE]),
        "tasks_late": len(late_tasks),
    }
    milestone_block = {
        "last_completed": _milestone_payload(last_done, today) if last_done else None,
        "next": _milestone_payload(next_milestone, today) if next_milestone else None,
    }

    alerts: list[dict] = []
    for milestone, days in late_milestones[:TASK_ALERT_LIMIT]:
        alerts.append(
            {
                "code": "MILESTONE_LATE",
                "severity": "critical" if days >= CRITICAL_MILESTONE_DAYS else "warning",
                "message": f"Jalon « {milestone.title} » en retard de {days} jour(s).",
                "entity_type": "Milestone",
                "entity_id": milestone.pk,
                "days_late": days,
            }
        )
    for task, days in late_tasks[:TASK_ALERT_LIMIT]:
        alerts.append(
            {
                "code": "TASK_LATE",
                "severity": "critical" if days >= CRITICAL_TASK_DAYS else "warning",
                "message": f"Tâche « {task.title} » en retard de {days} jour(s).",
                "entity_type": "Task",
                "entity_id": task.pk,
                "days_late": days,
            }
        )
    if len(late_tasks) > TASK_ALERT_LIMIT:
        alerts.append(
            {
                "code": "TASKS_LATE_MORE",
                "severity": "info",
                "message": f"{len(late_tasks) - TASK_ALERT_LIMIT} autre(s) tâche(s) en retard.",
            }
        )
    if (
        project.status == ProjectStatus.ACTIVE
        and project.planned_end_date
        and project.planned_end_date < today
        and progress < 100
    ):
        days = (today - project.planned_end_date).days
        alerts.append(
            {
                "code": "PROJECT_END_PASSED",
                "severity": "critical",
                "message": f"La fin prévue du projet est dépassée de {days} jour(s).",
                "days_late": days,
            }
        )
    if not milestones and not tasks:
        alerts.append(
            {
                "code": "NO_PLANNING",
                "severity": "info",
                "message": "Aucun jalon ni aucune tâche : planifiez le chantier pour suivre l'avancement.",
            }
        )
    return summary, milestone_block, alerts


def _evidence_section(project: Project, *, only_validated: bool, now) -> tuple[dict, list]:
    """Compteurs par statut (1 requête) et dernières preuves (1 requête)."""
    counts_row = Evidence.objects.filter(project=project).aggregate(
        pending=Count("id", filter=Q(status=EvidenceStatus.PENDING)),
        validated=Count("id", filter=Q(status=EvidenceStatus.VALIDATED)),
        rejected=Count("id", filter=Q(status=EvidenceStatus.REJECTED)),
        flagged=Count("id", filter=Q(status=EvidenceStatus.FLAGGED)),
        stale=Count(
            "id",
            filter=Q(
                status=EvidenceStatus.PENDING,
                captured_at__lt=now - timedelta(hours=STALE_EVIDENCE_HOURS),
            ),
        ),
    )
    latest_qs = Evidence.objects.filter(project=project).select_related("author", "task")
    if only_validated:
        latest_qs = latest_qs.filter(status=EvidenceStatus.VALIDATED)
    latest = [
        {
            "id": evidence.pk,
            "status": evidence.status,
            "status_label": evidence.get_status_display(),
            "captured_at": evidence.captured_at,
            "author": _person(evidence.author),
            "task_title": evidence.task.title if evidence.task_id else None,
            "description": evidence.description[:140],
            # Chemin relatif vers la miniature : les listes n'ouvrent jamais l'original.
            "thumbnail_url": f"/api/evidences/{evidence.pk}/"
            f"{'thumbnail' if evidence.thumbnail else 'file'}/",
        }
        for evidence in latest_qs.order_by("-captured_at", "-id")[:LATEST_LIMIT]
    ]
    counts = {key: counts_row[key] for key in ("pending", "validated", "rejected", "flagged")}
    if only_validated:
        counts = {"validated": counts["validated"]}
    counts["stale"] = counts_row["stale"] if not only_validated else 0
    return counts, latest


def _expense_section(project: Project, *, only_committed: bool) -> tuple[dict, list]:
    counts_row = Expense.objects.filter(project=project).aggregate(
        draft=Count("id", filter=Q(status=ExpenseStatus.DRAFT)),
        submitted=Count("id", filter=Q(status=ExpenseStatus.SUBMITTED)),
        approved=Count("id", filter=Q(status=ExpenseStatus.APPROVED)),
        paid=Count("id", filter=Q(status=ExpenseStatus.PAID)),
        rejected=Count("id", filter=Q(status=ExpenseStatus.REJECTED)),
    )
    latest_qs = Expense.objects.filter(project=project).select_related("created_by")
    if only_committed:
        latest_qs = latest_qs.filter(status__in=COMMITTED_STATUSES)
    latest = [
        {
            "id": expense.pk,
            "title": expense.title,
            "amount": int(expense.amount),
            "status": expense.status,
            "status_label": expense.get_status_display(),
            "incurred_on": expense.incurred_on,
            "supplier": expense.supplier,
        }
        for expense in latest_qs.order_by("-incurred_on", "-id")[:LATEST_LIMIT]
    ]
    return dict(counts_row), latest


def _activity_section(project: Project, *, investor: bool) -> list[dict]:
    queryset = ActivityLog.objects.filter(project=project).select_related("actor")
    if investor:
        queryset = queryset.filter(action__in=INVESTOR_ACTIVITY_ACTIONS)
    return [
        {
            "id": event.pk,
            "action": event.action,
            "action_label": event.get_action_display(),
            "actor": _person(event.actor),
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "created_at": event.created_at,
        }
        for event in queryset.order_by("-created_at", "-id")[:ACTIVITY_LIMIT]
    ]


def _budget_section(project: Project) -> tuple[dict, list[dict]]:
    summary = budget_summary(project)
    budget = {
        key: summary[key]
        for key in (
            "planned",
            "committed",
            "paid",
            "outstanding",
            "balance",
            "consumption_rate",
            "threshold",
            "currency",
        )
    }
    budget = {
        key: (int(value) if key not in {"consumption_rate", "threshold", "currency"} else value)
        for key, value in budget.items()
    }
    budget["consumption_rate"] = float(summary["consumption_rate"])
    return budget, list(summary["alerts"])


def build_project_dashboard(user, project: Project, *, today=None) -> dict:
    """Réponse complète de `GET /api/projects/{id}/dashboard/` pour un utilisateur autorisé."""
    today = today or timezone.localdate()
    now = timezone.now()

    role = project_role(user, project)
    capabilities = resolve_capabilities(user, project)
    permissions = permissions_payload(capabilities)
    audience = audience_for(role)
    investor = audience == "investor"
    can_view_finance = bool(capabilities.get(Capability.VIEW_FINANCE))

    progress, milestones, alerts = _schedule_section(project, today)

    budget = None
    finance_permissions = None
    expenses = None
    if can_view_finance:
        budget, budget_alerts = _budget_section(project)
        alerts.extend(budget_alerts)
        expense_counts, expense_latest = _expense_section(project, only_committed=investor)
        if investor:
            expense_counts = {
                "approved": expense_counts["approved"],
                "paid": expense_counts["paid"],
            }
        expenses = {"counts": expense_counts, "latest": expense_latest}
        finance_permissions = finance_permissions_map(user, [project])[project.pk]

    counts, latest = _evidence_section(project, only_validated=investor, now=now)
    evidences = {"counts": counts, "latest": latest}
    if (
        not investor
        and counts["stale"]
        and (
            capabilities.get(Capability.VALIDATE_EVIDENCE)
            or capabilities.get(Capability.MANAGE_SCHEDULE)
        )
    ):
        alerts.append(
            {
                "code": "EVIDENCE_PENDING_STALE",
                "severity": "warning",
                "message": f"{counts['stale']} preuve(s) attendent une validation depuis "
                f"plus de {STALE_EVIDENCE_HOURS} h.",
            }
        )

    activity = None
    if capabilities.get(Capability.VIEW_ACTIVITY):
        activity = _activity_section(project, investor=investor)

    alerts.sort(key=lambda alert: (SEVERITY_ORDER.get(alert["severity"], 9), alert["code"]))
    alerts_total = len(alerts)

    return {
        "reference_date": today,
        "generated_at": now,
        "audience": audience,
        "role": role,
        "project": {
            "id": project.pk,
            "name": project.name,
            "code": project.code,
            "status": project.status,
            "status_label": project.get_status_display(),
            "city": project.city,
            "region": project.region,
            "currency": project.currency,
            "planned_start_date": project.planned_start_date,
            "planned_end_date": project.planned_end_date,
            "days_to_end": (project.planned_end_date - today).days
            if project.planned_end_date
            else None,
        },
        "progress": progress,
        "milestones": milestones,
        "budget": budget,
        "alerts": alerts[:ALERT_LIMIT],
        "alerts_total": alerts_total,
        "evidences": evidences,
        "expenses": expenses,
        "activity": activity,
        "permissions": {**permissions, **(finance_permissions or {})},
    }


# ---------------------------------------------------------------------------
# Espace de travail (ingénieur / PME / pilotage)
# ---------------------------------------------------------------------------
WORKSPACE_PROJECT_LIMIT = 20
WORKSPACE_TASK_LIMIT = 20
WORKSPACE_QUEUE_LIMIT = 10


def build_workspace(user, projects_qs, *, today=None) -> dict:
    """Vue transverse de l'utilisateur : ses chantiers, ses tâches, ce qui attend sa décision.

    Toutes les requêtes portent sur l'ensemble des projets accessibles (jamais une boucle par
    projet) : le coût ne dépend pas du nombre de chantiers.
    """
    today = today or timezone.localdate()
    projects = list(projects_qs.select_related("organization").order_by("-created_at")[:200])
    project_ids = [project.pk for project in projects]
    capabilities = build_capabilities_map(user, projects)
    roles = build_project_roles_map(user, projects)
    finance_perms = finance_permissions_map(user, projects)

    late_tasks = dict(
        Task.objects.filter(project_id__in=project_ids, planned_end_date__lt=today)
        .exclude(status__in=FINAL_TASK_STATUSES)
        .values_list("project_id")
        .annotate(total=Count("id"))
    )
    late_milestones = dict(
        Milestone.objects.filter(project_id__in=project_ids, planned_date__lt=today)
        .exclude(status__in=FINAL_STATUSES)
        .values_list("project_id")
        .annotate(total=Count("id"))
    )
    pending_evidences = dict(
        Evidence.objects.filter(project_id__in=project_ids, status=EvidenceStatus.PENDING)
        .exclude(author=user)
        .values_list("project_id")
        .annotate(total=Count("id"))
    )
    submitted_expenses = dict(
        Expense.objects.filter(project_id__in=project_ids, status=ExpenseStatus.SUBMITTED)
        .values_list("project_id")
        .annotate(total=Count("id"))
    )

    cards = []
    for project in projects:
        can_finance = bool(capabilities[project.pk].get(Capability.VIEW_FINANCE))
        validate = bool(capabilities[project.pk].get(Capability.VALIDATE_EVIDENCE))
        settle = bool(finance_perms[project.pk]["settle_finance"])
        cards.append(
            {
                "id": project.pk,
                "name": project.name,
                "code": project.code,
                "organization_name": project.organization.name,
                "status": project.status,
                "status_label": project.get_status_display(),
                "progress": float(project.progress),
                "role": roles.get(project.pk),
                "tasks_late": late_tasks.get(project.pk, 0),
                "milestones_late": late_milestones.get(project.pk, 0),
                "evidences_to_validate": pending_evidences.get(project.pk, 0) if validate else 0,
                "expenses_to_approve": submitted_expenses.get(project.pk, 0) if settle else 0,
                "planned_end_date": project.planned_end_date,
                "budget_visible": can_finance,
            }
        )
    # Ce qui demande de l'attention d'abord (retards, puis file de décision), puis le reste.
    cards.sort(
        key=lambda card: (
            -(card["tasks_late"] + card["milestones_late"]),
            -(card["evidences_to_validate"] + card["expenses_to_approve"]),
            card["name"].lower(),
        )
    )

    my_tasks = [
        {
            "id": task.pk,
            "title": task.title,
            "status": task.status,
            "status_label": task.get_status_display(),
            "progress": float(task.progress),
            "planned_end_date": task.planned_end_date,
            "days_late": (today - task.planned_end_date).days
            if task.planned_end_date and task.planned_end_date < today
            else 0,
            "project": task.project_id,
            "project_name": task.project.name,
        }
        for task in Task.objects.filter(assignee=user, project_id__in=project_ids)
        .exclude(status__in=FINAL_TASK_STATUSES)
        .select_related("project")
        .order_by("planned_end_date", "id")[:WORKSPACE_TASK_LIMIT]
    ]

    validatable_ids = [
        pid for pid in project_ids if capabilities[pid].get(Capability.VALIDATE_EVIDENCE)
    ]
    to_validate_qs = (
        Evidence.objects.filter(project_id__in=validatable_ids, status=EvidenceStatus.PENDING)
        .exclude(author=user)
        .select_related("project", "author")
        .order_by("captured_at")
    )
    to_validate = {
        "count": to_validate_qs.count(),
        "items": [
            {
                "id": evidence.pk,
                "project": evidence.project_id,
                "project_name": evidence.project.name,
                "captured_at": evidence.captured_at,
                "author": _person(evidence.author),
                "thumbnail_url": f"/api/evidences/{evidence.pk}/"
                f"{'thumbnail' if evidence.thumbnail else 'file'}/",
            }
            for evidence in to_validate_qs[:WORKSPACE_QUEUE_LIMIT]
        ],
    }

    settle_ids = [pid for pid in project_ids if finance_perms[pid]["settle_finance"]]
    to_approve_qs = (
        Expense.objects.filter(project_id__in=settle_ids, status=ExpenseStatus.SUBMITTED)
        .exclude(created_by=user)
        .select_related("project")
        .order_by("created_at")
    )
    to_approve = {
        "count": to_approve_qs.count(),
        "items": [
            {
                "id": expense.pk,
                "project": expense.project_id,
                "project_name": expense.project.name,
                "title": expense.title,
                "amount": int(expense.amount),
            }
            for expense in to_approve_qs[:WORKSPACE_QUEUE_LIMIT]
        ],
    }

    roles_set = {role for role in roles.values() if role}
    if roles_set & {Role.PLATFORM_ADMIN, Role.ORG_OWNER, Role.PROJECT_OWNER}:
        profile = "manager"
    elif roles_set & {Role.ENGINEER, Role.CONTRACTOR}:
        profile = "engineer"
    elif roles_set == {Role.INVESTOR}:
        profile = "investor"
    elif roles_set & {Role.FINANCE}:
        profile = "finance"
    elif roles_set:
        profile = "field"
    else:
        profile = "none"

    return {
        "reference_date": today,
        "profile": profile,
        "totals": {
            "projects": len(cards),
            "tasks_late": sum(card["tasks_late"] for card in cards),
            "milestones_late": sum(card["milestones_late"] for card in cards),
            "evidences_to_validate": to_validate["count"],
            "expenses_to_approve": to_approve["count"],
            "my_open_tasks": len(my_tasks),
        },
        "projects": cards[:WORKSPACE_PROJECT_LIMIT],
        "projects_truncated": len(cards) > WORKSPACE_PROJECT_LIMIT,
        "my_tasks": my_tasks,
        "to_validate": to_validate,
        "to_approve": to_approve,
    }
