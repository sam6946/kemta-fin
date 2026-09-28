import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import Workspace from "../Workspace";
import { errorResponse, jsonResponse, workspace } from "./fixtures";

function renderWorkspace() {
  return render(
    <MemoryRouter>
      <Workspace />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Workspace", () => {
  it("liste les projets urgents, mes tâches et les files de décision", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
      String(input) === "/api/workspace/" ? jsonResponse(workspace()) : errorResponse("not_found", 404),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWorkspace();

    const panel = await screen.findByTestId("workspace");
    expect(within(panel).getByTestId("ws-late")).toHaveTextContent("2");
    expect(within(panel).getByTestId("ws-to-validate")).toHaveTextContent("1");
    expect(within(panel).getByTestId("ws-to-approve")).toHaveTextContent("1");
    expect(within(panel).getByTestId("workspace-projects")).toHaveTextContent("2 en retard");
    expect(within(panel).getByTestId("workspace-tasks")).toHaveTextContent("20 j de retard");
    expect(within(panel).getByTestId("workspace-approve")).toHaveTextContent(/1\s500\s000\sFCFA/);
    // Un seul appel pour tout l'espace de travail.
    const workspaceCalls = fetchMock.mock.calls.filter(([url]) => String(url) === "/api/workspace/");
    expect(workspaceCalls).toHaveLength(1);
  });

  it("investisseur : aucune file d'action ni tâche", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          workspace({
            profile: "investor",
            my_tasks: [],
            to_validate: { count: 0, items: [] },
            to_approve: { count: 0, items: [] },
            totals: { projects: 1, tasks_late: 0, milestones_late: 0, evidences_to_validate: 0, expenses_to_approve: 0, my_open_tasks: 0 },
          }),
        ),
      ),
    );
    renderWorkspace();

    await screen.findByTestId("workspace");
    expect(screen.queryByTestId("ws-to-validate")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ws-to-approve")).not.toBeInTheDocument();
    expect(screen.queryByText("Mes tâches ouvertes")).not.toBeInTheDocument();
    expect(screen.getByText(/Suivi investisseur/)).toBeInTheDocument();
  });

  it("sans projet : message d'aide clair", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          workspace({
            profile: "none",
            projects: [],
            my_tasks: [],
            to_validate: { count: 0, items: [] },
            to_approve: { count: 0, items: [] },
            totals: { projects: 0, tasks_late: 0, milestones_late: 0, evidences_to_validate: 0, expenses_to_approve: 0, my_open_tasks: 0 },
          }),
        ),
      ),
    );
    renderWorkspace();
    expect(await screen.findByTestId("workspace-empty")).toHaveTextContent("membre d'aucun projet");
  });

  it("gère l'erreur et le hors ligne", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => errorResponse("server_error", 500, "Indisponible")));
    renderWorkspace();
    expect(await screen.findByTestId("workspace-error")).toHaveTextContent("Indisponible");
  });

  it("hors ligne dès l'ouverture : message réseau explicite", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    renderWorkspace();
    expect(await screen.findByTestId("workspace-error")).toHaveTextContent("Pas de connexion");
  });

  it("signale que la liste est tronquée", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(workspace({ projects_truncated: true }))));
    renderWorkspace();
    expect(await screen.findByText(/Seuls les projets les plus urgents/)).toBeInTheDocument();
  });
});
