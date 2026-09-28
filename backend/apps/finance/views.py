"""Endpoints financiers (MVP-010) — contrat `docs/api-contract.md` §7.

Principes appliqués :

* lecture soumise à `VIEW_FINANCE`, écriture à `MANAGE_FINANCE` (et `can_settle_finance` pour
  approuver, payer ou modifier le budget) : les règles vivent dans `apps/finance/access.py` ;
* un projet hors périmètre renvoie **404** (jamais 403, pour ne pas révéler son existence) ;
* toute écriture passe par un service transactionnel : la vue ne calcule aucun montant ;
* les listes sont paginées et vectorisées (pas de N+1 : agrégats chargés en une requête).
"""

from __future__ import annotations

from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import KemtaAPIError
from apps.core.pagination import DefaultPagination
from apps.finance.access import can_view_finance, finance_permissions_map
from apps.finance.models import (
    BudgetCategory,
    BudgetLine,
    Expense,
    ExpenseStatus,
    FinancialTransaction,
    Payment,
    PaymentMethod,
    TransactionType,
)
from apps.finance.serializers import (
    AdjustmentSerializer,
    BudgetLineSerializer,
    ExpenseCreateSerializer,
    ExpenseSerializer,
    ExpenseTransitionSerializer,
    PaymentCreateSerializer,
    PaymentSerializer,
    TransactionSerializer,
)
from apps.finance.services import (
    attach_receipt,
    budget_summary,
    cancel_payment,
    create_budget_line,
    create_expense,
    delete_budget_line,
    ledger_totals,
    line_committed_map,
    payment_totals,
    record_adjustment,
    register_payment,
    transition_expense,
    update_budget_line,
    update_expense,
)
from apps.projects.access import accessible_projects


def accessible_expenses(user):
    return Expense.objects.filter(project__in=accessible_projects(user))


def _require_view(user, project) -> None:
    if not can_view_finance(user, project):
        raise KemtaAPIError(
            "permission_denied",
            "Vous n'avez pas accès aux informations financières de ce projet.",
            http_status=403,
        )


def _accessible_project(user, pk):
    return get_object_or_404(accessible_projects(user), pk=pk)


def _expense_with_context(user, pk) -> Expense:
    return get_object_or_404(
        accessible_expenses(user).select_related("project", "budget_line", "created_by"), pk=pk
    )


def serialize_expense(user, expense: Expense) -> dict:
    return ExpenseSerializer(expense, context={"user": user}).data


class BudgetLineListCreateView(APIView):
    """`GET` / `POST /api/projects/{id}/budget-lines/` — postes budgétaires du projet."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        project = _accessible_project(request.user, pk)
        _require_view(request.user, project)
        # `select_related("project")` : les permissions du sérialiseur ne doivent pas relire le projet.
        lines = list(BudgetLine.objects.filter(project=project).select_related("project"))
        # Nombre constant de requêtes, quelle que soit la taille du budget : jamais de N+1.
        counts = dict(
            Expense.objects.filter(project=project)
            .values_list("budget_line_id")
            .annotate(total=Count("id"))
        )
        committed_by_line = line_committed_map(project, lines)
        serializer = BudgetLineSerializer(
            lines,
            many=True,
            context={
                "user": request.user,
                "committed_by_line": committed_by_line,
                "expense_count_by_line": counts,
                "finance_permissions_by_project": finance_permissions_map(request.user, [project]),
            },
        )
        return Response(
            {
                "count": len(lines),
                "results": serializer.data,
                "summary": budget_summary(project),
                "categories": [
                    {"value": value, "label": label} for value, label in BudgetCategory.choices
                ],
            }
        )

    def post(self, request, pk):
        project = _accessible_project(request.user, pk)
        line = create_budget_line(
            project=project, actor=request.user, data=request.data, request=request
        )
        return Response(
            BudgetLineSerializer(line, context={"user": request.user}).data,
            status=status.HTTP_201_CREATED,
        )


class BudgetLineDetailView(APIView):
    """`GET` / `PATCH` / `DELETE /api/budget-lines/{id}/`."""

    permission_classes = [IsAuthenticated]

    def get_line(self, request, pk) -> BudgetLine:
        line = get_object_or_404(
            BudgetLine.objects.filter(project__in=accessible_projects(request.user)).select_related(
                "project"
            ),
            pk=pk,
        )
        _require_view(request.user, line.project)
        return line

    def get(self, request, pk):
        line = self.get_line(request, pk)
        return Response(BudgetLineSerializer(line, context={"user": request.user}).data)

    def patch(self, request, pk):
        line = self.get_line(request, pk)
        line = update_budget_line(line=line, actor=request.user, data=request.data, request=request)
        return Response(BudgetLineSerializer(line, context={"user": request.user}).data)

    def delete(self, request, pk):
        line = self.get_line(request, pk)
        delete_budget_line(line=line, actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ExpenseListCreateView(APIView):
    """`GET` / `POST /api/projects/{id}/expenses/`."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        project = _accessible_project(request.user, pk)
        _require_view(request.user, project)

        queryset = (
            Expense.objects.filter(project=project)
            # `project` inclus : les permissions de chaque ligne ne doivent pas relire le projet.
            .select_related("project", "budget_line", "created_by", "approved_by")
            .prefetch_related("payments__created_by", "payments__cancelled_by")
        )
        status_filter = request.query_params.get("status")
        if status_filter:
            values = [value.upper() for value in status_filter.split(",") if value]
            unknown = [value for value in values if value not in ExpenseStatus.values]
            if unknown:
                raise KemtaAPIError(
                    "invalid_status", "Statut inconnu.", details={"unknown": unknown}
                )
            queryset = queryset.filter(status__in=values)
        if request.query_params.get("budget_line"):
            queryset = queryset.filter(budget_line_id=request.query_params["budget_line"])
        if request.query_params.get("unpaid") in {"1", "true", "True"}:
            queryset = queryset.filter(status=ExpenseStatus.APPROVED)
        ordering = request.query_params.get("ordering", "-incurred_on")
        if ordering not in {
            "-incurred_on",
            "incurred_on",
            "-amount",
            "amount",
            "-created_at",
            "created_at",
        }:
            raise KemtaAPIError("invalid_ordering", "Tri non supporté.")
        queryset = queryset.order_by(ordering, "-id")

        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = ExpenseSerializer(
            page,
            many=True,
            context={
                "user": request.user,
                "paid_by_expense": _paid_map(page),
                "finance_permissions_by_project": finance_permissions_map(request.user, [project]),
            },
        )
        payload = paginator.get_paginated_response(serializer.data).data
        payload["summary"] = budget_summary(project)
        payload["counts"] = {
            "draft": Expense.objects.filter(project=project, status=ExpenseStatus.DRAFT).count(),
            "submitted": Expense.objects.filter(
                project=project, status=ExpenseStatus.SUBMITTED
            ).count(),
            "approved": Expense.objects.filter(
                project=project, status=ExpenseStatus.APPROVED
            ).count(),
            "paid": Expense.objects.filter(project=project, status=ExpenseStatus.PAID).count(),
            "rejected": Expense.objects.filter(
                project=project, status=ExpenseStatus.REJECTED
            ).count(),
            "cancelled": Expense.objects.filter(
                project=project, status=ExpenseStatus.CANCELLED
            ).count(),
        }
        return Response(payload)

    def post(self, request, pk):
        project = _accessible_project(request.user, pk)
        serializer = ExpenseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        expense = create_expense(
            project=project, actor=request.user, data=serializer.validated_data, request=request
        )
        return Response(serialize_expense(request.user, expense), status=status.HTTP_201_CREATED)


def _paid_map(expenses) -> dict[int, int]:
    """Payé par dépense en **une** requête (les listes ne doivent pas générer de N+1)."""
    expenses = list(expenses)
    if not expenses:
        return {}
    rows = (
        Payment.objects.filter(expense__in=expenses, cancelled_at__isnull=True)
        .values_list("expense_id")
        .annotate(total=Sum("amount"))
    )
    return dict(rows)


class ExpenseDetailView(APIView):
    """`GET` / `PATCH /api/expenses/{id}/` — la modification s'arrête à l'approbation."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        expense = _expense_with_context(request.user, pk)
        _require_view(request.user, expense.project)
        return Response(serialize_expense(request.user, expense))

    def patch(self, request, pk):
        expense = _expense_with_context(request.user, pk)
        expense = update_expense(
            expense=expense, actor=request.user, data=request.data, request=request
        )
        return Response(serialize_expense(request.user, expense))


class ExpenseTransitionView(APIView):
    """`POST /api/expenses/{id}/transition/` — soumettre, approuver, rejeter, annuler."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        expense = _expense_with_context(request.user, pk)
        serializer = ExpenseTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        expense = transition_expense(
            expense=expense,
            actor=request.user,
            action=serializer.validated_data["action"],
            comment=serializer.validated_data.get("comment") or "",
            override_reason=serializer.validated_data.get("override_reason") or "",
            request=request,
        )
        return Response(serialize_expense(request.user, expense))


class ExpensePaymentListCreateView(APIView):
    """`GET` / `POST /api/expenses/{id}/payments/` — paiements d'une dépense."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        expense = _expense_with_context(request.user, pk)
        _require_view(request.user, expense.project)
        payments = expense.payments.select_related("created_by", "cancelled_by")
        totals = payment_totals(expense)
        return Response(
            {
                "count": payments.count(),
                "results": PaymentSerializer(payments, many=True).data,
                "totals": {
                    "amount": int(expense.amount),
                    "paid": int(totals["paid"]),
                    "outstanding": int(totals["outstanding"]),
                },
                "methods": [
                    {"value": value, "label": label} for value, label in PaymentMethod.choices
                ],
            }
        )

    def post(self, request, pk):
        expense = _expense_with_context(request.user, pk)
        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = register_payment(
            expense=expense, actor=request.user, data=serializer.validated_data, request=request
        )
        expense.refresh_from_db()
        return Response(
            {
                "payment": PaymentSerializer(payment).data,
                "expense": serialize_expense(request.user, expense),
            },
            status=status.HTTP_201_CREATED,
        )


class PaymentCancelView(APIView):
    """`POST /api/payments/{id}/cancel/` — contre-écriture (rien n'est supprimé)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        payment = get_object_or_404(
            Payment.objects.filter(
                expense__project__in=accessible_projects(request.user)
            ).select_related("expense__project"),
            pk=pk,
        )
        payment = cancel_payment(
            payment=payment,
            actor=request.user,
            reason=request.data.get("reason") or "",
            request=request,
        )
        return Response(PaymentSerializer(payment).data)


class ExpenseReceiptView(APIView):
    """`GET` / `POST /api/expenses/{id}/receipt/` — consultation et dépôt du justificatif."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from django.http import FileResponse

        expense = _expense_with_context(request.user, pk)
        _require_view(request.user, expense.project)
        if not expense.receipt:
            raise KemtaAPIError(
                "receipt_not_available", "Aucun justificatif pour cette dépense.", http_status=404
            )
        response = FileResponse(expense.receipt.open("rb"))
        response["Content-Disposition"] = (
            f'inline; filename="{expense.receipt.name.rsplit("/", 1)[-1]}"'
        )
        # Un justificatif financier est privé : jamais de cache partagé.
        response["Cache-Control"] = "private, max-age=60"
        return response

    def post(self, request, pk):
        expense = _expense_with_context(request.user, pk)
        upload = request.FILES.get("file")
        if upload is None:
            raise KemtaAPIError("file_required", "Aucun fichier reçu.")
        expense = attach_receipt(
            expense=expense, actor=request.user, upload=upload, request=request
        )
        return Response(serialize_expense(request.user, expense), status=status.HTTP_201_CREATED)


class ProjectTransactionsView(APIView):
    """`GET /api/projects/{id}/transactions/` — grand livre paginé (source de vérité)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        project = _accessible_project(request.user, pk)
        _require_view(request.user, project)
        queryset = (
            FinancialTransaction.objects.filter(project=project)
            .select_related("created_by", "expense", "budget_line")
            .order_by("-created_at", "-id")
        )
        type_filter = request.query_params.get("type")
        if type_filter:
            values = [value.upper() for value in type_filter.split(",") if value]
            unknown = [value for value in values if value not in TransactionType.values]
            if unknown:
                raise KemtaAPIError("invalid_type", "Type inconnu.", details={"unknown": unknown})
            queryset = queryset.filter(type__in=values)

        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request)
        payload = paginator.get_paginated_response(TransactionSerializer(page, many=True).data).data
        totals = ledger_totals(project)
        payload["totals"] = {
            "committed": int(totals["committed"]),
            "paid": int(totals["paid"]),
            "adjustments": int(totals["adjustments"]),
            "balance": int(project.budget_total - totals["committed"]),
        }
        return Response(payload)


class ProjectFinanceSummaryView(APIView):
    """`GET /api/projects/{id}/finance/` — synthèse calculée côté serveur.

    Sert directement l'écran financier (et, en phase 8, le dashboard agrégé) : le client ne
    recalcule ni le consommé, ni le solde, ni le taux.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        project = _accessible_project(request.user, pk)
        _require_view(request.user, project)
        summary = budget_summary(project)
        return Response(
            {
                "project": {
                    "id": project.pk,
                    "code": project.code,
                    "name": project.name,
                    "currency": project.currency,
                },
                "budget": {
                    key: summary[key]
                    for key in (
                        "planned",
                        "allocated",
                        "unallocated",
                        "committed",
                        "paid",
                        "outstanding",
                        "balance",
                        "consumption_rate",
                        "threshold",
                        "currency",
                    )
                },
                "lines": summary["lines"],
                "alerts": summary["alerts"],
                "permissions": {
                    **finance_permissions_map(request.user, [project])[project.pk],
                },
                "generated_at": timezone.now(),
            }
        )


class ProjectAdjustmentView(APIView):
    """`POST /api/projects/{id}/adjustments/` — correction motivée du budget engagé."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        project = _accessible_project(request.user, pk)
        serializer = AdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = record_adjustment(
            project=project, actor=request.user, data=serializer.validated_data, request=request
        )
        return Response(TransactionSerializer(entry).data, status=status.HTTP_201_CREATED)
