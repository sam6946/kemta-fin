"""Sérialiseurs financiers (MVP-010).

Règle constante : **aucun total n'est accepté en entrée**. Les montants calculés (consommé,
payé, solde, taux) sont en lecture seule et proviennent du grand livre.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.core.serializers import FcfaField
from apps.finance.access import (
    finance_permissions_map,
)
from apps.finance.models import (
    BudgetCategory,
    BudgetLine,
    Expense,
    FinancialTransaction,
    Payment,
    PaymentMethod,
    TransactionDirection,
)
from apps.finance.services import payment_totals
from apps.users.serializers import UserSerializer


def _permissions_for(context: dict, user, project) -> dict:
    """Permissions du projet, pré-calculées par la vue quand la liste en contient plusieurs."""
    cached = context.get("finance_permissions_by_project")
    if cached is not None and project.pk in cached:
        return cached[project.pk]
    return finance_permissions_map(user, [project]).get(project.pk, {})


class BudgetLineSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    planned_amount = FcfaField()
    committed_amount = serializers.SerializerMethodField()
    expense_count = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = BudgetLine
        fields = [
            "id",
            "project",
            "label",
            "category",
            "category_label",
            "planned_amount",
            "committed_amount",
            "expense_count",
            "order",
            "notes",
            "permissions",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "project", "committed_amount", "expense_count", "permissions"]

    def get_committed_amount(self, obj: BudgetLine) -> int:
        """Consommé du poste, calculé en SQL depuis le grand livre (jamais reçu du client)."""
        committed = self.context.get("committed_by_line", {}).get(obj.pk)
        if committed is None:
            from apps.finance.services import line_committed

            committed = line_committed(obj.project, obj)
        return int(committed)

    def get_expense_count(self, obj: BudgetLine) -> int:
        counts = self.context.get("expense_count_by_line")
        if counts is not None:
            return counts.get(obj.pk, 0)
        return obj.expenses.count()

    def get_permissions(self, obj: BudgetLine) -> dict:
        user = self.context.get("user")
        if user is None or not getattr(user, "is_authenticated", False):
            return {}
        granted = _permissions_for(self.context, user, obj.project)
        settle = bool(granted.get("settle_finance"))
        return {
            # Modifier un poste (créer, réviser, supprimer) exige le droit d'**engagement**,
            # comme la vue : `manage_finance` ne suffit pas à redistribuer un budget.
            "manage_finance": settle,
            "settle_finance": settle,
            "create_expense": bool(granted.get("manage_finance")),
        }


class PaymentSerializer(serializers.ModelSerializer):
    method_label = serializers.CharField(source="get_method_display", read_only=True)
    amount = FcfaField(read_only=True)
    created_by = UserSerializer(read_only=True)
    cancelled_by = UserSerializer(read_only=True)
    is_cancelled = serializers.BooleanField(read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "expense",
            "amount",
            "paid_on",
            "method",
            "method_label",
            "reference",
            "note",
            "created_by",
            "cancelled_at",
            "cancelled_by",
            "is_cancelled",
            "created_at",
        ]
        read_only_fields = fields


class ExpenseSerializer(serializers.ModelSerializer):
    amount = FcfaField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    budget_line_label = serializers.CharField(
        source="budget_line.label", read_only=True, default=None
    )
    created_by = UserSerializer(read_only=True)
    approved_by = UserSerializer(read_only=True)
    # Champs calculés, en lecture seule : le reste dû et le payé viennent du grand livre.
    paid_amount = serializers.SerializerMethodField()
    outstanding_amount = serializers.SerializerMethodField()
    payments = PaymentSerializer(many=True, read_only=True)
    receipt_url = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()
    is_editable = serializers.BooleanField(read_only=True)

    class Meta:
        model = Expense
        fields = [
            "id",
            "project",
            "budget_line",
            "budget_line_label",
            "title",
            "description",
            "amount",
            "currency",
            "incurred_on",
            "status",
            "status_label",
            "supplier",
            "invoice_number",
            "invoice_date",
            "receipt_url",
            "receipt_hash",
            "paid_amount",
            "outstanding_amount",
            "payments",
            "is_editable",
            "created_by",
            "approved_by",
            "approved_at",
            "cancelled_at",
            "permissions",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "project",
            "currency",
            "status",
            "receipt_hash",
            "paid_amount",
            "outstanding_amount",
            "payments",
            "is_editable",
            "created_by",
            "approved_by",
            "approved_at",
            "cancelled_at",
            "permissions",
        ]

    def get_paid_amount(self, obj: Expense) -> int:
        # La vue de liste fournit la carte complète (une seule requête) : une entrée absente
        # signifie « aucun paiement », pas « valeur inconnue ». Le test porte donc sur la
        # présence de la carte (et non de la clé), sinon une dépense sans paiement relancerait
        # une agrégation par ligne.
        cached = self.context.get("paid_by_expense")
        if cached is not None:
            return int(cached.get(obj.pk, 0))
        return int(payment_totals(obj)["paid"])

    def get_outstanding_amount(self, obj: Expense) -> int:
        return int(obj.amount) - self.get_paid_amount(obj)

    def get_receipt_url(self, obj: Expense) -> str | None:
        """Chemin **relatif** : il reste valable derrière un proxy (comme les preuves)."""
        return f"/api/expenses/{obj.pk}/receipt/" if obj.receipt else None

    def get_permissions(self, obj: Expense) -> dict:
        user = self.context.get("user")
        if user is None or not getattr(user, "is_authenticated", False):
            return {}
        granted = _permissions_for(self.context, user, obj.project)
        settle = bool(granted.get("settle_finance"))
        from apps.projects.access import is_platform_admin

        return {
            "view_finance": bool(granted.get("view_finance")),
            "manage_finance": bool(granted.get("manage_finance")),
            "settle_finance": settle,
            # Séparation des tâches : l'auteur ne peut pas approuver sa propre dépense.
            "can_approve": bool(
                settle and (obj.created_by_id != user.pk or is_platform_admin(user))
            ),
            "can_pay": bool(settle and obj.status in {"APPROVED", "PAID"}),
            "can_edit": bool(granted.get("manage_finance") and obj.is_editable),
            "can_cancel": bool(settle and obj.status not in {"CANCELLED", "PAID"}),
        }


class ExpenseCreateSerializer(serializers.Serializer):
    """Entrée de création : aucun champ calculé n'est accepté."""

    budget_line = serializers.IntegerField(required=False, allow_null=True)
    title = serializers.CharField(max_length=180)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    amount = FcfaField(min_value=1)
    incurred_on = serializers.DateField()
    supplier = serializers.CharField(required=False, allow_blank=True, default="", max_length=180)
    invoice_number = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=80
    )
    invoice_date = serializers.DateField(required=False, allow_null=True, default=None)


class ExpenseTransitionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["SUBMIT", "APPROVE", "REJECT", "CANCEL"])
    comment = serializers.CharField(required=False, allow_blank=True, default="")
    # Motif exigé uniquement pour engager une dépense au-delà du budget disponible.
    override_reason = serializers.CharField(required=False, allow_blank=True, default="")


class PaymentCreateSerializer(serializers.Serializer):
    amount = FcfaField(min_value=1)
    paid_on = serializers.DateField(required=False, allow_null=True, default=None)
    method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = serializers.CharField(required=False, allow_blank=True, default="", max_length=120)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class AdjustmentSerializer(serializers.Serializer):
    amount = FcfaField(min_value=1)
    direction = serializers.ChoiceField(choices=TransactionDirection.choices)
    reason = serializers.CharField(min_length=5)


class TransactionSerializer(serializers.ModelSerializer):
    type_label = serializers.CharField(source="get_type_display", read_only=True)
    direction_label = serializers.CharField(source="get_direction_display", read_only=True)
    amount = FcfaField(read_only=True)
    balance_after = FcfaField(read_only=True)
    created_by = UserSerializer(read_only=True)
    expense_title = serializers.CharField(source="expense.title", read_only=True, default=None)
    budget_line_label = serializers.CharField(
        source="budget_line.label", read_only=True, default=None
    )

    class Meta:
        model = FinancialTransaction
        fields = [
            "id",
            "project",
            "type",
            "type_label",
            "direction",
            "direction_label",
            "amount",
            "balance_after",
            "expense",
            "expense_title",
            "payment",
            "budget_line",
            "budget_line_label",
            "note",
            "created_by",
            "created_at",
        ]
        read_only_fields = fields


def category_choices() -> list[dict]:
    return [{"value": value, "label": label} for value, label in BudgetCategory.choices]
