"""Suivi financier des chantiers : budget, dépenses, paiements, grand livre (MVP-010).

Règles structurantes (voir `docs/data-model.md` §5) :

* **montants en FCFA entiers** — jamais de centimes : on refuse plutôt que d'arrondir en silence
  (`apps.core.money.validate_fcfa_amount`) ;
* **tout effet financier écrit une ligne de grand livre** (`FinancialTransaction`), append-only :
  le consommé et le solde sont des **agrégats SQL** du grand livre, jamais des valeurs reçues du
  client ni des compteurs incrémentés à la main ;
* **une écriture financière = une transaction atomique** avec verrou (`select_for_update`) sur le
  projet et sur la dépense : deux paiements concurrents ne peuvent pas dépasser le montant dû ;
* **annulation = contre-écriture** : rien n'est supprimé, tout reste auditable ;
* les journaux financiers bruts ne sont **jamais** modifiables (ni par l'administration).
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models

from apps.core.models import SoftDeleteModel, TimeStampedModel


class BudgetCategory(models.TextChoices):
    """Postes budgétaires d'un chantier camerounais (liste fermée, comme les statuts)."""

    MATERIALS = "MATERIALS", "Matériaux"
    LABOUR = "LABOUR", "Main-d'œuvre"
    EQUIPMENT = "EQUIPMENT", "Matériel et engins"
    SUBCONTRACT = "SUBCONTRACT", "Sous-traitance"
    TRANSPORT = "TRANSPORT", "Transport"
    ADMIN = "ADMIN", "Frais administratifs"
    OTHER = "OTHER", "Autres"


class ExpenseStatus(models.TextChoices):
    DRAFT = "DRAFT", "Brouillon"
    SUBMITTED = "SUBMITTED", "Soumise à validation"
    APPROVED = "APPROVED", "Approuvée"
    REJECTED = "REJECTED", "Rejetée"
    PAID = "PAID", "Payée"
    CANCELLED = "CANCELLED", "Annulée"


class ExpenseAction(models.TextChoices):
    """Actions d'approbation : elles ne sont pas des statuts."""

    SUBMIT = "SUBMIT", "Soumettre"
    APPROVE = "APPROVE", "Approuver"
    REJECT = "REJECT", "Rejeter"
    CANCEL = "CANCEL", "Annuler"


# Transitions autorisées de la dépense (machine à états explicite et testée).
EXPENSE_TRANSITIONS: dict[str, dict[str, str]] = {
    ExpenseStatus.DRAFT: {
        ExpenseAction.SUBMIT: ExpenseStatus.SUBMITTED,
        ExpenseAction.CANCEL: ExpenseStatus.CANCELLED,
    },
    ExpenseStatus.SUBMITTED: {
        ExpenseAction.APPROVE: ExpenseStatus.APPROVED,
        ExpenseAction.REJECT: ExpenseStatus.REJECTED,
        ExpenseAction.CANCEL: ExpenseStatus.CANCELLED,
    },
    ExpenseStatus.APPROVED: {
        ExpenseAction.CANCEL: ExpenseStatus.CANCELLED,
    },
    ExpenseStatus.REJECTED: {
        # Après correction, la dépense repart en validation (l'historique garde le rejet).
        ExpenseAction.SUBMIT: ExpenseStatus.SUBMITTED,
        ExpenseAction.CANCEL: ExpenseStatus.CANCELLED,
    },
    # Une dépense payée se règle par l'annulation de ses paiements (contre-écritures),
    # jamais par une annulation directe : l'argent réellement sorti doit rester tracé.
    ExpenseStatus.PAID: {},
    ExpenseStatus.CANCELLED: {},
}

# Statuts qui comptent dans le budget engagé (l'engagement naît de l'approbation).
COMMITTED_STATUSES = (ExpenseStatus.APPROVED, ExpenseStatus.PAID)


class PaymentMethod(models.TextChoices):
    CASH = "CASH", "Espèces"
    BANK_TRANSFER = "BANK_TRANSFER", "Virement bancaire"
    MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"
    CHEQUE = "CHEQUE", "Chèque"


class TransactionType(models.TextChoices):
    EXPENSE = "EXPENSE", "Dépense engagée"
    PAYMENT = "PAYMENT", "Paiement"
    ADJUSTMENT = "ADJUSTMENT", "Ajustement"
    CANCELLATION = "CANCELLATION", "Contre-écriture"


class TransactionDirection(models.TextChoices):
    DEBIT = "DEBIT", "Débit (consomme le budget)"
    CREDIT = "CREDIT", "Crédit (libère le budget)"


class BudgetLine(TimeStampedModel, SoftDeleteModel):
    """Poste budgétaire : ventilation du budget global par nature de dépense."""

    project = models.ForeignKey(
        "projects.Project",
        verbose_name="projet",
        on_delete=models.CASCADE,
        related_name="budget_lines",
    )
    label = models.CharField("libellé", max_length=160)
    category = models.CharField(
        "catégorie", max_length=16, choices=BudgetCategory.choices, default=BudgetCategory.MATERIALS
    )
    planned_amount = models.DecimalField("montant prévu (FCFA)", max_digits=15, decimal_places=0)
    order = models.PositiveIntegerField("ordre", default=0)
    notes = models.TextField("notes", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="créé par",
        on_delete=models.PROTECT,
        related_name="created_budget_lines",
    )

    class Meta:
        verbose_name = "poste budgétaire"
        verbose_name_plural = "postes budgétaires"
        ordering = ["order", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "label"], name="uniq_budget_line_label_per_project"
            ),
            models.CheckConstraint(
                condition=models.Q(planned_amount__gte=0), name="budget_line_amount_positive"
            ),
        ]
        indexes = [models.Index(fields=["project", "category"])]

    def __str__(self) -> str:  # pragma: no cover - confort d'administration
        return f"{self.project.code} · {self.label}"

    def clean(self):
        if self.planned_amount is not None and self.planned_amount < 0:
            raise ValidationError({"planned_amount": "Le montant prévu ne peut pas être négatif."})


class Expense(TimeStampedModel, SoftDeleteModel):
    """Dépense engagée sur un chantier, avec sa facture et son justificatif."""

    project = models.ForeignKey(
        "projects.Project", verbose_name="projet", on_delete=models.CASCADE, related_name="expenses"
    )
    budget_line = models.ForeignKey(
        BudgetLine,
        verbose_name="poste budgétaire",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expenses",
    )
    title = models.CharField("libellé", max_length=180)
    description = models.TextField("description", blank=True)
    amount = models.DecimalField("montant (FCFA)", max_digits=15, decimal_places=0)
    currency = models.CharField("devise", max_length=3, default="XAF")
    incurred_on = models.DateField("date de la dépense")
    status = models.CharField(
        "statut", max_length=16, choices=ExpenseStatus.choices, default=ExpenseStatus.DRAFT
    )
    supplier = models.CharField("fournisseur / prestataire", max_length=180, blank=True)
    # Facture : référencée (numéro + date) et, si fournie, stockée comme justificatif.
    invoice_number = models.CharField("numéro de facture", max_length=80, blank=True)
    invoice_date = models.DateField("date de facture", null=True, blank=True)
    receipt = models.FileField(
        "justificatif", upload_to="finance/receipts/", null=True, blank=True, max_length=255
    )
    receipt_hash = models.CharField("empreinte du justificatif", max_length=64, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="créée par",
        on_delete=models.PROTECT,
        related_name="created_expenses",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="approuvée par",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_expenses",
    )
    approved_at = models.DateTimeField("approuvée le", null=True, blank=True)
    cancelled_at = models.DateTimeField("annulée le", null=True, blank=True)

    class Meta:
        verbose_name = "dépense"
        verbose_name_plural = "dépenses"
        ordering = ["-incurred_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="expense_amount_positive"
            ),
            models.UniqueConstraint(
                fields=["project", "invoice_number"],
                condition=models.Q(invoice_number__gt=""),
                name="uniq_expense_invoice_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status", "deleted_at"]),
            models.Index(fields=["project", "incurred_on"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - confort d'administration
        return f"{self.project.code} · {self.title} ({self.get_status_display()})"

    @property
    def is_editable(self) -> bool:
        """Modifiable tant que l'argent n'est pas engagé.

        Un brouillon se corrige ; une dépense soumise ou **rejetée** aussi (on corrige la pièce
        manquante puis on resoumet) ; dès l'approbation, la dépense est figée : la correction
        passe par une annulation puis une nouvelle dépense, pour ne pas réécrire l'historique.
        """
        return self.status in {
            ExpenseStatus.DRAFT,
            ExpenseStatus.SUBMITTED,
            ExpenseStatus.REJECTED,
        }

    def clean(self):
        errors: dict[str, str] = {}
        if self.amount is not None:
            amount = Decimal(str(self.amount))
            self.amount = amount
            if amount < 0:
                errors["amount"] = "Le montant ne peut pas être négatif."
            elif amount != amount.to_integral_value():
                errors["amount"] = "Les montants sont exprimés en FCFA entiers (sans centimes)."
        if (
            self.budget_line_id
            and self.project_id
            and self.budget_line.project_id != self.project_id
        ):
            errors["budget_line"] = "Le poste budgétaire appartient à un autre projet."
        if self.invoice_date and self.incurred_on and self.invoice_date > self.incurred_on:
            errors["invoice_date"] = "La facture ne peut pas être postérieure à la dépense."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class Payment(TimeStampedModel, SoftDeleteModel):
    """Paiement d'une dépense approuvée. Une annulation crée une contre-écriture."""

    expense = models.ForeignKey(
        Expense, verbose_name="dépense", on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField("montant (FCFA)", max_digits=15, decimal_places=0)
    paid_on = models.DateField("payé le")
    method = models.CharField("moyen de paiement", max_length=16, choices=PaymentMethod.choices)
    reference = models.CharField("référence", max_length=120, blank=True)
    note = models.TextField("note", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="enregistré par",
        on_delete=models.PROTECT,
        related_name="recorded_payments",
    )
    cancelled_at = models.DateTimeField("annulé le", null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="annulé par",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cancelled_payments",
    )

    class Meta:
        verbose_name = "paiement"
        verbose_name_plural = "paiements"
        ordering = ["-paid_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="payment_amount_strictly_positive"
            )
        ]
        indexes = [models.Index(fields=["expense", "paid_on"])]

    def __str__(self) -> str:  # pragma: no cover - confort d'administration
        return f"Paiement {self.amount} FCFA · {self.expense.title}"

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None


class FinancialTransactionQuerySet(models.QuerySet):
    """Grand livre en lecture seule : aucune modification, aucune suppression."""

    def delete(self):
        raise IntegrityError("Le grand livre financier ne peut pas être supprimé.")

    def update(self, **kwargs):
        raise IntegrityError("Le grand livre financier n'est pas modifiable.")


class FinancialTransaction(models.Model):
    """Ligne de grand livre — **append-only**.

    Chaque effet financier (engagement, paiement, ajustement, contre-écriture) écrit une ligne.
    Le consommé, le payé et le solde sont des agrégats de ces lignes : c'est la seule source de
    vérité, ce qui rend toute divergence impossible sans trace.
    """

    project = models.ForeignKey(
        "projects.Project",
        verbose_name="projet",
        on_delete=models.PROTECT,
        related_name="financial_transactions",
    )
    type = models.CharField("type", max_length=16, choices=TransactionType.choices)
    direction = models.CharField("sens", max_length=8, choices=TransactionDirection.choices)
    amount = models.DecimalField("montant (FCFA)", max_digits=15, decimal_places=0)
    expense = models.ForeignKey(
        Expense,
        verbose_name="dépense",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transactions",
    )
    payment = models.ForeignKey(
        Payment,
        verbose_name="paiement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transactions",
    )
    budget_line = models.ForeignKey(
        BudgetLine,
        verbose_name="poste budgétaire",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transactions",
    )
    # Solde du budget **après** cette écriture : permet de relire l'historique sans recalcul.
    balance_after = models.DecimalField("solde après", max_digits=15, decimal_places=0)
    note = models.TextField("motif", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="auteur",
        on_delete=models.PROTECT,
        related_name="financial_transactions",
    )
    created_at = models.DateTimeField("date", auto_now_add=True, db_index=True)

    objects = FinancialTransactionQuerySet.as_manager()

    class Meta:
        verbose_name = "écriture financière"
        verbose_name_plural = "écritures financières"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["project", "type", "direction"]),
            models.Index(fields=["project", "created_at"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - confort d'administration
        return f"{self.get_type_display()} {self.amount} FCFA ({self.get_direction_display()})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise IntegrityError("Une écriture du grand livre n'est pas modifiable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise IntegrityError("Le grand livre financier ne peut pas être supprimé.")
