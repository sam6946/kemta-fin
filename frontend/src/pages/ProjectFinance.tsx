/**
 * Finances du chantier (MVP-010) — budget, dépenses, paiements et grand livre.
 *
 * Principes d'interface, alignés sur le backend :
 *
 * - **aucun calcul de montant côté client** : tous les totaux (engagé, payé, solde, taux) sont
 *   affichés tels que le serveur les renvoie ;
 * - les actions proposées viennent du champ `permissions` de chaque dépense (le backend reste
 *   l'autorité) ; un refus est toujours expliqué par le message du serveur ;
 * - un dépassement de budget n'est jamais silencieux : le serveur répond `422 budget_exceeded`
 *   et l'interface demande alors un motif explicite avant de réessayer ;
 * - les opérations financières ne sont **pas** mises en file hors ligne (elles exigent
 *   l'autorité du serveur) : hors connexion, l'écran l'indique clairement.
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../api/client";
import {
  EXPENSE_STATUS_TONES,
  availableActions,
  financeApi,
  openReceipt,
  type BudgetLine,
  type BudgetLineInput,
  type Expense,
  type ExpenseAction,
  type ExpensePage,
  type ExpenseStatus,
  type FinanceOverview,
  type FinancialTransaction,
  type Payment,
  type PaymentMethod,
  type TransactionDirection,
  type TransactionPage,
  type BudgetLinePage,
} from "../api/finance";
import type { Project } from "../api/projects";
import { messageForErrorCode } from "../auth/passwordPolicy";
import { Alert, Button, Field } from "../components/ui";
import { formatDate, formatFcfa, formatPercent, parseFcfaInput } from "../lib/format";

type Panel = "budget" | "expenses" | "ledger";

type ExpenseDraft = {
  title: string;
  amountInput: string;
  incurred_on: string;
  budget_line: string;
  supplier: string;
  invoice_number: string;
};

type AdjustmentDraft = {
  amountInput: string;
  direction: TransactionDirection;
  reason: string;
};

const STATUS_FILTERS: { value: "" | ExpenseStatus; label: string }[] = [
  { value: "", label: "Toutes" },
  { value: "DRAFT", label: "Brouillons" },
  { value: "SUBMITTED", label: "À approuver" },
  { value: "APPROVED", label: "Engagées" },
  { value: "PAID", label: "Payées" },
  { value: "REJECTED", label: "Rejetées" },
  { value: "CANCELLED", label: "Annulées" },
];

const PAYMENT_METHODS: { value: PaymentMethod; label: string }[] = [
  { value: "CASH", label: "Espèces" },
  { value: "BANK_TRANSFER", label: "Virement bancaire" },
  { value: "MOBILE_MONEY", label: "Mobile Money" },
  { value: "CHEQUE", label: "Chèque" },
];

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function describeError(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.isOffline) {
      return "Pas de connexion : les opérations financières exigent le serveur. Réessayez une fois en ligne.";
    }
    const overruns = caught.details.overruns as { label?: string; over?: number }[] | undefined;
    if (caught.code === "budget_exceeded" && overruns?.length) {
      const detail = overruns
        .map((row) => `${row.label ?? "budget"} : +${formatFcfa(row.over ?? 0)}`)
        .join(" · ");
      return `${caught.message} Dépassement — ${detail}`;
    }
    return caught.message || messageForErrorCode(caught.code);
  }
  return messageForErrorCode("server_error");
}

export default function ProjectFinance({
  project,
  onChanged,
}: {
  project: Project;
  onChanged?: () => void;
}) {
  const [overview, setOverview] = useState<FinanceOverview | null>(null);
  const [budget, setBudget] = useState<BudgetLinePage | null>(null);
  const [expenses, setExpenses] = useState<ExpensePage | null>(null);
  const [ledger, setLedger] = useState<TransactionPage | null>(null);
  const [panel, setPanel] = useState<Panel>("budget");
  const [statusFilter, setStatusFilter] = useState<"" | ExpenseStatus>("");
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const [newLine, setNewLine] = useState<BudgetLineInput & { amountInput: string }>({
    label: "",
    planned_amount: 0,
    amountInput: "",
    category: "MATERIALS",
  });
  const [newExpense, setNewExpense] = useState<ExpenseDraft>({
    title: "",
    amountInput: "",
    incurred_on: todayIso(),
    budget_line: "",
    supplier: "",
    invoice_number: "",
  });
  const [adjustment, setAdjustment] = useState<AdjustmentDraft>({
    amountInput: "",
    direction: "CREDIT",
    reason: "",
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const query = statusFilter ? `?status=${statusFilter}` : "";
      const [overviewData, budgetData, expenseData, ledgerData] = await Promise.all([
        financeApi.overview(project.id),
        financeApi.budgetLines(project.id),
        financeApi.expenses(project.id, query),
        financeApi.transactions(project.id),
      ]);
      setOverview(overviewData);
      setBudget(budgetData);
      setExpenses(expenseData);
      setLedger(ledgerData);
      setError(null);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setLoading(false);
    }
  }, [project.id, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      await action();
      setFeedback(success);
      await load();
      onChanged?.();
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  if (loading && !overview) return <div className="screen-center">Chargement des finances…</div>;

  const permissions = overview?.permissions;
  const canSettle = Boolean(permissions?.settle_finance);
  const canManage = Boolean(permissions?.manage_finance);
  const canCreateExpense = canManage;

  return (
    <section className="card" id="finances" data-testid="project-finance">
      <h2 style={{ fontSize: "1rem", marginTop: 0 }}>Finances du chantier</h2>
      <p className="field-hint">
        Totaux calculés par le serveur à partir du grand livre : aucun montant n'est ni saisi ni
        recalculé dans l'interface.
      </p>

      <div data-testid="finance-feedback">
        {error ? <Alert tone="error">{error}</Alert> : null}
        {feedback ? <Alert tone="success">{feedback}</Alert> : null}
        {overview?.budget.threshold === "WARNING" ? (
          <Alert tone="warning">Budget consommé à {formatPercent(overview.budget.consumption_rate)} — seuil d'alerte des 80 % atteint.</Alert>
        ) : null}
        {overview?.budget.threshold === "EXCEEDED" ? (
          <Alert tone="error">Budget dépassé : {formatFcfa(overview.budget.committed)} engagés pour {formatFcfa(overview.budget.planned)} prévus.</Alert>
        ) : null}
      </div>

      {overview ? (
        <div className="grid" data-testid="finance-metrics">
          <div className="metric">
            <div className="metric-label">Budget prévu</div>
            <div className="metric-value">{formatFcfa(overview.budget.planned)}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Engagé</div>
            <div className="metric-value" data-testid="metric-committed">
              {formatFcfa(overview.budget.committed)}
            </div>
          </div>
          <div className="metric">
            <div className="metric-label">Payé</div>
            <div className="metric-value" data-testid="metric-paid">
              {formatFcfa(overview.budget.paid)}
            </div>
          </div>
          <div className="metric">
            <div className="metric-label">Reste à payer</div>
            <div className="metric-value">{formatFcfa(overview.budget.outstanding)}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Solde disponible</div>
            <div className="metric-value" data-testid="metric-balance">
              {formatFcfa(overview.budget.balance)}
            </div>
          </div>
          <div className="metric">
            <div className="metric-label">Taux de consommation</div>
            <div className="metric-value">{formatPercent(overview.budget.consumption_rate)}</div>
          </div>
        </div>
      ) : null}

      {overview?.alerts.length ? (
        <ul className="sync-list" data-testid="finance-alerts" style={{ marginTop: 8 }}>
          {overview.alerts.map((alert) => (
            <li key={`${alert.code}-${alert.budget_line ?? 0}`}>
              <Alert tone={alert.severity === "critical" ? "error" : "warning"}>
                {alert.message}
              </Alert>
            </li>
          ))}
        </ul>
      ) : null}

      <div style={{ display: "flex", gap: 8, margin: "12px 0" }} role="tablist">
        <Button
          variant={panel === "budget" ? "primary" : "ghost"}
          role="tab"
          aria-selected={panel === "budget"}
          onClick={() => setPanel("budget")}
        >
          Postes budgétaires ({budget?.count ?? 0})
        </Button>
        <Button
          variant={panel === "expenses" ? "primary" : "ghost"}
          role="tab"
          aria-selected={panel === "expenses"}
          onClick={() => setPanel("expenses")}
        >
          Dépenses ({expenses?.count ?? 0})
        </Button>
        <Button
          variant={panel === "ledger" ? "primary" : "ghost"}
          role="tab"
          aria-selected={panel === "ledger"}
          onClick={() => setPanel("ledger")}
        >
          Grand livre ({ledger?.count ?? 0})
        </Button>
      </div>

      {panel === "budget" ? (
        <BudgetPanel
          lines={budget?.results ?? []}
          unallocated={budget?.summary.unallocated ?? 0}
          canSettle={canSettle}
          busy={busy}
          newLine={newLine}
          setNewLine={setNewLine}
          onSubmit={() =>
            run(
              () =>
                financeApi.createBudgetLine(project.id, {
                  label: newLine.label,
                  planned_amount: newLine.planned_amount,
                  category: newLine.category,
                }),
              "Poste budgétaire créé.",
            ).then(() =>
              setNewLine({ label: "", planned_amount: 0, amountInput: "", category: "MATERIALS" }),
            )
          }
          onRevised={(line, amount) =>
            run(
              () => financeApi.updateBudgetLine(line.id, { planned_amount: amount }),
              "Poste budgétaire révisé.",
            )
          }
          onDelete={(line) =>
            run(() => financeApi.deleteBudgetLine(line.id), "Poste budgétaire supprimé.")
          }
        />
      ) : null}

      {panel === "expenses" ? (
        <ExpensesPanel
          expenses={expenses}
          budgetLines={budget?.results ?? []}
          canCreate={canCreateExpense}
          busy={busy}
          statusFilter={statusFilter}
          setStatusFilter={setStatusFilter}
          newExpense={newExpense}
          setNewExpense={setNewExpense}
          onCreate={(amount) =>
            run(
              () =>
                financeApi.createExpense(project.id, {
                  title: newExpense.title,
                  amount,
                  incurred_on: newExpense.incurred_on,
                  budget_line: newExpense.budget_line ? Number(newExpense.budget_line) : null,
                  supplier: newExpense.supplier,
                  invoice_number: newExpense.invoice_number,
                }),
              "Dépense enregistrée en brouillon.",
            ).then(() =>
              setNewExpense({
                title: "",
                amountInput: "",
                incurred_on: todayIso(),
                budget_line: "",
                supplier: "",
                invoice_number: "",
              }),
            )
          }
          onTransition={(expense, action, comment, overrideReason) =>
            run(
              () => financeApi.transition(expense.id, action, comment, overrideReason),
              `${actionLabel(action)} : ${expense.title}.`,
            )
          }
          onPay={(expense, amount, method, reference) =>
            run(
              () =>
                financeApi.recordPayment(expense.id, {
                  amount,
                  method,
                  reference,
                  paid_on: todayIso(),
                }),
              `Paiement enregistré sur « ${expense.title} ».`,
            )
          }
          onCancelPayment={(payment, reason) =>
            run(
              () => financeApi.cancelPayment(payment.id, reason),
              "Paiement annulé (contre-écriture au grand livre).",
            )
          }
          onReceipt={(expense, file) =>
            run(() => financeApi.uploadReceipt(expense.id, file), "Justificatif enregistré.")
          }
        />
      ) : null}

      {panel === "ledger" ? (
        <LedgerPanel
          ledger={ledger}
          canSettle={canSettle}
          busy={busy}
          adjustment={adjustment}
          setAdjustment={setAdjustment}
          onSubmit={() =>
            run(
              () =>
                financeApi.adjust(
                  project.id,
                  Number(adjustment.amountInput.replace(/\s/g, "")),
                  adjustment.direction,
                  adjustment.reason,
                ),
              "Ajustement enregistré.",
            ).then(() => setAdjustment({ amountInput: "", direction: "CREDIT", reason: "" }))
          }
        />
      ) : null}
    </section>
  );
}

function actionLabel(action: ExpenseAction): string {
  switch (action) {
    case "SUBMIT":
      return "Dépense soumise";
    case "APPROVE":
      return "Dépense approuvée";
    case "REJECT":
      return "Dépense rejetée";
    default:
      return "Dépense annulée";
  }
}

function BudgetPanel({
  lines,
  unallocated,
  canSettle,
  busy,
  newLine,
  setNewLine,
  onSubmit,
  onRevised,
  onDelete,
}: {
  lines: BudgetLine[];
  unallocated: number;
  canSettle: boolean;
  busy: boolean;
  newLine: BudgetLineInput & { amountInput: string };
  setNewLine: (value: BudgetLineInput & { amountInput: string }) => void;
  onSubmit: () => void;
  onRevised: (line: BudgetLine, amount: number) => void;
  onDelete: (line: BudgetLine) => void;
}) {
  const [revised, setRevised] = useState<Record<number, string>>({});
  const parsed = parseFcfaInput(newLine.amountInput);
  const invalid = parsed === null;

  return (
    <div data-testid="budget-panel">
      <p className="field-hint">
        Non alloué : <strong>{formatFcfa(unallocated)}</strong>. La somme des postes ne peut pas
        dépasser le budget global du projet.
      </p>
      <ul className="sync-list" data-testid="budget-lines">
        {lines.map((line) => (
          <li key={line.id} className="metric" data-testid="budget-line">
            <strong>{line.label}</strong>
            <div className="field-hint">{line.category_label}</div>
            <div className="grid" style={{ marginTop: 6 }}>
              <div>
                <div className="metric-label">Prévu</div>
                <div data-testid="line-planned">{formatFcfa(line.planned_amount)}</div>
              </div>
              <div>
                <div className="metric-label">Engagé</div>
                {/* Valeur calculée par le serveur depuis le grand livre. */}
                <div data-testid="line-committed">{formatFcfa(line.committed_amount)}</div>
              </div>
              <div>
                <div className="metric-label">Dépenses</div>
                <div>{line.expense_count}</div>
              </div>
            </div>
            {canSettle ? (
              <div style={{ display: "flex", gap: 8, alignItems: "flex-end", marginTop: 8 }}>
                <Field label={`Réviser « ${line.label} »`}>
                  <input
                    aria-label={`Nouveau montant de ${line.label}`}
                    inputMode="numeric"
                    value={revised[line.id] ?? ""}
                    onChange={(event) => setRevised({ ...revised, [line.id]: event.target.value })}
                    placeholder={String(line.planned_amount)}
                  />
                </Field>
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => {
                    const amount = parseFcfaInput(revised[line.id] ?? "");
                    if (amount === null) return;
                    onRevised(line, amount);
                  }}
                >
                  Réviser
                </Button>
                <Button variant="ghost" disabled={busy} onClick={() => onDelete(line)}>
                  Supprimer
                </Button>
              </div>
            ) : null}
          </li>
        ))}
      </ul>

      {canSettle ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (invalid) return;
            onSubmit();
          }}
          noValidate
          data-testid="budget-line-form"
        >
          <h3 style={{ fontSize: "0.95rem" }}>Nouveau poste budgétaire</h3>
          <Field label="Libellé">
            <input
              value={newLine.label}
              onChange={(event) => setNewLine({ ...newLine, label: event.target.value })}
              required
            />
          </Field>
          <Field label="Montant prévu (FCFA)" error={invalid ? "Montant entier en FCFA requis." : undefined}>
            <input
              inputMode="numeric"
              value={newLine.amountInput}
              onChange={(event) => {
                const raw = event.target.value;
                setNewLine({ ...newLine, amountInput: raw, planned_amount: parseFcfaInput(raw) ?? 0 });
              }}
              required
            />
          </Field>
          <Field label="Catégorie">
            <select
              value={newLine.category ?? "MATERIALS"}
              onChange={(event) =>
                setNewLine({ ...newLine, category: event.target.value as BudgetLineInput["category"] })
              }
            >
              <option value="MATERIALS">Matériaux</option>
              <option value="LABOUR">Main-d'œuvre</option>
              <option value="EQUIPMENT">Matériel et engins</option>
              <option value="SUBCONTRACT">Sous-traitance</option>
              <option value="TRANSPORT">Transport</option>
              <option value="ADMIN">Frais administratifs</option>
              <option value="OTHER">Autres</option>
            </select>
          </Field>
          <Button type="submit" loading={busy} disabled={invalid}>
            Ajouter le poste
          </Button>
        </form>
      ) : (
        <Alert tone="info">
          Vous consultez le budget en lecture seule : seul un rôle de pilotage financier
          (maître d'ouvrage, promoteur, gestionnaire financier) peut le modifier.
        </Alert>
      )}
    </div>
  );
}

function ExpensesPanel({
  expenses,
  budgetLines,
  canCreate,
  busy,
  statusFilter,
  setStatusFilter,
  newExpense,
  setNewExpense,
  onCreate,
  onTransition,
  onPay,
  onCancelPayment,
  onReceipt,
}: {
  expenses: ExpensePage | null;
  budgetLines: BudgetLine[];
  canCreate: boolean;
  busy: boolean;
  statusFilter: "" | ExpenseStatus;
  setStatusFilter: (value: "" | ExpenseStatus) => void;
  newExpense: ExpenseDraft;
  setNewExpense: (value: ExpenseDraft) => void;
  onCreate: (amount: number) => void;
  onTransition: (
    expense: Expense,
    action: ExpenseAction,
    comment: string,
    overrideReason: string,
  ) => void;
  onPay: (expense: Expense, amount: number, method: PaymentMethod, reference: string) => void;
  onCancelPayment: (payment: Payment, reason: string) => void;
  onReceipt: (expense: Expense, file: File) => void;
}) {
  const amount = parseFcfaInput(newExpense.amountInput);

  return (
    <div data-testid="expenses-panel">
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        {STATUS_FILTERS.map((filter) => (
          <Button
            key={filter.value || "all"}
            variant={statusFilter === filter.value ? "primary" : "ghost"}
            onClick={() => setStatusFilter(filter.value)}
          >
            {filter.label}
            {filter.value && expenses
              ? ` (${expenses.counts[filter.value.toLowerCase() as Lowercase<ExpenseStatus>] ?? 0})`
              : ""}
          </Button>
        ))}
      </div>

      {canCreate ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (amount === null) return;
            onCreate(amount);
          }}
          noValidate
          data-testid="expense-form"
        >
          <h3 style={{ fontSize: "0.95rem" }}>Nouvelle dépense</h3>
          <Field label="Objet">
            <input
              value={newExpense.title}
              onChange={(event) => setNewExpense({ ...newExpense, title: event.target.value })}
              required
            />
          </Field>
          <Field
            label="Montant (FCFA)"
            error={amount === null ? "Montant entier en FCFA requis (pas de centimes)." : undefined}
          >
            <input
              inputMode="numeric"
              value={newExpense.amountInput}
              onChange={(event) => setNewExpense({ ...newExpense, amountInput: event.target.value })}
              required
            />
          </Field>
          <Field label="Date de la dépense">
            <input
              type="date"
              value={newExpense.incurred_on}
              onChange={(event) => setNewExpense({ ...newExpense, incurred_on: event.target.value })}
              required
            />
          </Field>
          <Field label="Poste budgétaire (optionnel)">
            <select
              value={newExpense.budget_line}
              onChange={(event) => setNewExpense({ ...newExpense, budget_line: event.target.value })}
            >
              <option value="">— Aucun —</option>
              {budgetLines.map((line) => (
                <option key={line.id} value={line.id}>
                  {line.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Fournisseur">
            <input
              value={newExpense.supplier}
              onChange={(event) => setNewExpense({ ...newExpense, supplier: event.target.value })}
            />
          </Field>
          <Field label="N° de facture">
            <input
              value={newExpense.invoice_number}
              onChange={(event) =>
                setNewExpense({ ...newExpense, invoice_number: event.target.value })
              }
            />
          </Field>
          <Button type="submit" loading={busy} disabled={amount === null}>
            Enregistrer en brouillon
          </Button>
          <p className="field-hint">
            Un brouillon n'engage rien : le budget est consommé à l'approbation par un responsable
            financier.
          </p>
        </form>
      ) : (
        <Alert tone="info">
          Vous pouvez consulter les dépenses, mais leur création demande la permission « gestion
          financière » sur ce projet.
        </Alert>
      )}

      <ul className="sync-list" data-testid="expense-list">
        {(expenses?.results ?? []).map((expense) => (
          <ExpenseCard
            key={expense.id}
            expense={expense}
            busy={busy}
            onTransition={onTransition}
            onPay={onPay}
            onCancelPayment={onCancelPayment}
            onReceipt={onReceipt}
          />
        ))}
      </ul>
      {expenses && expenses.results.length === 0 ? (
        <p className="field-hint">Aucune dépense pour ce filtre.</p>
      ) : null}
    </div>
  );
}

function ExpenseCard({
  expense,
  busy,
  onTransition,
  onPay,
  onCancelPayment,
  onReceipt,
}: {
  expense: Expense;
  busy: boolean;
  onTransition: (
    expense: Expense,
    action: ExpenseAction,
    comment: string,
    overrideReason: string,
  ) => void;
  onPay: (expense: Expense, amount: number, method: PaymentMethod, reference: string) => void;
  onCancelPayment: (payment: Payment, reason: string) => void;
  onReceipt: (expense: Expense, file: File) => void;
}) {
  const [comment, setComment] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [payment, setPayment] = useState({ amountInput: "", method: "CASH" as PaymentMethod, reference: "" });
  const [paymentError, setPaymentError] = useState<string | null>(null);

  const actions = availableActions(expense);
  const paidAmount = parseFcfaInput(payment.amountInput);

  return (
    <li className="metric" data-testid="expense-row">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <div>
          <strong>{expense.title}</strong>
          <div className="field-hint">
            {expense.budget_line_label ?? "sans poste"} · {formatDate(expense.incurred_on)}
            {expense.supplier ? ` · ${expense.supplier}` : ""}
            {expense.invoice_number ? ` · ${expense.invoice_number}` : ""}
          </div>
        </div>
        <span className={`evidence-status status-${EXPENSE_STATUS_TONES[expense.status]}`} data-testid="expense-status">
          {expense.status_label}
        </span>
      </div>

      <div className="grid" style={{ marginTop: 6 }}>
        <div>
          <div className="metric-label">Montant</div>
          <div data-testid="expense-amount">{formatFcfa(expense.amount)}</div>
        </div>
        <div>
          <div className="metric-label">Payé</div>
          <div data-testid="expense-paid">{formatFcfa(expense.paid_amount)}</div>
        </div>
        <div>
          <div className="metric-label">Reste dû</div>
          <div>{formatFcfa(expense.outstanding_amount)}</div>
        </div>
      </div>

      {expense.payments.length ? (
        <ul className="sync-list" data-testid="expense-payments" style={{ marginTop: 6 }}>
          {expense.payments.map((row) => (
            <li key={row.id} className="field-hint">
              {formatFcfa(row.amount)} le {formatDate(row.paid_on)} — {row.method_label}
              {row.reference ? ` (${row.reference})` : ""}
              {row.is_cancelled ? " · annulé" : ""}
              {!row.is_cancelled && expense.permissions.settle_finance ? (
                <Button variant="ghost" disabled={busy} onClick={() => onCancelPayment(row, "Annulation depuis l'interface")}>
                  Annuler le paiement
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
        {actions.map((entry) => (
          <Button
            key={entry.action}
            variant="ghost"
            disabled={busy}
            onClick={() => onTransition(expense, entry.action, comment, overrideReason)}
          >
            {entry.label}
          </Button>
        ))}
        {expense.receipt_url ? (
          <Button
            variant="ghost"
            onClick={() =>
              void openReceipt(expense.id).then((url) => window.open(url, "_blank", "noopener"))
            }
          >
            Voir le justificatif
          </Button>
        ) : null}
      </div>

      {expense.permissions.manage_finance ? (
        <div style={{ marginTop: 8 }}>
          <Field label={`Motif ou commentaire — ${expense.title}`}>
            <input
              aria-label={`Motif concernant ${expense.title}`}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              placeholder="Obligatoire pour un rejet"
            />
          </Field>
          <Field label={`Motif de dépassement — ${expense.title}`}>
            <input
              aria-label={`Motif de dépassement de ${expense.title}`}
              value={overrideReason}
              onChange={(event) => setOverrideReason(event.target.value)}
              placeholder="Requis si la dépense dépasse le budget (min. 10 caractères)"
            />
          </Field>
          <Field
            label={`Justificatif de ${expense.title}`}
            hint="Photo (JPEG, PNG, WebP) ou PDF, vérifié par le serveur."
          >
            <input
              type="file"
              accept="application/pdf,image/jpeg,image/png,image/webp"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) onReceipt(expense, file);
              }}
            />
          </Field>
        </div>
      ) : null}

      {expense.permissions.can_pay ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (paidAmount === null || paidAmount <= 0) {
              setPaymentError("Montant entier en FCFA requis.");
              return;
            }
            if (paidAmount > expense.outstanding_amount) {
              setPaymentError(
                `Le paiement dépasse le reste dû (${formatFcfa(expense.outstanding_amount)}).`,
              );
              return;
            }
            setPaymentError(null);
            onPay(expense, paidAmount, payment.method, payment.reference);
          }}
          noValidate
        >
          <Field label={`Paiement sur « ${expense.title} »`} error={paymentError ?? undefined}>
            <input
              aria-label={`Montant du paiement pour ${expense.title}`}
              inputMode="numeric"
              value={payment.amountInput}
              onChange={(event) => setPayment({ ...payment, amountInput: event.target.value })}
              required
            />
          </Field>
          <Field label={`Moyen de paiement — ${expense.title}`}>
            <select
              aria-label={`Moyen de paiement pour ${expense.title}`}
              value={payment.method}
              onChange={(event) =>
                setPayment({ ...payment, method: event.target.value as PaymentMethod })
              }
            >
              {PAYMENT_METHODS.map((method) => (
                <option key={method.value} value={method.value}>
                  {method.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label={`Référence du paiement — ${expense.title}`}>
            <input
              aria-label={`Référence du paiement pour ${expense.title}`}
              value={payment.reference}
              onChange={(event) => setPayment({ ...payment, reference: event.target.value })}
            />
          </Field>
          <Button type="submit" loading={busy}>
            Enregistrer le paiement
          </Button>
        </form>
      ) : null}
    </li>
  );
}

function LedgerPanel({
  ledger,
  canSettle,
  busy,
  adjustment,
  setAdjustment,
  onSubmit,
}: {
  ledger: TransactionPage | null;
  canSettle: boolean;
  busy: boolean;
  adjustment: { amountInput: string; direction: TransactionDirection; reason: string };
  setAdjustment: (value: {
    amountInput: string;
    direction: TransactionDirection;
    reason: string;
  }) => void;
  onSubmit: () => void;
}) {
  const amount = parseFcfaInput(adjustment.amountInput);

  return (
    <div data-testid="ledger-panel">
      <p className="field-hint">
        Grand livre en lecture seule : chaque écriture est conservée (aucune suppression, aucune
        modification), avec le solde du budget après opération.
      </p>
      {ledger ? (
        <div className="grid" data-testid="ledger-totals">
          <div className="metric">
            <div className="metric-label">Engagé</div>
            <div className="metric-value">{formatFcfa(ledger.totals.committed)}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Payé</div>
            <div className="metric-value">{formatFcfa(ledger.totals.paid)}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Ajustements</div>
            <div className="metric-value">{formatFcfa(ledger.totals.adjustments)}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Solde</div>
            <div className="metric-value">{formatFcfa(ledger.totals.balance)}</div>
          </div>
        </div>
      ) : null}

      <ul className="sync-list" data-testid="ledger-entries">
        {(ledger?.results ?? []).map((entry: FinancialTransaction) => (
          <li key={entry.id} className="metric" data-testid="ledger-row">
            <strong>{entry.type_label}</strong>{" "}
            <span className="field-hint">
              {entry.direction_label} · {formatFcfa(entry.amount)} · solde {formatFcfa(entry.balance_after)}
            </span>
            <div className="field-hint">
              {formatDate(entry.created_at)} ·{" "}
              {entry.created_by ? `${entry.created_by.first_name} ${entry.created_by.last_name}` : "système"}
              {entry.expense_title ? ` · ${entry.expense_title}` : ""}
              {entry.note ? ` · ${entry.note}` : ""}
            </div>
          </li>
        ))}
      </ul>

      {canSettle ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (amount === null) return;
            onSubmit();
          }}
          noValidate
          data-testid="adjustment-form"
        >
          <h3 style={{ fontSize: "0.95rem" }}>Ajustement motivé</h3>
          <p className="field-hint">
            Correction manuelle du budget engagé : le motif est obligatoire et journalisé
            (ancienne et nouvelle valeur).
          </p>
          <Field label="Montant (FCFA)" error={amount === null ? "Montant entier requis." : undefined}>
            <input
              inputMode="numeric"
              value={adjustment.amountInput}
              onChange={(event) =>
                setAdjustment({ ...adjustment, amountInput: event.target.value })
              }
              required
            />
          </Field>
          <Field label="Sens">
            <select
              value={adjustment.direction}
              onChange={(event) =>
                setAdjustment({
                  ...adjustment,
                  direction: event.target.value as TransactionDirection,
                })
              }
            >
              <option value="DEBIT">Débit (consomme le budget)</option>
              <option value="CREDIT">Crédit (libère le budget)</option>
            </select>
          </Field>
          <Field label="Motif">
            <input
              value={adjustment.reason}
              onChange={(event) => setAdjustment({ ...adjustment, reason: event.target.value })}
              required
            />
          </Field>
          <Button type="submit" loading={busy} disabled={amount === null}>
            Enregistrer l'ajustement
          </Button>
        </form>
      ) : null}
    </div>
  );
}
