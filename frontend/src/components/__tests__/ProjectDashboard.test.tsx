/** MVP-011 — le dashboard affiche ce que le serveur calcule, et gère tous les états. */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProjectDashboard from "../ProjectDashboard";
import { dashboard, errorResponse, jsonResponse } from "./fixtures";

function renderDashboard() {
  return render(
    <MemoryRouter>
      <ProjectDashboard projectId={12} />
    </MemoryRouter>,
  );
}

function mockFetch(handler: (url: string) => Response | Promise<Response>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => handler(String(input)));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ProjectDashboard", () => {
  it("affiche avancement, budget, alertes, jalons, dépenses et activité venant du backend", async () => {
    const fetchMock = mockFetch((url) =>
      url.includes("/dashboard/") ? jsonResponse(dashboard()) : errorResponse("not_found", 404),
    );
    renderDashboard();

    expect(screen.getByTestId("dashboard-loading")).toBeInTheDocument();
    const panel = await screen.findByTestId("project-dashboard");

    expect(within(panel).getByText("46,7 %")).toBeInTheDocument();
    expect(within(panel).getByText("1/3 jalons · 1/3 tâches")).toBeInTheDocument();
    expect(within(panel).getByTestId("late-count")).toHaveTextContent("2");
    expect(within(panel).getByText("Fondations")).toBeInTheDocument();
    expect(within(panel).getByText("Toiture")).toBeInTheDocument();
    // Montants formatés en FCFA entiers, sans recalcul côté client.
    const budget = within(panel).getByTestId("dashboard-budget");
    expect(budget).toHaveTextContent("Seuil de 80 % atteint");
    expect(budget).toHaveTextContent(/9\s000\s000\sFCFA/);
    expect(within(budget).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "82");
    // Alertes : ordre du serveur, avec gravité en clair et compteur des alertes masquées.
    const alerts = within(panel).getByTestId("dashboard-alerts");
    expect(alerts).toHaveTextContent("Critique");
    expect(alerts).toHaveTextContent("Jalon « Élévation R+1 » en retard de 9 jour(s).");
    expect(alerts).toHaveTextContent("+ 2 autre(s) alerte(s)");
    expect(within(panel).getByTestId("dashboard-expenses")).toHaveTextContent("Achat ciment");
    expect(within(panel).getByTestId("dashboard-activity")).toHaveTextContent("Dépense soumise");
    expect(within(panel).getByTestId("evidence-counts")).toHaveTextContent("en attente depuis plus de 72 h");
    // Une seule requête agrégée : pas de cascade d'appels.
    const dashboardCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes("/dashboard/"));
    expect(dashboardCalls).toHaveLength(1);
  });

  it("masque budget, dépenses et journal quand le rôle n'y a pas droit", async () => {
    mockFetch(() =>
      jsonResponse(dashboard({ audience: "field", budget: null, expenses: null, activity: null, alerts: [], alerts_total: 0 })),
    );
    renderDashboard();

    await screen.findByTestId("project-dashboard");
    expect(screen.queryByTestId("dashboard-budget")).not.toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-expenses")).not.toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-activity")).not.toBeInTheDocument();
    expect(screen.getByText("Vue terrain", { exact: false })).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-no-alert")).toBeInTheDocument();
  });

  it("explique le projet vide au lieu d'afficher des zéros muets", async () => {
    mockFetch(() =>
      jsonResponse(
        dashboard({
          progress: { progress: 0, milestones_total: 0, milestones_done: 0, milestones_late: 0, tasks_total: 0, tasks_done: 0, tasks_late: 0 },
          milestones: { last_completed: null, next: null },
          alerts: [{ code: "NO_PLANNING", severity: "info", message: "Aucun planning défini." }],
          alerts_total: 1,
          evidences: { counts: { pending: 0, validated: 0, rejected: 0, flagged: 0, stale: 0 }, latest: [] },
        }),
      ),
    );
    renderDashboard();

    await screen.findByTestId("project-dashboard");
    expect(screen.getByText(/Aucun jalon ni tâche n'est planifié/)).toBeInTheDocument();
    expect(screen.getByText("Aucune preuve déposée pour le moment.")).toBeInTheDocument();
  });

  it("affiche l'erreur serveur et permet de réessayer", async () => {
    let calls = 0;
    mockFetch((url) => {
      if (!url.includes("/dashboard/")) return errorResponse("not_found", 404);
      calls += 1;
      return calls === 1 ? errorResponse("server_error", 500, "Panne serveur") : jsonResponse(dashboard());
    });
    renderDashboard();

    const failure = await screen.findByTestId("dashboard-error");
    expect(failure).toHaveTextContent("Panne serveur");
    await userEvent.click(within(failure).getByRole("button", { name: "Réessayer" }));

    expect(await screen.findByTestId("project-dashboard")).toBeInTheDocument();
    expect(calls).toBe(2);
  });

  it("signale le refus d'accès en clair", async () => {
    mockFetch(() => errorResponse("permission_denied", 403));
    renderDashboard();
    expect(await screen.findByTestId("dashboard-error")).toHaveTextContent(
      "Votre rôle ne permet pas de consulter cette information.",
    );
  });

  it("hors ligne : conserve les dernières données et l'indique clairement", async () => {
    let online = true;
    mockFetch(() => {
      if (!online) throw new TypeError("Failed to fetch");
      return jsonResponse(dashboard());
    });
    renderDashboard();
    await screen.findByTestId("project-dashboard");

    online = false;
    await userEvent.click(screen.getByRole("button", { name: "Actualiser" }));

    await waitFor(() => expect(screen.getByText(/Pas de connexion/)).toBeInTheDocument());
    expect(screen.getByText(/Les chiffres affichés datent de/)).toBeInTheDocument();
    expect(screen.getByText("46,7 %")).toBeInTheDocument(); // les données restent visibles
    expect(screen.queryByTestId("dashboard-error")).not.toBeInTheDocument();
  });

  it("n'interroge jamais le serveur en boucle (pas de polling)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = mockFetch((url) =>
      url.includes("/dashboard/") ? jsonResponse(dashboard()) : errorResponse("not_found", 404),
    );
    renderDashboard();
    await screen.findByTestId("project-dashboard");

    await vi.advanceTimersByTimeAsync(10 * 60_000);

    const dashboardCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes("/dashboard/"));
    expect(dashboardCalls).toHaveLength(1);
    vi.useRealTimers();
  });
});
