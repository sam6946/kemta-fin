"""Administration : lecture seule sur le grand livre, consultation des autres objets."""

from django.contrib import admin

from apps.finance.models import BudgetLine, Expense, FinancialTransaction, Payment


@admin.register(BudgetLine)
class BudgetLineAdmin(admin.ModelAdmin):
    list_display = ("project", "label", "category", "planned_amount", "order")
    list_filter = ("category",)
    search_fields = ("label", "project__code", "project__name")


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("project", "title", "amount", "status", "incurred_on", "supplier")
    list_filter = ("status", "project")
    search_fields = ("title", "supplier", "invoice_number", "project__code")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("expense", "amount", "paid_on", "method", "cancelled_at")
    list_filter = ("method",)
    search_fields = ("reference", "expense__title")


@admin.register(FinancialTransaction)
class FinancialTransactionAdmin(admin.ModelAdmin):
    """Le grand livre n'est ni modifiable ni supprimable, même depuis l'administration."""

    list_display = (
        "created_at",
        "project",
        "type",
        "direction",
        "amount",
        "balance_after",
        "created_by",
    )
    list_filter = ("type", "direction", "project")
    readonly_fields = [field.name for field in FinancialTransaction._meta.fields]

    def has_add_permission(self, request):  # pragma: no cover - dépendance admin
        return False

    def has_change_permission(self, request, obj=None):  # pragma: no cover
        return False

    def has_delete_permission(self, request, obj=None):  # pragma: no cover
        return False
