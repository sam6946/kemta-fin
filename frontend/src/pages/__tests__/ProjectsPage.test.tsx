/**
 * Liste des projets : rendu des données réelles renvoyées par l'API, filtrage par statut,
 * contrôle d'accès à la création et charge utile FCFA envoyée au backend.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../auth/AuthContext";
import ProjectsPage from "../ProjectsPage";

const USER = {
  id: 7,
  phone: "+237691000002",
  phone_masked: "+23769100 •• 02",
  email: null,
  first_name: "Serge",
  last_name: "Kamdem",
  role: "ORG_OWNER",
  role_label: "Promoteur / propriétaire d'organisation",
  is_phone_verified: true,
  capabilities: ["create_project", "create_organization", "manage_members"],
};

const ORGANIZATION = {
  id: 3,
  name: "KEMTA Promotion Douala",
  slug: "kemta-promotion-douala",
  type: "PROMOTER",
  type_label: "Promoteur immobilier",
  country: "CM",
  city: "Douala",
  address: "",
  contact_phone: "",
  owner: USER,
  is_active: true,
  project_count: 1,
  member_count: 2,
  created_at: "2026-01-01T08:00:00Z",
  updated_at: "2026-01-01T08:00:00Z",
};

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
  budget_total: 85000000,
  status: "ACTIVE" as const,
  status_label: "En cours",
  progress: "0.00",
  planned_start_date: "2026-01-12",
  planned_end_date: "2026-12-18",
  actual_start_date: null,
  actual_end_date: null,
  created_by: USER,
  member_count: 7,
  permissions: {
    edit_project: true,
    archive_project: true,
    manage_members: true,
    capture_evidence: false,
    validate_evidence: false,
    view_finance: true,
    manage_finance: true,
  },
  created_at: "2026-01-01T08:00:00Z",
  updated_at: "2026-01-01T08:00:00Z",
};

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockApi(overrides: Partial<Record<string, (init?: RequestInit) => Response>> = {}) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push({ url, init });

    if (overrides[url]) return overrides[url]!(init);
    if (url.startsWith("/api/auth/me/")) return jsonResponse(USER);
    if (url.startsWith("/api/projects/") && (!init || init.method === undefined || init.method === "GET")) {
      return jsonResponse({ count: 1, next: null, previous: null, results: [PROJECT] });
    }
    if (url.startsWith("/api/projects/") && init?.method === "POST") {
      return jsonResponse(PROJECT, 201);
    }
    if (url.startsWith("/api/organizations/")) {
      return jsonResponse({ count: 1, next: null, previous: null, results: [ORGANIZATION] });
    }
    if (url.startsWith("/api/meta/roles/")) {
      return jsonResponse({
        results: [
          { code: "PROJECT_OWNER", label: "Maître d'ouvrage", capabilities: [] },
          { code: "ENGINEER", label: "Ingénieur / bureau d'études", capabilities: [] },
        ],
      });
    }
    throw new Error(`Requête inattendue : ${url}`);
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return { fetchMock, calls };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <ProjectsPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("ProjectsPage", () => {
  it("affiche les projets renvoyés par l'API avec leur budget en FCFA", async () => {
    mockApi();
    sessionStorage.setItem("kemta.access", "token");
    renderPage();

    expect(await screen.findByTestId("project-card")).toBeInTheDocument();
    expect(screen.getByText(/Résidence Bonamoussadi/)).toBeInTheDocument();
    expect(screen.getByText(/85 000 000 FCFA/)).toBeInTheDocument();
    expect(screen.getByText(/7 membre/)).toBeInTheDocument();
  });

  it("n'affiche pas de formulaire de création pour un rôle sans capacité", async () => {
    mockApi({
      "/api/auth/me/": () =>
        jsonResponse({ ...USER, role: "FIELD_AGENT", capabilities: ["capture_evidence"] }),
    });
    sessionStorage.setItem("kemta.access", "token");
    renderPage();

    await screen.findByTestId("project-card");
    expect(screen.queryByRole("button", { name: /créer le projet/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Budget prévu (FCFA)")).not.toBeInTheDocument();
    expect(screen.getByText(/Votre rôle ne permet pas de créer un projet/i)).toBeInTheDocument();
  });

  it("refuse une saisie de budget avec centimes avant tout appel réseau", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi();
    sessionStorage.setItem("kemta.access", "token");
    renderPage();

    await screen.findByTestId("project-card");
    await user.selectOptions(screen.getByLabelText("Organisation"), "3");
    await user.type(screen.getByLabelText("Nom du projet"), "Entrepôt Bonabéri");
    await user.type(screen.getByLabelText("Budget prévu (FCFA)"), "12000000,50");
    await user.click(screen.getByRole("button", { name: /créer le projet/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/entier en FCFA/i);
    expect(calls.some((call) => call.init?.method === "POST")).toBe(false);
  });

  it("envoie un montant entier et le statut choisi au backend", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi();
    sessionStorage.setItem("kemta.access", "token");
    renderPage();

    await screen.findByTestId("project-card");
    await user.selectOptions(screen.getByLabelText("Organisation"), "3");
    await user.type(screen.getByLabelText("Nom du projet"), "Entrepôt Bonabéri");
    await user.type(screen.getByLabelText("Budget prévu (FCFA)"), "12000000");
    await user.selectOptions(screen.getByLabelText("Statut"), "ACTIVE");
    await user.click(screen.getByRole("button", { name: /créer le projet/i }));

    await waitFor(() => {
      const post = calls.find((call) => call.init?.method === "POST");
      expect(post).toBeDefined();
    });
    const post = calls.find((call) => call.init?.method === "POST")!;
    const payload = JSON.parse(String(post.init?.body));
    expect(payload).toMatchObject({
      organization: 3,
      name: "Entrepôt Bonabéri",
      budget_total: 12000000,
      status: "ACTIVE",
    });
    expect(Number.isInteger(payload.budget_total)).toBe(true);
  });

  it("affiche l'état vide quand aucun projet n'est accessible", async () => {
    mockApi({
      "/api/projects/": () => jsonResponse({ count: 0, next: null, previous: null, results: [] }),
      "/api/organizations/": () =>
        jsonResponse({ count: 0, next: null, previous: null, results: [] }),
    });
    sessionStorage.setItem("kemta.access", "token");
    renderPage();

    expect(await screen.findByText(/Aucun projet pour le moment/i)).toBeInTheDocument();
  });
});
