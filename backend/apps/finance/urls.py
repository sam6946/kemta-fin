"""Routes financières (MVP-010) — contrat `docs/api-contract.md` §7."""

from django.urls import path

from apps.finance.views import (
    BudgetLineDetailView,
    BudgetLineListCreateView,
    ExpenseDetailView,
    ExpenseListCreateView,
    ExpensePaymentListCreateView,
    ExpenseReceiptView,
    ExpenseTransitionView,
    PaymentCancelView,
    ProjectAdjustmentView,
    ProjectFinanceSummaryView,
    ProjectTransactionsView,
)

urlpatterns = [
    path(
        "projects/<int:pk>/budget-lines/",
        BudgetLineListCreateView.as_view(),
        name="project-budget-lines",
    ),
    path("budget-lines/<int:pk>/", BudgetLineDetailView.as_view(), name="budget-line-detail"),
    path("projects/<int:pk>/expenses/", ExpenseListCreateView.as_view(), name="project-expenses"),
    path("expenses/<int:pk>/", ExpenseDetailView.as_view(), name="expense-detail"),
    path(
        "expenses/<int:pk>/transition/",
        ExpenseTransitionView.as_view(),
        name="expense-transition",
    ),
    path(
        "expenses/<int:pk>/payments/",
        ExpensePaymentListCreateView.as_view(),
        name="expense-payments",
    ),
    path("expenses/<int:pk>/receipt/", ExpenseReceiptView.as_view(), name="expense-receipt"),
    path("payments/<int:pk>/cancel/", PaymentCancelView.as_view(), name="payment-cancel"),
    path(
        "projects/<int:pk>/transactions/",
        ProjectTransactionsView.as_view(),
        name="project-transactions",
    ),
    path(
        "projects/<int:pk>/finance/",
        ProjectFinanceSummaryView.as_view(),
        name="project-finance",
    ),
    path(
        "projects/<int:pk>/adjustments/",
        ProjectAdjustmentView.as_view(),
        name="project-adjustments",
    ),
]
