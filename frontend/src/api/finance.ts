/**
 * Appels financiers — contrat `docs/api-contract.md` §7 (MVP-010).
 *
 * Règle constante : **aucun total n'est envoyé au serveur**. Le consommé, le payé, le solde et
 * le taux affichés proviennent toujours des réponses de l'API (elles-mêmes calculées depuis le
 * grand livre) : l'interface ne recalcule jamais un montant.
 */

import { ApiError, request, tokens } from "./client";
import type { Paginated } from "./organizations";

export type ExpenseStatus = "DRAFT" | "SUBMITTED" | "APPROVED" | "REJECTED" | "PAID" | "CANCELLED";
export type ExpenseAction = "SUBMIT" | "APPROVE" | "REJECT" | "CANCEL";
export type BudgetCategory =
  | "MATERIALS"
  | "LABOUR"
  | "EQUIPMENT"
  | "SUBCONTRACT"
  | "TRANSPORT"
  | "ADMIN"
  | "OTHER";
export type PaymentMethod = "CASH" | "BANK_TRANSFER" | "MOBILE_MONEY" | "CHEQUE";
export type TransactionType = "EXPENSE" | "PAYMENT" | "ADJUSTMENT" | "CANCELLATION";
export type TransactionDirection = "DEBIT" | "CREDIT";

export type UserSummary = {
  id: number;
  first_name: string;
  last_name: string;
  phone_masked: string;
  role_label: string;
};

export type BudgetLine = {
  id: number;
  project: number;
  label: string;
  category: BudgetCategory;
  category_label: string;
  planned_amount: number;
  /** Calculé par le serveur depuis le grand livre. */
  committed_amount: number;
  expense_count: number;
  order: number;
  notes: string;
  permissions: { manage_finance: boolean; create_expense: boolean };
  created_at: string;
  updated_at: string;
};

export type BudgetSummary = {
  planned: number;
  allocated: number;
  unallocated: number;
  committed: number;
  paid: number;
  outstanding: number;
  balance: number;
  consumption_rate: string | number;
  threshold: "OK" | "WARNING" | "EXCEEDED";
  currency: string;
  lines: {
    budget_line: number;
    label: string;
    category: BudgetCategory;
    planned: number;
    committed: number;
  }[];
  alerts: {
    code: "BUDGET_THRESHOLD_REACHED" | "BUDGET_EXCEEDED" | "BUDGET_LINE_EXCEEDED";
    severity: "warning" | "critical";
    message: string;
    amount: number;
    budget_line?: number;
  }[];
};

export type Payment = {
  id: number;
  expense: number;
  amount: number;
  paid_on: string;
  method: PaymentMethod;
  method_label: string;
  reference: string;
  note: string;
  created_by: UserSummary | null;
  cancelled_at: string | null;
  cancelled_by: UserSummary | null;
  is_cancelled: boolean;
  created_at: string;
};

export type ExpensePermissions = {
  view_finance: boolean;
  manage_finance: boolean;
  settle_finance: boolean;
  can_approve: boolean;
  can_pay: boolean;
  can_edit: boolean;
  can_cancel: boolean;
};

export type Expense = {
  id: number;
  project: number;
  budget_line: number | null;
  budget_line_label: string | null;
  title: string;
  description: string;
  amount: number;
  currency: string;
  incurred_on: string;
  status: ExpenseStatus;
  status_label: string;
  supplier: string;
  invoice_number: string;
  invoice_date: string | null;
  receipt_url: string | null;
  receipt_hash: string;
  paid_amount: number;
  outstanding_amount: number;
  payments: Payment[];
  is_editable: boolean;
  created_by: UserSummary | null;
  approved_by: UserSummary | null;
  approved_at: string | null;
  cancelled_at: string | null;
  permissions: ExpensePermissions;
  created_at: string;
  updated_at: string;
};

export type FinancialTransaction = {
  id: number;
  project: number;
  type: TransactionType;
  type_label: string;
  direction: TransactionDirection;
  direction_label: string;
  amount: number;
  /** Solde du budget après cette écriture, calculé par le serveur. */
  balance_after: number;
  expense: number | null;
  expense_title: string | null;
  payment: number | null;
  budget_line: number | null;
  budget_line_label: string | null;
  note: string;
  created_by: UserSummary | null;
  created_at: string;
};

export type FinancePermissions = {
  view_finance: boolean;
  manage_finance: boolean;
  settle_finance: boolean;
  can_approve: boolean;
  can_pay: boolean;
  can_edit: boolean;
  can_cancel: boolean;
};

export type FinanceOverview = {
  project: { id: number; code: string; name: string; currency: string };
  budget: Omit<BudgetSummary, "lines" | "alerts">;
  lines: BudgetSummary["lines"];
  alerts: BudgetSummary["alerts"];
  permissions: FinancePermissions;
  generated_at: string;
};

export type BudgetLinePage = {
  count: number;
  results: BudgetLine[];
  summary: BudgetSummary;
  categories: { value: BudgetCategory; label: string }[];
};

export type ExpensePage = Paginated<Expense> & {
  summary: BudgetSummary;
  counts: Record<Lowercase<ExpenseStatus>, number>;
};

export type TransactionPage = Paginated<FinancialTransaction> & {
  totals: { committed: number; paid: number; adjustments: number; balance: number };
};

export type ExpenseInput = {
  title: string;
  amount: number;
  incurred_on: string;
  budget_line?: number | null;
  description?: string;
  supplier?: string;
  invoice_number?: string;
  invoice_date?: string | null;
};

export type PaymentInput = {
  amount: number;
  paid_on?: string | null;
  method?: PaymentMethod;
  reference?: string;
  note?: string;
};

export type BudgetLineInput = {
  label: string;
  planned_amount: number;
  category?: BudgetCategory;
  order?: number;
  notes?: string;
};

export const EXPENSE_STATUS_TONES: Record<
  ExpenseStatus,
  "info" | "success" | "error" | "warning"
> = {
  DRAFT: "info",
  SUBMITTED: "warning",
  APPROVED: "success",
  REJECTED: "error",
  PAID: "success",
  CANCELLED: "error",
};

/** Actions proposées par l'interface, dans l'ordre du cycle de vie. */
export function availableActions(expense: Expense): { action: ExpenseAction; label: string }[] {
  const permissions = expense.permissions;
  switch (expense.status) {
    case "DRAFT":
      return permissions.manage_finance
        ? [
            { action: "SUBMIT", label: "Soumettre" },
            { action: "CANCEL", label: "Annuler" },
          ]
        : [];
    case "SUBMITTED":
      return permissions.settle_finance
        ? [
            { action: "APPROVE", label: "Approuver" },
            { action: "REJECT", label: "Rejeter" },
            { action: "CANCEL", label: "Annuler" },
          ]
        : [];
    case "REJECTED":
      return permissions.manage_finance
        ? [
            { action: "SUBMIT", label: "Resoumettre" },
            { action: "CANCEL", label: "Annuler" },
          ]
        : [];
    case "APPROVED":
      return permissions.settle_finance ? [{ action: "CANCEL", label: "Annuler" }] : [];
    default:
      return [];
  }
}

export const financeApi = {
  overview: (projectId: string | number) =>
    request<FinanceOverview>(`/projects/${projectId}/finance/`, { auth: true }),

  budgetLines: (projectId: string | number) =>
    request<BudgetLinePage>(`/projects/${projectId}/budget-lines/`, { auth: true }),

  createBudgetLine: (projectId: string | number, payload: BudgetLineInput) =>
    request<BudgetLine>(`/projects/${projectId}/budget-lines/`, {
      method: "POST",
      auth: true,
      body: payload,
    }),

  updateBudgetLine: (id: number, payload: Partial<BudgetLineInput>) =>
    request<BudgetLine>(`/budget-lines/${id}/`, { method: "PATCH", auth: true, body: payload }),

  deleteBudgetLine: (id: number) =>
    request<void>(`/budget-lines/${id}/`, { method: "DELETE", auth: true }),

  expenses: (projectId: string | number, query = "") =>
    request<ExpensePage>(`/projects/${projectId}/expenses/${query}`, { auth: true }),

  createExpense: (projectId: string | number, payload: ExpenseInput) =>
    request<Expense>(`/projects/${projectId}/expenses/`, {
      method: "POST",
      auth: true,
      body: payload,
    }),

  updateExpense: (id: number, payload: Partial<ExpenseInput>) =>
    request<Expense>(`/expenses/${id}/`, { method: "PATCH", auth: true, body: payload }),

  transition: (id: number, action: ExpenseAction, comment = "", overrideReason = "") =>
    request<Expense>(`/expenses/${id}/transition/`, {
      method: "POST",
      auth: true,
      body: { action, comment, override_reason: overrideReason },
    }),

  payments: (expenseId: number) =>
    request<{
      count: number;
      results: Payment[];
      totals: { amount: number; paid: number; outstanding: number };
      methods: { value: PaymentMethod; label: string }[];
    }>(`/expenses/${expenseId}/payments/`, { auth: true }),

  recordPayment: (expenseId: number, payload: PaymentInput) =>
    request<{ payment: Payment; expense: Expense }>(`/expenses/${expenseId}/payments/`, {
      method: "POST",
      auth: true,
      body: payload,
    }),

  cancelPayment: (id: number, reason = "") =>
    request<Payment>(`/payments/${id}/cancel/`, {
      method: "POST",
      auth: true,
      body: { reason },
    }),

  uploadReceipt: (expenseId: number, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<Expense>(`/expenses/${expenseId}/receipt/`, {
      method: "POST",
      auth: true,
      body,
    });
  },

  transactions: (projectId: string | number, query = "") =>
    request<TransactionPage>(`/projects/${projectId}/transactions/${query}`, { auth: true }),

  adjust: (projectId: string | number, amount: number, direction: TransactionDirection, reason: string) =>
    request<FinancialTransaction>(`/projects/${projectId}/adjustments/`, {
      method: "POST",
      auth: true,
      body: { amount, direction, reason },
    }),
};

/** Ouvre un justificatif : l'API est protégée, on récupère donc le fichier avec le jeton. */
export async function openReceipt(expenseId: number): Promise<string> {
  const response = await fetch(`/api/expenses/${expenseId}/receipt/`, {
    headers: tokens.access ? { Authorization: `Bearer ${tokens.access}` } : {},
  });
  if (!response.ok) {
    throw new ApiError(
      response.status === 404 ? "receipt_not_available" : "server_error",
      response.status === 404
        ? "Aucun justificatif pour cette dépense."
        : "Le justificatif n'a pas pu être ouvert.",
      response.status,
    );
  }
  return URL.createObjectURL(await response.blob());
}
