/**
 * Finances du chantier (MVP-010) : l'écran n'invente aucun chiffre et n'autorise aucune action
 * que le backend refuserait.
 *
 * Ce que ces tests verrouillent :
 * - les totaux affichés (engagé, payé, solde, taux) viennent des réponses serveur ;
 * - aucune requête n'envoie de total calculé côté client ;
 * - les actions dépendent des `permissions` de chaque dépense (masquées sinon) ;
 * - un dépassement de budget remonte le motif d'overrun et propose le champ « motif de
 *   dépassement » exigé par le serveur ;
 * - hors ligne, l'écran dit clairement que les opérations financières exigent le serveur.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ProjectFinance from "../ProjectFinance";

const PROJECT = {
  id: 12,
  organization: 3,
  organization_name: "KEMTA Promotion Douala",
  name: "Résidence Bonamoussadi — tranche 1",
  code: "RBS-T1",
  description: "",
  location_label: "Bonamoussadi",
  city: "Douala",
  region: "Littoral",
  latitude: null,
  longitude: null,
  geofence_radius_m: 500,
  currency: "XAF" as const,
  budget_total: 10_000_000,
  status: "ACTIVE" as const,
  status_label: "En cours",
  progress: "60.00",
  planned_start_date: "2026-01-12",
  planned_end_date: "2026-12-18",
  actual_start_date: null,
  actual_end_date: null,
  created_by: { id: 7, first_name: "Arnaud", last_name: "Nkoulou" } as never,
  member_count: 6,
  permissions: {
    edit_project: true,
    archive_project: true,
    manage_members: true,
    manage_schedule: true,
    update_task: true,
    capture_evidence: true,
    validate_evidence: true,
    view_finance: true,
    manage_finance: true,
    view_activity: true,
  },
  created_at: "2026-01-01T08:00:00Z",
  updated_at: "2026-01-01T08:00:00Z",
};

const BUDGET_LINE = {
  id: 5,
  project: 12,
  label: "Matériaux — ciment et fer",
  category: "MATERIALS" as const,
  category_label: "Matériaux",
  planned_amount: 4_000_000,
  committed_amount: 1_200_000,
  expense_count: 1,
  order: 1,
  notes: "",
  permissions: { manage_finance: true, create_expense: true },
  created_at: "2026-01-02T08:00:00Z",
  updated_at: "2026-01-02T08:00:00Z",
};

function summary(overrides: Record<string, unknown> = {}) {
  return {
    planned: 10_000_000,
    allocated: 4_000_000,
    unallocated: 6_000_000,
    committed: 1_200_000,
    paid: 200_000,
    outstanding: 1_000_000,
    balance: 8_800_000,
    consumption_rate: "12.00",
    threshold: "OK" as const,
    currency: "XAF",
    lines: [
      {
        budget_line: 5,
        label: "Matériaux — ciment et fer",
        category: "MATERIALS" as const,
        planned: 4_000_000,
        committed: 1_200_000,
      },
    ],
    alerts: [] as unknown[],
    ...overrides,
  };
}

function expense(overrides: Record<string, unknown> = {}) {
  return {
    id: 41,
    project: 12,
    budget_line: 5,
    budget_line_label: "Matériaux — ciment et fer",
    title: "Achat 400 sacs de ciment",
    description: "",
    amount: 1_200_000,
    currency: "XAF",
    incurred_on: "2026-02-10",
    status: "SUBMITTED" as const,
    status_label: "Soumise à validation",
    supplier: "CIMENCAM Douala",
    invoice_number: "FAC-2026-0142",
    invoice_date: null,
    receipt_url: null,
    receipt_hash: "",
    paid_amount: 0,
    outstanding_amount: 1_200_000,
    payments: [] as unknown[],
    is_editable: true,
    created_by: { id: 8, first_name: "Franck", last_name: "Biya", phone_masked: "", role_label: "Gestionnaire financier" },
    approved_by: null,
    approved_at: null,
    cancelled_at: null,
    permissions: {
      view_finance: true,
      manage_finance: true,
      settle_finance: true,
      can_approve: true,
      can_pay: true,
      can_edit: true,
      can_cancel: true,
    },
    created_at: "2026-02-10T08:00:00Z",
    updated_at: "2026-02-10T08:00:00Z",
    ...overrides,
  };
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type MockOptions = {
  expenses?: unknown[];
  alerts?: unknown[];
  budgetThreshold?: "OK" | "WARNING" | "EXCEEDED";
  permissions?: Partial<{
    view_finance: boolean;
    manage_finance: boolean;
    settle_finance: boolean;
    can_approve: boolean;
    can_pay: boolean;
    can_edit: boolean;
    can_cancel: boolean;
  }>;
  transitionStatus?: number;
  offline?: boolean;
  ledger?: unknown[];
};

function mockApi(options: MockOptions = {}) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const permissions = {
    view_finance: true,
    manage_finance: true,
    settle_finance: true,
    can_approve: true,
    can_pay: true,
    can_edit: true,
    can_cancel: true,
    ...options.permissions,
  };
  const budget = summary({
    threshold: options.budgetThreshold ?? "OK",
    alerts: options.alerts ?? [],
  });

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push({ url, init });
    if (options.offline) throw new TypeError("Failed to fetch");

    if (url === "/api/projects/12/finance/") {
      const { lines, alerts, ...rest } = budget;
      return jsonResponse({
        project: { id: 12, code: "RBS-T1", name: PROJECT.name, currency: "XAF" },
        budget: rest,
        lines,
        alerts,
        permissions,
        generated_at: "2026-09-26T08:00:00Z",
      });
    }
    if (url === "/api/projects/12/budget-lines/") {
      return jsonResponse({
        count: 1,
        results: [BUDGET_LINE],
        summary: budget,
        categories: [
          { value: "MATERIALS", label: "Matériaux" },
          { value: "LABOUR", label: "Main-d'œuvre" },
        ],
      });
    }
    if (url.startsWith("/api/projects/12/expenses/")) {
      const items = options.expenses ?? [expense()];
      return jsonResponse({
        count: items.length,
        next: null,
        previous: null,
        results: items,
        summary: budget,
        counts: { draft: 0, submitted: items.length, approved: 0, paid: 0, rejected: 0, cancelled: 0 },
      });
    }
    if (url.startsWith("/api/projects/12/transactions/")) {
      return jsonResponse({
        count: (options.ledger ?? []).length,
        next: null,
        previous: null,
        results: options.ledger ?? [],
        totals: { committed: 1_200_000, paid: 200_000, adjustments: 0, balance: 8_800_000 },
      });
    }
    if (url.match(/\/api\/expenses\/\d+\/transition\/$/)) {
      if (options.transitionStatus && options.transitionStatus >= 400) {
        return jsonResponse(
          {
            error: {
              code: "budget_exceeded",
              message:
                "Cette dépense dépasse le budget disponible. Indiquez un motif de dépassement.",
              details: {
                overruns: [{ label: "Budget global", over: 200_000 }],
                min_override_reason_length: 10,
              },
              request_id: "t",
            },
          },
          options.transitionStatus,
        );
      }
      return jsonResponse(
        expense({ status: "APPROVED", status_label: "Approuvée", permissions: { ...permissions } }),
      );
    }
    if (url.match(/\/api\/expenses\/\d+\/payments\/$/) && init?.method === "POST") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      return jsonResponse(
        {
          payment: {
            id: 9,
            expense: 41,
            amount: body.amount,
            paid_on: body.paid_on,
            method: body.method,
            method_label: "Virement bancaire",
            reference: body.reference,
            note: "",
            created_by: null,
            cancelled_at: null,
            cancelled_by: null,
            is_cancelled: false,
            created_at: "2026-09-26T08:00:00Z",
          },
          expense: expense({ paid_amount: body.amount, outstanding_amount: 1_200_000 - body.amount }),
        },
        201,
      );
    }
    if (url === "/api/projects/12/budget-lines/" && init?.method === "POST") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      return jsonResponse({ ...BUDGET_LINE, id: 6, label: body.label, planned_amount: body.planned_amount }, 201);
    }
    throw new Error(`Requête inattendue : ${url} ${init?.method ?? "GET"}`);
  });

  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return { calls, permissions };
}

/** L'onglet « Dépenses » n'est pas celui par défaut : on l'ouvre explicitement. */
async function openExpensesTab(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("tab", { name: /dépenses/i }));
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ProjectFinance project={PROJECT as never} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  Object.defineProperty(globalThis.navigator, "onLine", { value: true, configurable: true });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ProjectFinance", () => {
  it("affiche les totaux calculés par le serveur", async () => {
    mockApi();
    renderPage();

    const section = await screen.findByTestId("project-finance");
    expect(within(section).getByTestId("metric-committed")).toHaveTextContent("1 200 000 FCFA");
    expect(within(section).getByTestId("metric-paid")).toHaveTextContent("200 000 FCFA");
    expect(within(section).getByTestId("metric-balance")).toHaveTextContent("8 800 000 FCFA");
    expect(within(section).getByText(/12 %/)).toBeInTheDocument();
  });

  it("affiche le consommé de chaque poste budgétaire (valeur serveur)", async () => {
    mockApi();
    renderPage();

    const row = await screen.findByTestId("budget-line");
    expect(within(row).getByText("Matériaux — ciment et fer")).toBeInTheDocument();
    expect(within(row).getByTestId("line-planned")).toHaveTextContent("4 000 000 FCFA");
    expect(within(row).getByTestId("line-committed")).toHaveTextContent("1 200 000 FCFA");
  });

  it("crée un poste budgétaire sans jamais envoyer de total", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi();
    renderPage();

    const form = await screen.findByTestId("budget-line-form");
    await user.type(within(form).getByLabelText(/libellé/i), "Sous-traitance électricité");
    await user.type(within(form).getByLabelText(/montant prévu/i), "1500000");
    await user.click(within(form).getByRole("button", { name: /ajouter le poste/i }));

    await waitFor(() => {
      expect(calls.some((call) => call.url === "/api/projects/12/budget-lines/" && call.init?.method === "POST")).toBe(true);
    });
    const post = calls.find((call) => call.url === "/api/projects/12/budget-lines/" && call.init?.method === "POST")!;
    const payload = JSON.parse(String(post.init?.body));
    expect(payload).toEqual({
      label: "Sous-traitance électricité",
      planned_amount: 1_500_000,
      category: "MATERIALS",
    });
    // Aucun total calculé n'est transmis (ni engagé, ni payé, ni solde).
    expect(Object.keys(payload)).not.toContain("committed_amount");
    expect(Object.keys(payload)).not.toContain("balance");
  });

  it("approuve une dépense soumise et remonte le motif exigé en cas de dépassement", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi({ transitionStatus: 422 });
    renderPage();

    await screen.findByTestId("project-finance");
    await openExpensesTab(user);
    await screen.findByTestId("expense-row");
    await user.click(screen.getByRole("button", { name: /^approuver$/i }));

    // Le refus du serveur est affiché avec le détail du dépassement…
    const feedback = await screen.findByTestId("finance-feedback");
    await waitFor(() => expect(within(feedback).getByRole("alert")).toHaveTextContent(/Dépassement/));
    expect(within(feedback).getByRole("alert")).toHaveTextContent(/200 000 FCFA/);

    // … et le champ « motif de dépassement » permet de réessayer avec un motif explicite.
    const reason = screen.getByLabelText(/motif de dépassement de Achat 400 sacs de ciment/i);
    await user.type(reason, "Avenant validé par le comité");
    await user.click(screen.getByRole("button", { name: /^approuver$/i }));

    await waitFor(() => {
      const posts = calls.filter((call) => call.url === "/api/expenses/41/transition/");
      expect(posts.length).toBe(2);
    });
    const posts = calls.filter((call) => call.url === "/api/expenses/41/transition/");
    expect(JSON.parse(String(posts[1].init?.body))).toMatchObject({
      action: "APPROVE",
      override_reason: "Avenant validé par le comité",
    });
  });

  it("enregistre un paiement dans la limite du reste dû", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi({
      expenses: [
        expense({
          status: "APPROVED",
          status_label: "Approuvée",
          paid_amount: 200_000,
          outstanding_amount: 1_000_000,
        }),
      ],
    });
    renderPage();

    await screen.findByTestId("project-finance");
    await openExpensesTab(user);
    await screen.findByTestId("expense-row");
    const field = screen.getByLabelText(/montant du paiement pour Achat 400 sacs de ciment/i);

    // Trop-perçu : refusé avant tout appel réseau, avec le reste dû rappelé.
    await user.type(field, "1200000");
    await user.selectOptions(
      screen.getByLabelText(/moyen de paiement pour Achat 400 sacs de ciment/i),
      "BANK_TRANSFER",
    );
    await user.type(
      screen.getByLabelText(/référence du paiement pour Achat 400 sacs de ciment/i),
      "VIR-2026-0142",
    );
    await user.click(screen.getByRole("button", { name: /enregistrer le paiement/i }));
    expect(screen.getByText(/dépasse le reste dû/i)).toBeInTheDocument();
    expect(calls.some((call) => call.url === "/api/expenses/41/payments/")).toBe(false);

    await user.clear(field);
    await user.type(field, "1000000");
    await user.click(screen.getByRole("button", { name: /enregistrer le paiement/i }));

    await waitFor(() => {
      expect(calls.some((call) => call.url === "/api/expenses/41/payments/" && call.init?.method === "POST")).toBe(true);
    });
    const post = calls.find((call) => call.url === "/api/expenses/41/payments/")!;
    const payload = JSON.parse(String(post.init?.body));
    expect(payload).toMatchObject({ amount: 1_000_000, method: "BANK_TRANSFER", reference: "VIR-2026-0142" });
    expect(payload).not.toHaveProperty("outstanding");
  });

  it("reste en lecture seule sans droit de pilotage financier", async () => {
    const user = userEvent.setup();
    mockApi({
      permissions: { manage_finance: false, settle_finance: false, can_approve: false, can_pay: false, can_edit: false, can_cancel: false },
      expenses: [expense({ permissions: { view_finance: true, manage_finance: false, settle_finance: false, can_approve: false, can_pay: false, can_edit: false, can_cancel: false } })],
    });
    renderPage();

    // Le panneau budgétaire (par défaut) rappelle la lecture seule…
    await screen.findByTestId("budget-line");
    expect(screen.queryByTestId("budget-line-form")).not.toBeInTheDocument();
    expect(screen.getAllByText(/lecture seule/i).length).toBeGreaterThan(0);

    // … et l'onglet des dépenses n'expose ni création, ni approbation, ni paiement.
    await openExpensesTab(user);
    await screen.findByTestId("expense-row");
    expect(screen.queryByTestId("expense-form")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^approuver$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /enregistrer le paiement/i })).not.toBeInTheDocument();
  });

  it("avertit explicitement quand le réseau manque (opérations financières en ligne uniquement)", async () => {
    mockApi({ offline: true });
    renderPage();

    const feedback = await screen.findByTestId("finance-feedback");
    expect(within(feedback).getByRole("alert")).toHaveTextContent(/Pas de connexion/i);
    expect(within(feedback).getByRole("alert")).toHaveTextContent(/exigent le serveur/i);
  });

  it("affiche les alertes de seuil et le grand livre avec le solde après opération", async () => {
    mockApi({
      budgetThreshold: "WARNING",
      alerts: [
        {
          code: "BUDGET_THRESHOLD_REACHED",
          severity: "warning",
          message: "Seuil de 80 % du budget atteint (81.38 %).",
          amount: 2_700_000,
        },
      ],
      ledger: [
        {
          id: 1,
          project: 12,
          type: "EXPENSE",
          type_label: "Dépense engagée",
          direction: "DEBIT",
          direction_label: "Débit (consomme le budget)",
          amount: 1_200_000,
          balance_after: 8_800_000,
          expense: 41,
          expense_title: "Achat 400 sacs de ciment",
          payment: null,
          budget_line: 5,
          budget_line_label: "Matériaux — ciment et fer",
          note: "Approbation de « Achat 400 sacs de ciment »",
          created_by: { id: 7, first_name: "Arnaud", last_name: "Nkoulou", phone_masked: "", role_label: "Maître d'ouvrage" },
          created_at: "2026-02-11T09:00:00Z",
        },
      ],
    });
    const user = userEvent.setup();
    renderPage();

    const alerts = await screen.findByTestId("finance-alerts");
    expect(within(alerts).getByText(/Seuil de 80 % du budget atteint/)).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /grand livre/i }));
    const row = await screen.findByTestId("ledger-row");
    expect(within(row).getByText(/Dépense engagée/)).toBeInTheDocument();
    expect(within(row).getByText(/solde 8 800 000 FCFA/)).toBeInTheDocument();
    expect(within(row).getByText(/Approbation de/)).toBeInTheDocument();
    expect(within(screen.getByTestId("ledger-totals")).getByText("1 200 000 FCFA")).toBeInTheDocument();
  });
});
