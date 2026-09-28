"""Services financiers (MVP-010) — **seul chemin d'écriture** du budget, des dépenses et des
paiements.

Chaque écriture suit le même schéma, sans exception :

1. `transaction.atomic()` ;
2. **verrou** `select_for_update()` sur le projet (et sur la dépense pour un paiement) : deux
   paiements concurrents ne peuvent pas dépasser le montant dû, et le solde ne peut pas être
   calculé deux fois de suite sur la même base ;
3. vérification des permissions puis des règles métier (montants FCFA entiers, machine à états,
   plafond budgétaire) ;
4. écriture d'une ligne de **grand livre** append-only, qui porte le solde après opération ;
5. journalisation (`ActivityLog`) avec ancienne et nouvelle valeur ;
6. alerte déterministe si un seuil budgétaire (80 % / 100 %) vient d'être franchi.

Le consommé, le payé et le solde ne sont **jamais** reçus du client : ils sont recalculés en SQL
depuis le grand livre (tout champ de total envoyé est ignoré).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.activity import log_event
from apps.core.exceptions import KemtaAPIError
from apps.core.money import validate_fcfa_amount
from apps.finance.access import can_manage_finance, can_settle_finance
from apps.finance.models import (
    COMMITTED_STATUSES,
    EXPENSE_TRANSITIONS,
    BudgetLine,
    Expense,
    ExpenseStatus,
    FinancialTransaction,
    Payment,
    TransactionDirection,
    TransactionType,
)
from apps.projects.access import is_platform_admin
from apps.projects.models import Project

ZERO = Decimal("0")
WARNING_RATE = Decimal("80")
FULL_RATE = Decimal("100")
MIN_OVERRIDE_REASON_LENGTH = 10


# --------------------------------------------------------------------------- utilitaires
def locked_project(project_id: int) -> Project:
    """Verrouille la ligne projet : sérialise toutes les écritures financières du chantier."""
    return Project.objects.select_for_update().get(pk=project_id)


def locked_expense(expense_id: int, project: Project) -> Expense:
    return (
        Expense.objects.select_for_update()
        .select_related("budget_line")
        .get(pk=expense_id, project=project)
    )


def _sum(queryset, field: str = "amount") -> Decimal:
    return queryset.aggregate(total=Sum(field))["total"] or ZERO


def ledger_totals(project: Project) -> dict[str, Decimal]:
    """Agrégats du grand livre : engagé, payé, ajustements (jamais des valeurs client)."""
    entries = FinancialTransaction.objects.filter(project=project)
    expense_debit = _sum(
        entries.filter(type=TransactionType.EXPENSE, direction=TransactionDirection.DEBIT)
    )
    payment_debit = _sum(
        entries.filter(type=TransactionType.PAYMENT, direction=TransactionDirection.DEBIT)
    )
    cancels = entries.filter(
        type=TransactionType.CANCELLATION, direction=TransactionDirection.CREDIT
    )
    # Une contre-écriture de **paiement** porte aussi la dépense : elle ne libère pas
    # l'engagement, elle annule un décaissement. Seule l'annulation de la dépense le fait.
    expense_cancelled = _sum(cancels.filter(expense__isnull=False, payment__isnull=True))
    payment_cancelled = _sum(cancels.filter(payment__isnull=False))
    adjustments = entries.filter(type=TransactionType.ADJUSTMENT)
    adjustment_net = _sum(adjustments.filter(direction=TransactionDirection.DEBIT)) - _sum(
        adjustments.filter(direction=TransactionDirection.CREDIT)
    )
    committed = expense_debit - expense_cancelled + adjustment_net
    paid = payment_debit - payment_cancelled
    return {
        "committed": committed,
        "paid": paid,
        "expense_committed": expense_debit - expense_cancelled,
        "payment_cancelled": payment_cancelled,
        "adjustments": adjustment_net,
    }


def line_committed_map(project: Project, lines=None) -> dict[int, Decimal]:
    """Consommé de **chaque** poste budgétaire, en un nombre constant de requêtes.

    Utilisé par la synthèse et les listes : afficher le consommé de 40 postes ne doit jamais
    produire 40 requêtes (règle anti-N+1 du projet).
    """
    if lines is None:
        line_ids = list(BudgetLine.objects.filter(project=project).values_list("id", flat=True))
    else:
        line_ids = [line.pk for line in lines]
    if not line_ids:
        return {}
    entries = FinancialTransaction.objects.filter(project=project, budget_line_id__in=line_ids)
    debits = dict(
        entries.filter(type=TransactionType.EXPENSE, direction=TransactionDirection.DEBIT)
        .values_list("budget_line_id")
        .annotate(total=Sum("amount"))
    )
    # Idem : seule l'annulation d'une **dépense** (et non d'un paiement) libère un poste.
    credits = dict(
        entries.filter(
            type=TransactionType.CANCELLATION,
            direction=TransactionDirection.CREDIT,
            payment__isnull=True,
        )
        .values_list("budget_line_id")
        .annotate(total=Sum("amount"))
    )
    return {line_id: debits.get(line_id, ZERO) - credits.get(line_id, ZERO) for line_id in line_ids}


def line_committed(project: Project, budget_line: BudgetLine | None) -> Decimal:
    """Consommé d'un poste isolé (utilisé hors liste : fiche d'une dépense)."""
    if budget_line is None:
        return ZERO
    return line_committed_map(project, [budget_line]).get(budget_line.pk, ZERO)


def consumption_rate(planned: Decimal, committed: Decimal) -> Decimal:
    """Taux de consommation en pourcentage, borné à deux décimales (jamais envoyé par le client)."""
    if planned <= 0:
        return Decimal("0.00") if committed <= 0 else Decimal("100.00")
    return (committed * 100 / planned).quantize(Decimal("0.01"))


def _threshold(rate: Decimal) -> str:
    if rate >= FULL_RATE:
        return "EXCEEDED"
    if rate >= WARNING_RATE:
        return "WARNING"
    return "OK"


def budget_alerts(project: Project, *, totals=None, lines=None, consumed=None) -> list[dict]:
    """Alertes budgétaires **déterministes** (utilisées telles quelles par l'interface).

    `totals`, `lines` et `consumed` peuvent être fournis par l'appelant (la synthèse les a
    déjà calculés) : les alertes ne relancent jamais les mêmes agrégats.
    """
    totals = ledger_totals(project) if totals is None else totals
    rate = consumption_rate(project.budget_total, totals["committed"])
    alerts: list[dict] = []
    if rate >= FULL_RATE:
        alerts.append(
            {
                "code": "BUDGET_EXCEEDED",
                "severity": "critical",
                "message": (
                    f"Budget dépassé : {totals['committed']} FCFA engagés pour "
                    f"{project.budget_total} FCFA prévus."
                ),
                "amount": int(totals["committed"] - project.budget_total),
            }
        )
    elif rate >= WARNING_RATE:
        alerts.append(
            {
                "code": "BUDGET_THRESHOLD_REACHED",
                "severity": "warning",
                "message": f"Seuil de 80 % du budget atteint ({rate} %).",
                "amount": int(project.budget_total - totals["committed"]),
            }
        )
    lines = list(BudgetLine.objects.filter(project=project)) if lines is None else lines
    consumed_by_line = line_committed_map(project, lines) if consumed is None else consumed
    for line in lines:
        consumed = consumed_by_line.get(line.pk, ZERO)
        if consumed > line.planned_amount:
            alerts.append(
                {
                    "code": "BUDGET_LINE_EXCEEDED",
                    "severity": "warning",
                    "message": f"Poste « {line.label} » dépassé : {consumed} FCFA engagés.",
                    "budget_line": line.pk,
                    "amount": int(consumed - line.planned_amount),
                }
            )
    return alerts


def budget_summary(project: Project) -> dict:
    """Synthèse financière du projet : tout est calculé côté serveur, à partir du grand livre."""
    totals = ledger_totals(project)
    committed = totals["committed"]
    paid = totals["paid"]
    planned = project.budget_total
    lines_objects = list(BudgetLine.objects.filter(project=project))
    lines_planned = sum((line.planned_amount for line in lines_objects), ZERO)
    consumed_by_line = line_committed_map(project, lines_objects)
    rate = consumption_rate(planned, committed)
    return {
        "planned": planned,
        "allocated": lines_planned,
        "unallocated": planned - lines_planned,
        "committed": committed,
        "paid": paid,
        "outstanding": committed - paid,
        "balance": planned - committed,
        "consumption_rate": rate,
        "threshold": _threshold(rate),
        "currency": project.currency,
        "lines": [
            {
                "budget_line": line.pk,
                "label": line.label,
                "category": line.category,
                "planned": line.planned_amount,
                "committed": consumed_by_line.get(line.pk, ZERO),
            }
            for line in lines_objects
        ],
        "alerts": budget_alerts(
            project, totals=totals, lines=lines_objects, consumed=consumed_by_line
        ),
        "generated_at": timezone.now(),
    }


def _log_threshold_crossing(
    *, project: Project, before_committed: Decimal, after_committed: Decimal, actor, request
) -> None:
    """Journalise **au franchissement** (pas à chaque écriture) les seuils 80 % et 100 %."""
    before = consumption_rate(project.budget_total, before_committed)
    after = consumption_rate(project.budget_total, after_committed)
    if before < WARNING_RATE <= after:
        log_event(
            "BUDGET_THRESHOLD_REACHED",
            actor=actor,
            entity_type="Project",
            entity_id=project.pk,
            organization=project.organization,
            project=project,
            metadata={
                "threshold_percent": 80,
                "consumption_rate": str(after),
                "committed": int(after_committed),
                "planned": int(project.budget_total),
            },
            request=request,
        )
    if before < FULL_RATE <= after:
        log_event(
            "BUDGET_EXCEEDED",
            actor=actor,
            entity_type="Project",
            entity_id=project.pk,
            organization=project.organization,
            project=project,
            metadata={
                "threshold_percent": 100,
                "consumption_rate": str(after),
                "committed": int(after_committed),
                "planned": int(project.budget_total),
            },
            request=request,
        )


@dataclass
class LedgerEffect:
    """Effet d'une écriture sur le consommé (pour recalculer le solde après opération)."""

    committed_delta: Decimal = ZERO
    paid_delta: Decimal = ZERO


def _write_ledger(
    *,
    project: Project,
    transaction_type: str,
    direction: str,
    amount: Decimal,
    actor,
    effect: LedgerEffect,
    expense: Expense | None = None,
    payment: Payment | None = None,
    budget_line: BudgetLine | None = None,
    note: str = "",
) -> FinancialTransaction:
    """Écrit une ligne de grand livre et renvoie le solde après opération."""
    totals = ledger_totals(project)
    committed_after = totals["committed"] + effect.committed_delta
    balance_after = project.budget_total - committed_after
    return FinancialTransaction.objects.create(
        project=project,
        type=transaction_type,
        direction=direction,
        amount=amount,
        expense=expense,
        payment=payment,
        budget_line=budget_line,
        balance_after=balance_after,
        note=note,
        created_by=actor,
    )


def _require_manage(user, project: Project) -> None:
    if not can_manage_finance(user, project):
        raise KemtaAPIError(
            "permission_denied",
            "Vous n'avez pas la permission de gérer les finances de ce projet.",
            http_status=403,
        )


def _require_settle(user, project: Project) -> None:
    if not can_settle_finance(user, project):
        raise KemtaAPIError(
            "permission_denied",
            "Seul un rôle de pilotage financier (maître d'ouvrage, promoteur, finance) peut "
            "engager, approuver ou payer une dépense sur ce projet.",
            http_status=403,
        )


def _fcfa(value, *, field_name: str, positive: bool = True) -> Decimal:
    amount = validate_fcfa_amount(value, field=field_name)
    if positive and amount <= 0:
        raise KemtaAPIError(
            "amount_not_positive",
            "Le montant doit être strictement supérieur à zéro.",
            details={"field": field_name},
        )
    return amount


# --------------------------------------------------------------------------- postes budgétaires
def _check_allocation(project: Project, *, extra: Decimal = ZERO, extra_label: str = "") -> None:
    """La somme des postes ne peut pas dépasser le budget global du projet.

    `extra` permet de vérifier une modification **avant** de l'écrire (nouveau poste, ou nouveau
    montant d'un poste existant dont la ligne a déjà été retirée du total par l'appelant).
    """
    allocated = _sum(BudgetLine.objects.filter(project=project), "planned_amount") + extra
    if allocated > project.budget_total:
        raise KemtaAPIError(
            "budget_lines_exceed_budget",
            "La somme des postes budgétaires dépasse le budget global du projet."
            + (f" (poste « {extra_label} »)" if extra_label else ""),
            http_status=422,
            details={
                "allocated": int(allocated),
                "planned": int(project.budget_total),
                "over": int(allocated - project.budget_total),
            },
        )


def create_budget_line(*, project: Project, actor, data: dict, request=None) -> BudgetLine:
    with transaction.atomic():
        project = locked_project(project.pk)
        _require_settle(actor, project)
        planned = _fcfa(data.get("planned_amount"), field_name="planned_amount", positive=False)

        line = BudgetLine(
            project=project,
            label=(data.get("label") or "").strip(),
            category=data.get("category") or "MATERIALS",
            planned_amount=planned,
            order=int(data.get("order") or 0),
            notes=data.get("notes") or "",
            created_by=actor,
        )
        if not line.label:
            raise KemtaAPIError("label_required", "Le libellé du poste budgétaire est obligatoire.")
        if BudgetLine.objects.filter(project=project, label=line.label).exists():
            raise KemtaAPIError(
                "budget_line_already_exists",
                "Un poste budgétaire porte déjà ce libellé sur ce projet.",
                http_status=409,
            )
        line.clean()
        # Le total incluant le nouveau poste doit rester dans le budget global du projet.
        _check_allocation(project, extra=planned, extra_label=line.label)
        line.save()

        log_event(
            "BUDGET_LINE_CREATED",
            actor=actor,
            entity_type="BudgetLine",
            entity_id=line.pk,
            organization=project.organization,
            project=project,
            metadata={
                "label": line.label,
                "category": line.category,
                "planned_amount": int(line.planned_amount),
            },
            request=request,
        )
        return line


def update_budget_line(*, line: BudgetLine, actor, data: dict, request=None) -> BudgetLine:
    with transaction.atomic():
        project = locked_project(line.project_id)
        _require_settle(actor, project)
        line = BudgetLine.objects.select_for_update().get(pk=line.pk)

        before = {
            "label": line.label,
            "category": line.category,
            "planned_amount": int(line.planned_amount),
            "order": line.order,
            "notes": line.notes,
        }
        if "label" in data:
            label = (data.get("label") or "").strip()
            if not label:
                raise KemtaAPIError(
                    "label_required", "Le libellé du poste budgétaire est obligatoire."
                )
            line.label = label
        if "category" in data:
            line.category = data["category"]
        if "order" in data:
            line.order = int(data["order"] or 0)
        if "notes" in data:
            line.notes = data["notes"] or ""
        if "planned_amount" in data:
            previous_amount = line.planned_amount
            line.planned_amount = _fcfa(
                data.get("planned_amount"), field_name="planned_amount", positive=False
            )
            # La ligne est déjà comptée dans le total : on vérifie l'écart, pas le montant entier.
            _check_allocation(
                project, extra=line.planned_amount - previous_amount, extra_label=line.label
            )

        line.clean()
        line.save()

        after = {
            "label": line.label,
            "category": line.category,
            "planned_amount": int(line.planned_amount),
            "order": line.order,
            "notes": line.notes,
        }
        changed = {
            key: {"old": before[key], "new": after[key]}
            for key in before
            if before[key] != after[key]
        }
        log_event(
            "BUDGET_LINE_UPDATED",
            actor=actor,
            entity_type="BudgetLine",
            entity_id=line.pk,
            organization=project.organization,
            project=project,
            metadata={"label": line.label, "changed": changed},
            request=request,
        )
        return line


def delete_budget_line(*, line: BudgetLine, actor, request=None) -> None:
    with transaction.atomic():
        project = locked_project(line.project_id)
        _require_settle(actor, project)
        line = BudgetLine.objects.select_for_update().get(pk=line.pk)
        if Expense.objects.filter(budget_line=line).exists():
            raise KemtaAPIError(
                "budget_line_in_use",
                "Ce poste porte des dépenses : conservez-le (l'historique doit rester lisible).",
                http_status=409,
            )
        label = line.label
        line.delete()  # suppression logique
        log_event(
            "BUDGET_LINE_DELETED",
            actor=actor,
            entity_type="BudgetLine",
            entity_id=line.pk,
            organization=project.organization,
            project=project,
            metadata={"label": label},
            request=request,
        )


# --------------------------------------------------------------------------- dépenses
def create_expense(*, project: Project, actor, data: dict, request=None) -> Expense:
    with transaction.atomic():
        project = locked_project(project.pk)
        _require_manage(actor, project)

        amount = _fcfa(data.get("amount"), field_name="amount")
        budget_line = _resolve_budget_line(project, data.get("budget_line"))
        expense = Expense(
            project=project,
            budget_line=budget_line,
            title=(data.get("title") or "").strip(),
            description=data.get("description") or "",
            amount=amount,
            currency=project.currency,
            incurred_on=data.get("incurred_on"),
            status=ExpenseStatus.DRAFT,
            supplier=(data.get("supplier") or "").strip(),
            invoice_number=(data.get("invoice_number") or "").strip(),
            invoice_date=data.get("invoice_date") or None,
            created_by=actor,
        )
        if not expense.title:
            raise KemtaAPIError("title_required", "Le libellé de la dépense est obligatoire.")
        if expense.incurred_on is None:
            raise KemtaAPIError("incurred_on_required", "La date de la dépense est obligatoire.")
        _check_invoice_uniqueness(project, expense.invoice_number)
        expense.clean()
        expense.save()

        log_event(
            "EXPENSE_CREATED",
            actor=actor,
            entity_type="Expense",
            entity_id=expense.pk,
            organization=project.organization,
            project=project,
            metadata={
                "title": expense.title,
                "amount": int(expense.amount),
                "status": expense.status,
                "budget_line": expense.budget_line_id,
                "supplier": expense.supplier,
            },
            request=request,
        )
        return expense


def update_expense(*, expense: Expense, actor, data: dict, request=None) -> Expense:
    with transaction.atomic():
        project = locked_project(expense.project_id)
        _require_manage(actor, project)
        expense = locked_expense(expense.pk, project)

        if not expense.is_editable:
            raise KemtaAPIError(
                "expense_locked",
                "Une dépense approuvée, payée ou annulée n'est plus modifiable : "
                "créez une dépense de correction.",
                http_status=409,
                details={"status": expense.status},
            )

        before = {
            "title": expense.title,
            "description": expense.description,
            "amount": int(expense.amount),
            "budget_line": expense.budget_line_id,
            "incurred_on": str(expense.incurred_on or ""),
            "supplier": expense.supplier,
            "invoice_number": expense.invoice_number,
        }
        if "title" in data:
            title = (data.get("title") or "").strip()
            if not title:
                raise KemtaAPIError("title_required", "Le libellé de la dépense est obligatoire.")
            expense.title = title
        if "description" in data:
            expense.description = data["description"] or ""
        if "supplier" in data:
            expense.supplier = (data["supplier"] or "").strip()
        if "incurred_on" in data:
            expense.incurred_on = data["incurred_on"]
        if "budget_line" in data:
            expense.budget_line = _resolve_budget_line(project, data["budget_line"])
        if "invoice_number" in data:
            number = (data["invoice_number"] or "").strip()
            _check_invoice_uniqueness(project, number, exclude=expense)
            expense.invoice_number = number
        if "invoice_date" in data:
            expense.invoice_date = data["invoice_date"] or None
        if "amount" in data:
            expense.amount = _fcfa(data.get("amount"), field_name="amount")

        expense.clean()
        expense.save()

        after = {
            "title": expense.title,
            "description": expense.description,
            "amount": int(expense.amount),
            "budget_line": expense.budget_line_id,
            "incurred_on": str(expense.incurred_on or ""),
            "supplier": expense.supplier,
            "invoice_number": expense.invoice_number,
        }
        changed = {
            key: {"old": before[key], "new": after[key]}
            for key in before
            if before[key] != after[key]
        }
        log_event(
            "EXPENSE_UPDATED",
            actor=actor,
            entity_type="Expense",
            entity_id=expense.pk,
            organization=project.organization,
            project=project,
            metadata={"title": expense.title, "changed": changed},
            request=request,
        )
        return expense


def _resolve_budget_line(project: Project, value) -> BudgetLine | None:
    """Accepte un identifiant (API) ou une instance (appels internes, seed)."""
    if value is None or (not isinstance(value, BudgetLine) and value in ("", "null")):
        return None
    if isinstance(value, BudgetLine):
        line = value
        if line.pk is None:
            raise KemtaAPIError(
                "budget_line_not_found", "Poste budgétaire introuvable.", http_status=404
            )
    else:
        line = _fetch_budget_line(value)
    if line.project_id != project.pk:
        raise KemtaAPIError(
            "budget_line_other_project",
            "Ce poste budgétaire appartient à un autre projet.",
            details={"budget_line": line.pk},
        )
    return line


def _fetch_budget_line(value) -> BudgetLine:
    try:
        return BudgetLine.objects.get(pk=value)
    except (BudgetLine.DoesNotExist, ValueError, TypeError) as exc:
        raise KemtaAPIError(
            "budget_line_not_found", "Poste budgétaire introuvable.", http_status=404
        ) from exc


def _check_invoice_uniqueness(
    project: Project, number: str, *, exclude: Expense | None = None
) -> None:
    if not number:
        return
    queryset = Expense.objects.filter(project=project, invoice_number=number)
    if exclude is not None:
        queryset = queryset.exclude(pk=exclude.pk)
    if queryset.exists():
        raise KemtaAPIError(
            "invoice_already_used",
            "Une dépense de ce projet porte déjà ce numéro de facture.",
            http_status=409,
            details={"invoice_number": number},
        )


def transition_expense(
    *,
    expense: Expense,
    actor,
    action: str,
    comment: str = "",
    override_reason: str = "",
    request=None,
) -> Expense:
    """Soumission, approbation, rejet ou annulation d'une dépense (machine à états fermée)."""
    with transaction.atomic():
        project = locked_project(expense.project_id)
        expense = locked_expense(expense.pk, project)
        comment = (comment or "").strip()

        next_status = EXPENSE_TRANSITIONS.get(expense.status, {}).get(action)
        if next_status is None:
            raise KemtaAPIError(
                "invalid_transition",
                f"Action « {action} » impossible depuis le statut « {expense.get_status_display()} ».",
                http_status=409,
                details={
                    "from_status": expense.status,
                    "action": action,
                    "allowed_actions": sorted(EXPENSE_TRANSITIONS.get(expense.status, {})),
                },
            )

        if action == "SUBMIT":
            _require_manage(actor, project)
        else:
            _require_settle(actor, project)

        # Séparation des tâches : on n'approuve pas sa propre dépense (sauf administration).
        if (
            action == "APPROVE"
            and expense.created_by_id == actor.pk
            and not is_platform_admin(actor)
        ):
            raise KemtaAPIError(
                "cannot_approve_own_expense",
                "Vous ne pouvez pas approuver une dépense que vous avez vous-même créée : "
                "un autre responsable financier doit la valider.",
                http_status=403,
            )
        if action == "REJECT" and not comment:
            raise KemtaAPIError(
                "comment_required",
                "Un motif est obligatoire pour rejeter une dépense.",
                details={"action": action},
            )

        before_committed = ledger_totals(project)["committed"]
        previous_status = expense.status

        if action == "APPROVE":
            _enforce_budget(project, expense, override_reason=override_reason)
            _write_ledger(
                project=project,
                transaction_type=TransactionType.EXPENSE,
                direction=TransactionDirection.DEBIT,
                amount=expense.amount,
                actor=actor,
                effect=LedgerEffect(committed_delta=expense.amount),
                expense=expense,
                budget_line=expense.budget_line,
                note=f"Approbation de « {expense.title} »",
            )
            expense.approved_by = actor
            expense.approved_at = timezone.now()
        elif action == "CANCEL":
            # On ne libère pas un engagement dont une partie est déjà payée : l'argent sorti
            # doit d'abord être tracé comme annulé (contre-écriture dédiée).
            paid = payment_totals(expense)["paid"]
            if paid > ZERO:
                raise KemtaAPIError(
                    "expense_has_payments",
                    "Annulez d'abord les paiements enregistrés sur cette dépense.",
                    http_status=409,
                    details={"paid": int(paid)},
                )
            if previous_status in COMMITTED_STATUSES:
                _write_ledger(
                    project=project,
                    transaction_type=TransactionType.CANCELLATION,
                    direction=TransactionDirection.CREDIT,
                    amount=expense.amount,
                    actor=actor,
                    effect=LedgerEffect(committed_delta=-expense.amount),
                    expense=expense,
                    budget_line=expense.budget_line,
                    note=f"Annulation de « {expense.title} »{f' : {comment}' if comment else ''}",
                )
            expense.cancelled_at = timezone.now()

        expense.status = next_status
        expense.save()

        event = {
            "SUBMIT": "EXPENSE_SUBMITTED",
            "APPROVE": "EXPENSE_APPROVED",
            "REJECT": "EXPENSE_REJECTED",
            "CANCEL": "EXPENSE_CANCELLED",
        }[action]
        metadata = {
            "title": expense.title,
            "amount": int(expense.amount),
            "from_status": previous_status,
            "to_status": next_status,
            "comment": comment[:280],
        }
        if action == "APPROVE" and override_reason:
            # Dépassement assumé : le motif est journalisé, l'alerte reste visible dans la synthèse.
            metadata["over_budget_override"] = override_reason[:280]
        log_event(
            event,
            actor=actor,
            entity_type="Expense",
            entity_id=expense.pk,
            organization=project.organization,
            project=project,
            metadata=metadata,
            request=request,
        )
        if action == "APPROVE":
            _log_threshold_crossing(
                project=project,
                before_committed=before_committed,
                after_committed=before_committed + expense.amount,
                actor=actor,
                request=request,
            )
        return expense


def _enforce_budget(project: Project, expense: Expense, *, override_reason: str) -> None:
    """Refuse un engagement qui dépasse le budget — sauf dépassement motivé (tracé)."""
    totals = ledger_totals(project)
    overruns: list[dict] = []
    committed_after = totals["committed"] + expense.amount
    if committed_after > project.budget_total:
        overruns.append(
            {
                "scope": "project",
                "planned": int(project.budget_total),
                "committed_after": int(committed_after),
                "over": int(committed_after - project.budget_total),
            }
        )
    if expense.budget_line is not None:
        line_after = line_committed(project, expense.budget_line) + expense.amount
        if line_after > expense.budget_line.planned_amount:
            overruns.append(
                {
                    "scope": "budget_line",
                    "budget_line": expense.budget_line_id,
                    "label": expense.budget_line.label,
                    "planned": int(expense.budget_line.planned_amount),
                    "committed_after": int(line_after),
                    "over": int(line_after - expense.budget_line.planned_amount),
                }
            )
    if not overruns:
        return
    if len(override_reason.strip()) < MIN_OVERRIDE_REASON_LENGTH:
        raise KemtaAPIError(
            "budget_exceeded",
            "Cette dépense dépasse le budget disponible. Pour l'engager malgré tout, "
            f"indiquez un motif de dépassement (au moins {MIN_OVERRIDE_REASON_LENGTH} caractères).",
            http_status=422,
            details={
                "overruns": overruns,
                "min_override_reason_length": MIN_OVERRIDE_REASON_LENGTH,
            },
        )


# --------------------------------------------------------------------------- paiements
def payment_totals(expense: Expense) -> dict[str, Decimal]:
    payments = Payment.objects.filter(expense=expense, cancelled_at__isnull=True)
    paid = _sum(payments.only("id", "amount"))
    return {"paid": paid, "outstanding": expense.amount - paid}


def register_payment(*, expense: Expense, actor, data: dict, request=None) -> Payment:
    with transaction.atomic():
        project = locked_project(expense.project_id)
        _require_settle(actor, project)
        expense = locked_expense(expense.pk, project)

        if expense.status not in (ExpenseStatus.APPROVED, ExpenseStatus.PAID):
            raise KemtaAPIError(
                "expense_not_approved",
                "Seule une dépense approuvée peut être payée.",
                http_status=409,
                details={"status": expense.status},
            )

        amount = _fcfa(data.get("amount"), field_name="amount")
        paid_on = data.get("paid_on") or timezone.localdate()
        if paid_on > timezone.localdate():
            raise KemtaAPIError(
                "payment_date_in_future",
                "La date de paiement ne peut pas être dans le futur.",
                details={"server_date": str(timezone.localdate())},
            )

        outstanding = payment_totals(expense)["outstanding"]
        if amount > outstanding:
            raise KemtaAPIError(
                "payment_exceeds_outstanding",
                "Le paiement dépasse le montant restant dû sur cette dépense.",
                http_status=422,
                details={"outstanding": int(outstanding), "amount": int(amount)},
            )

        method = data.get("method") or "CASH"
        payment = Payment.objects.create(
            expense=expense,
            amount=amount,
            paid_on=paid_on,
            method=method,
            reference=(data.get("reference") or "").strip(),
            note=(data.get("note") or "").strip(),
            created_by=actor,
        )
        # Défense en profondeur : si une écriture concurrente s'était glissée malgré le verrou,
        # le total payé ne doit jamais dépasser le montant de la dépense (tout est annulé).
        checked = payment_totals(expense)
        if checked["paid"] > expense.amount:
            raise KemtaAPIError(
                "payment_exceeds_outstanding",
                "Le paiement dépasse le montant restant dû sur cette dépense.",
                http_status=422,
                details={
                    "outstanding": int(payment_totals(expense)["outstanding"]),
                    "amount": int(amount),
                },
            )

        _write_ledger(
            project=project,
            transaction_type=TransactionType.PAYMENT,
            direction=TransactionDirection.DEBIT,
            amount=amount,
            actor=actor,
            effect=LedgerEffect(paid_delta=amount),
            expense=expense,
            payment=payment,
            budget_line=expense.budget_line,
            note=f"Paiement de « {expense.title} » ({payment.get_method_display()})",
        )

        # Une dépense intégralement payée change de statut (le reste dû est recalculé, jamais déduit).
        totals = payment_totals(expense)
        new_status = ExpenseStatus.PAID if totals["outstanding"] <= ZERO else ExpenseStatus.APPROVED
        if new_status != expense.status:
            expense.status = new_status
            expense.save(update_fields=["status", "updated_at"])

        log_event(
            "PAYMENT_RECORDED",
            actor=actor,
            entity_type="Payment",
            entity_id=payment.pk,
            organization=project.organization,
            project=project,
            metadata={
                "expense": expense.pk,
                "title": expense.title,
                "amount": int(amount),
                "method": method,
                "paid_on": str(paid_on),
                "expense_status": expense.status,
                "outstanding": int(totals["outstanding"]),
            },
            request=request,
        )
        return payment


def cancel_payment(*, payment: Payment, actor, reason: str = "", request=None) -> Payment:
    with transaction.atomic():
        project = locked_project(payment.expense.project_id)
        _require_settle(actor, project)
        payment = Payment.objects.select_for_update().select_related("expense").get(pk=payment.pk)
        if payment.cancelled_at is not None:
            raise KemtaAPIError(
                "payment_already_cancelled", "Ce paiement est déjà annulé.", http_status=409
            )

        expense = locked_expense(payment.expense_id, project)
        _write_ledger(
            project=project,
            transaction_type=TransactionType.CANCELLATION,
            direction=TransactionDirection.CREDIT,
            amount=payment.amount,
            actor=actor,
            effect=LedgerEffect(paid_delta=-payment.amount),
            expense=expense,
            payment=payment,
            budget_line=expense.budget_line,
            note=f"Annulation du paiement {payment.pk}{f' : {reason.strip()}' if reason.strip() else ''}",
        )
        payment.cancelled_at = timezone.now()
        payment.cancelled_by = actor
        payment.save(update_fields=["cancelled_at", "cancelled_by", "updated_at"])

        # Le montant redevient dû : la dépense sort de l'état « payée ».
        totals = payment_totals(expense)
        new_status = ExpenseStatus.PAID if totals["outstanding"] <= ZERO else ExpenseStatus.APPROVED
        if new_status != expense.status:
            expense.status = new_status
            expense.save(update_fields=["status", "updated_at"])

        log_event(
            "PAYMENT_CANCELLED",
            actor=actor,
            entity_type="Payment",
            entity_id=payment.pk,
            organization=project.organization,
            project=project,
            metadata={
                "expense": expense.pk,
                "amount": int(payment.amount),
                "reason": reason[:280],
                "expense_status": expense.status,
            },
            request=request,
        )
        return payment


# --------------------------------------------------------------------------- ajustements
def record_adjustment(*, project: Project, actor, data: dict, request=None) -> FinancialTransaction:
    """Correction manuelle du budget engagé — **toujours motivée** et tracée."""
    with transaction.atomic():
        project = locked_project(project.pk)
        _require_settle(actor, project)

        amount = _fcfa(data.get("amount"), field_name="amount")
        direction = data.get("direction") or TransactionDirection.DEBIT
        reason = (data.get("reason") or "").strip()
        if len(reason) < 5:
            raise KemtaAPIError(
                "reason_required",
                "Un motif est obligatoire pour enregistrer un ajustement financier.",
                details={"min_length": 5},
            )

        delta = amount if direction == TransactionDirection.DEBIT else -amount
        before_committed = ledger_totals(project)["committed"]
        entry = _write_ledger(
            project=project,
            transaction_type=TransactionType.ADJUSTMENT,
            direction=direction,
            amount=amount,
            actor=actor,
            effect=LedgerEffect(committed_delta=delta),
            note=reason,
        )
        log_event(
            "ADJUSTMENT_RECORDED",
            actor=actor,
            entity_type="FinancialTransaction",
            entity_id=entry.pk,
            organization=project.organization,
            project=project,
            metadata={
                "amount": int(amount),
                "direction": direction,
                "reason": reason[:280],
                "balance_after": int(entry.balance_after),
            },
            request=request,
        )
        _log_threshold_crossing(
            project=project,
            before_committed=before_committed,
            after_committed=before_committed + delta,
            actor=actor,
            request=request,
        )
        return entry


# --------------------------------------------------------------------------- justificatifs
def attach_receipt(*, expense: Expense, actor, upload, request=None) -> Expense:
    """Attache (ou remplace) la facture justificative ; le contenu est vérifié côté serveur."""
    from django.core.files.base import ContentFile

    from apps.finance.storage import (
        EXTENSION_BY_CONTENT_TYPE,
        read_and_validate_receipt,
        sha256_of,
    )

    with transaction.atomic():
        project = locked_project(expense.project_id)
        _require_manage(actor, project)
        expense = locked_expense(expense.pk, project)

        payload, content_type = read_and_validate_receipt(upload)
        extension = EXTENSION_BY_CONTENT_TYPE[content_type]
        filename = f"facture-{expense.pk}-{timezone.now():%Y%m%d%H%M%S}.{extension}"
        expense.receipt.save(filename, ContentFile(payload), save=False)
        expense.receipt_hash = sha256_of(payload)
        expense.save(update_fields=["receipt", "receipt_hash", "updated_at"])

        log_event(
            "EXPENSE_RECEIPT_ATTACHED",
            actor=actor,
            entity_type="Expense",
            entity_id=expense.pk,
            organization=project.organization,
            project=project,
            metadata={
                "title": expense.title,
                "content_type": content_type,
                "size_bytes": len(payload),
                "sha256": expense.receipt_hash,
            },
            request=request,
        )
        return expense
