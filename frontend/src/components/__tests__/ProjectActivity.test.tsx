import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProjectActivity from "../ProjectActivity";
import { errorResponse, jsonResponse } from "./fixtures";

const EVENT = {
  id: 1,
  action: "EXPENSE_APPROVED",
  action_label: "Dépense approuvée",
  group: "finance",
  actor: { id: 6, name: "Test Finance", role: "FINANCE" },
  entity_type: "Expense",
  entity_id: "3",
  project: 12,
  metadata: {},
  created_at: "2026-09-28T09:00:00Z",
};

afterEach(() => vi.unstubAllGlobals());

describe("ProjectActivity", () => {
  it("affiche qui / quoi / quand, filtre par famille et n'offre aucune écriture", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        urls.push(`${init?.method ?? "GET"} ${url}`);
        if (url.includes("/activity/meta/"))
          return jsonResponse({ groups: [{ code: "finance", label: "Finances", actions: [] }] });
        return jsonResponse({ count: 1, next: null, previous: null, results: [EVENT] });
      }),
    );
    render(<ProjectActivity projectId={12} />);

    const list = await screen.findByTestId("activity-list");
    expect(list).toHaveTextContent("Dépense approuvée");
    expect(list).toHaveTextContent("Test Finance");
    expect(list).toHaveTextContent("Expense #3");

    await userEvent.selectOptions(screen.getByLabelText(/Famille d'événements/), "finance");

    await waitFor(() => expect(urls.some((entry) => entry.includes("group=finance"))).toBe(true));
    expect(urls.every((entry) => entry.startsWith("GET "))).toBe(true);
    expect(screen.queryByRole("button", { name: /supprimer|modifier/i })).not.toBeInTheDocument();
  });

  it("gère le refus d'accès et l'état vide", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).includes("/meta/")
          ? jsonResponse({ groups: [] })
          : errorResponse("permission_denied", 403, "Accès refusé"),
      ),
    );
    const { unmount } = render(<ProjectActivity projectId={12} />);
    expect(await screen.findByText(/Votre rôle ne permet pas/)).toBeInTheDocument();
    unmount();

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).includes("/meta/")
          ? jsonResponse({ groups: [] })
          : jsonResponse({ count: 0, next: null, previous: null, results: [] }),
      ),
    );
    render(<ProjectActivity projectId={12} />);
    expect(await screen.findByTestId("activity-empty")).toBeInTheDocument();
  });
});
