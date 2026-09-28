/**
 * Détail projet : lecture des membres, ajout contrôlé par les permissions backend,
 * et absence de toute action non autorisée (le frontend masque, le backend décide).
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../auth/AuthContext";
import { SyncProvider } from "../../sync/SyncProvider";
import ProjectDetailPage from "../ProjectDetailPage";

const OWNER = {
  id: 7,
  phone: "+237691000003",
  phone_masked: "+23769100 •• 03",
  email: null,
  first_name: "Arnaud",
  last_name: "Nkoulou",
  role: "PROJECT_OWNER",
  role_label: "Maître d'ouvrage",
  is_phone_verified: true,
  capabilities: ["edit_project", "manage_members", "view_finance"],
};

const ENGINEER = {
  id: 9,
  phone: "+237691000004",
  phone_masked: "+23769100 •• 04",
  email: null,
  first_name: "Bertrand",
  last_name: "Fotso",
  role: "ENGINEER",
  role_label: "Ingénieur / bureau d'études",
  is_phone_verified: true,
  capabilities: ["manage_schedule", "capture_evidence"],
};

function project(manageMembers: boolean) {
  return {
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
    currency: "XAF",
    budget_total: 85000000,
    status: "ACTIVE",
    status_label: "En cours",
    progress: "0.00",
    planned_start_date: "2026-01-12",
    planned_end_date: "2026-12-18",
    actual_start_date: null,
    actual_end_date: null,
    created_by: OWNER,
    member_count: 2,
    permissions: {
      edit_project: manageMembers,
      archive_project: manageMembers,
      manage_members: manageMembers,
      manage_schedule: manageMembers,
      update_task: manageMembers,
      capture_evidence: true,
      validate_evidence: false,
      view_finance: true,
      manage_finance: manageMembers,
      view_activity: true,
    },
    created_at: "2026-01-01T08:00:00Z",
    updated_at: "2026-01-01T08:00:00Z",
  };
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockApi({
  manager = true,
  addStatus = 201,
}: { manager?: boolean; addStatus?: number } = {}) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const currentUser = manager ? OWNER : ENGINEER;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push({ url, init });

    if (url.startsWith("/api/auth/me/")) return jsonResponse(currentUser);
    if (url.match(/\/api\/projects\/\d+\/members\/$/) && init?.method === "POST") {
      if (addStatus !== 201) {
        return jsonResponse(
          {
            error: {
              code: "user_not_found",
              message: "Aucun compte KEMTA ne correspond à ce numéro.",
              details: {},
              request_id: "abc",
            },
          },
          addStatus,
        );
      }
      return jsonResponse({ id: 4, user: ENGINEER, role: "ENGINEER", role_label: "Ingénieur", can_validate_evidence: false, can_manage_finance: false, is_active: true, created_at: "" }, 201);
    }
    if (url.match(/\/api\/projects\/\d+\/members\/$/)) {
      return jsonResponse({
        count: 2,
        next: null,
        previous: null,
        results: [
          {
            id: 1,
            user: OWNER,
            role: "PROJECT_OWNER",
            role_label: "Maître d'ouvrage",
            can_validate_evidence: true,
            can_manage_finance: true,
            is_active: true,
            created_at: "",
          },
          {
            id: 2,
            user: ENGINEER,
            role: "ENGINEER",
            role_label: "Ingénieur / bureau d'études",
            can_validate_evidence: false,
            can_manage_finance: false,
            is_active: true,
            created_at: "",
          },
        ],
      });
    }
    if (url.match(/\/api\/projects\/\d+\/$/)) return jsonResponse(project(manager));
    if (url.match(/\/api\/projects\/\d+\/schedule\/$/)) {
      return jsonResponse({
        project: {
          id: 12,
          name: "Résidence Bonamoussadi — tranche 1",
          status: "ACTIVE",
          progress: 62.5,
          planned_start_date: "2026-01-12",
          planned_end_date: "2026-12-18",
        },
        milestones: [
          {
            id: 1,
            project: 12,
            title: "Fondations et soubassement",
            description: "",
            status: "IN_PROGRESS",
            status_label: "En cours",
            planned_date: "2026-04-10",
            actual_date: null,
            order: 0,
            weight: "3.00",
            is_late: true,
            days_late: 12,
            progress: 55,
            task_total: 1,
            task_done: 0,
            tasks: [
              {
                id: 41,
                title: "Coulage des semelles",
                status: "IN_PROGRESS",
                status_label: "En cours",
                progress: "40.00",
                is_late: false,
                days_late: 0,
                planned_start_date: "2026-03-01",
                planned_end_date: "2026-03-20",
                actual_end_date: null,
                assignee: { id: 9, first_name: "Bertrand", last_name: "Fotso", phone_masked: "+23769100 •• 04" },
                depends_on: [],
              },
            ],
            created_at: "",
            updated_at: "",
          },
        ],
        orphan_tasks: [],
        summary: {
          milestones_total: 1,
          milestones_done: 0,
          tasks_total: 1,
          tasks_done: 0,
          tasks_late: 0,
          milestones_late: 1,
          names_late: ["Fondations et soubassement"],
        },
        alerts: [
          { type: "milestone_late", id: 1, title: "Fondations et soubassement", days_late: 12 },
        ],
      });
    }
    if (url.match(/\/api\/projects\/\d+\/tasks\/\d+\/$/) && init?.method === "PATCH") {
      return jsonResponse({ id: 41, project: 12, milestone: 1, title: "Coulage des semelles",
        description: "", status: "DONE", status_label: "Terminée", planned_start_date: "2026-03-01",
        planned_end_date: "2026-03-20", actual_start_date: null, actual_end_date: "2026-09-25",
        progress: "100.00", weight: "1.00", assignee: null, depends_on: [], is_late: false,
        days_late: 0, created_at: "", updated_at: "" });
    }
    if (url.match(/\/api\/projects\/\d+\/milestones\/$/) && init?.method === "POST") {
      return jsonResponse({ id: 2, project: 12, title: "Second œuvre", description: "",
        status: "PLANNED", status_label: "Planifié", planned_date: null, actual_date: null,
        order: 1, weight: "1.00", is_late: false, days_late: 0, progress: 0, task_total: 0,
        task_done: 0, created_at: "", updated_at: "" }, 201);
    }
    // Phase 7 — l'onglet finances du projet charge sa synthèse à l'affichage.
    if (url.match(/\/api\/projects\/\d+\/finance\/$/)) {
      return jsonResponse({
        project: { id: 12, code: "RBS-T1", name: "Résidence Bonamoussadi — tranche 1", currency: "XAF" },
        budget: {
          planned: 85000000,
          allocated: 0,
          unallocated: 85000000,
          committed: 0,
          paid: 0,
          outstanding: 0,
          balance: 85000000,
          consumption_rate: "0.00",
          threshold: "OK",
          currency: "XAF",
        },
        lines: [],
        alerts: [],
        permissions: {
          view_finance: true,
          manage_finance: manager,
          settle_finance: manager,
          can_approve: manager,
          can_pay: manager,
          can_edit: manager,
          can_cancel: manager,
        },
        generated_at: "2026-09-26T08:00:00Z",
      });
    }
    if (url.match(/\/api\/projects\/\d+\/budget-lines\/$/)) {
      return jsonResponse({
        count: 0,
        results: [],
        summary: {
          planned: 85000000,
          allocated: 0,
          unallocated: 85000000,
          committed: 0,
          paid: 0,
          outstanding: 0,
          balance: 85000000,
          consumption_rate: "0.00",
          threshold: "OK",
          currency: "XAF",
          lines: [],
          alerts: [],
        },
        categories: [{ value: "MATERIALS", label: "Matériaux" }],
      });
    }
    if (url.match(/\/api\/projects\/\d+\/expenses\//)) {
      return jsonResponse({
        count: 0,
        next: null,
        previous: null,
        results: [],
        summary: {
          planned: 85000000,
          allocated: 0,
          unallocated: 85000000,
          committed: 0,
          paid: 0,
          outstanding: 0,
          balance: 85000000,
          consumption_rate: "0.00",
          threshold: "OK",
          currency: "XAF",
          lines: [],
          alerts: [],
        },
        counts: { draft: 0, submitted: 0, approved: 0, paid: 0, rejected: 0, cancelled: 0 },
      });
    }
    if (url.match(/\/api\/projects\/\d+\/transactions\//)) {
      return jsonResponse({
        count: 0,
        next: null,
        previous: null,
        results: [],
        totals: { committed: 0, paid: 0, adjustments: 0, balance: 85000000 },
      });
    }
    if (url.startsWith("/api/meta/status/")) {
      return jsonResponse({
        project: [{ value: "ACTIVE", label: "En cours" }],
        milestone: [
          { value: "PLANNED", label: "Planifié" },
          { value: "IN_PROGRESS", label: "En cours" },
          { value: "DONE", label: "Terminé" },
        ],
        task: [
          { value: "TODO", label: "À faire" },
          { value: "IN_PROGRESS", label: "En cours" },
          { value: "DONE", label: "Terminée" },
        ],
      });
    }
    if (url.startsWith("/api/meta/roles/")) {
      return jsonResponse({
        results: [
          { code: "PROJECT_OWNER", label: "Maître d'ouvrage", capabilities: [] },
          { code: "ENGINEER", label: "Ingénieur / bureau d'études", capabilities: [] },
          { code: "FIELD_AGENT", label: "Agent terrain / chef de chantier", capabilities: [] },
        ],
      });
    }
    throw new Error(`Requête inattendue : ${url}`);
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return { calls };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/projets/12"]}>
      <AuthProvider>
        <SyncProvider>
        <Routes>
          <Route path="/projets/:id" element={<ProjectDetailPage />} />
        </Routes>
        </SyncProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("ProjectDetailPage", () => {
  it("affiche les informations du projet et ses membres", async () => {
    mockApi();
    sessionStorage.setItem("kemta.access", "token");
    renderPage();

    expect(await screen.findByText("Résidence Bonamoussadi — tranche 1")).toBeInTheDocument();
    expect(screen.getByText(/85 000 000 FCFA/)).toBeInTheDocument();
    expect(screen.getAllByTestId("member-row")).toHaveLength(2);
    expect(screen.getByText("Bertrand Fotso")).toBeInTheDocument();
  });

  it("ajoute un membre avec le rôle choisi", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi();
    sessionStorage.setItem("kemta.access", "token");
    renderPage();

    await screen.findByText("Résidence Bonamoussadi — tranche 1");
    await user.type(screen.getByLabelText(/numéro de téléphone/i), "+237 6 91 00 00 04");
    await user.selectOptions(screen.getByLabelText(/rôle sur ce projet/i), "FIELD_AGENT");
    await user.click(screen.getByRole("button", { name: /ajouter au projet/i }));

    await waitFor(() => {
      const post = calls.find((call) => call.init?.method === "POST");
      expect(post).toBeDefined();
    });
    const payload = JSON.parse(String(calls.find((call) => call.init?.method === "POST")!.init?.body));
    expect(payload).toMatchObject({ phone: "+237 6 91 00 00 04", role: "FIELD_AGENT" });
  });

  it("explique clairement qu'un numéro inconnu n'a pas de compte", async () => {
    const user = userEvent.setup();
    mockApi({ addStatus: 404 });
    sessionStorage.setItem("kemta.access", "token");
    renderPage();

    await screen.findByText("Résidence Bonamoussadi — tranche 1");
    await user.type(screen.getByLabelText(/numéro de téléphone/i), "+237699000777");
    await user.click(screen.getByRole("button", { name: /ajouter au projet/i }));

    const feedback = await screen.findByTestId("members-feedback");
    expect(await within(feedback).findByRole("alert")).toHaveTextContent(/erreur/i);
  });

  it("masque les actions de gestion pour un rôle non autorisé", async () => {
    mockApi({ manager: false });
    sessionStorage.setItem("kemta.access", "token");
    renderPage();

    await screen.findByText("Résidence Bonamoussadi — tranche 1");
    expect(screen.queryByRole("button", { name: /ajouter au projet/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retirer/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /archiver le projet/i })).not.toBeInTheDocument();
    // Plusieurs blocs (membres, budget) rappellent la lecture seule : on vérifie la présence.
    expect(screen.getAllByText(/lecture seule/i).length).toBeGreaterThan(0);
  });

  it("affiche le planning : jalons, tâches, retards et avancement calculé", async () => {
    mockApi();
    sessionStorage.setItem("kemta.access", "token");
    renderPage();

    await screen.findByText("Résidence Bonamoussadi — tranche 1");
    const planning = await screen.findByTestId("planning");

    expect(within(planning).getAllByText("Fondations et soubassement").length).toBeGreaterThan(0);
    expect(within(planning).getByText("Coulage des semelles")).toBeInTheDocument();
    // L'avancement affiché est celui du serveur (62,5 %), jamais recalculé côté client.
    expect(
      within(planning).getByRole("heading", { name: /avancement calculé/i }),
    ).toHaveTextContent(/62,5/);
    // L'alerte de retard du jalon est affichée (une seule mention dans le bandeau).
    expect(within(planning).getAllByText(/12 j/).length).toBeGreaterThan(0);
  });

  it("permet au responsable désigné de faire avancer une tâche", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi();
    sessionStorage.setItem("kemta.access", "token");
    renderPage();

    const planning = await screen.findByTestId("planning");
    await within(planning).findByText("Coulage des semelles");
    await user.selectOptions(
      within(planning).getByLabelText("Statut de Coulage des semelles"),
      "DONE",
    );

    await waitFor(() => {
      const patch = calls.find(
        (call) =>
          call.url === "/api/tasks/41/" && call.init?.method === "PATCH",
      );
      expect(patch).toBeDefined();
      // La date réelle est exigée par le backend pour une tâche terminée.
      expect(JSON.parse(String(patch!.init!.body))).toMatchObject({ status: "DONE" });
      expect(JSON.parse(String(patch!.init!.body)).actual_end_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    });
  });

  it("n'expose aucune action de planification sans la capacité manage_schedule", async () => {
    mockApi({ manager: false });
    sessionStorage.setItem("kemta.access", "token");
    renderPage();

    const planning = await screen.findByTestId("planning");
    expect(within(planning).queryByRole("button", { name: /créer le jalon/i })).not.toBeInTheDocument();
    expect(within(planning).queryByRole("button", { name: /créer la tâche/i })).not.toBeInTheDocument();
    expect(within(planning).queryByRole("button", { name: /supprimer/i })).not.toBeInTheDocument();
    expect(within(planning).getByText(/consultation seule/i)).toBeInTheDocument();
  });

  it("crée un jalon et recharge le planning", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi();
    sessionStorage.setItem("kemta.access", "token");
    renderPage();

    const planning = await screen.findByTestId("planning");
    await within(planning).findAllByText("Fondations et soubassement");
    await user.type(within(planning).getByLabelText("Titre du jalon"), "Second œuvre");
    await user.click(within(planning).getByRole("button", { name: /créer le jalon/i }));

    await waitFor(() => {
      const post = calls.find(
        (call) => call.url === "/api/projects/12/milestones/" && call.init?.method === "POST",
      );
      expect(post).toBeDefined();
      expect(JSON.parse(String(post!.init!.body))).toMatchObject({ title: "Second œuvre", weight: 1 });
    });
  });
});
