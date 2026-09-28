/** MVP-014 — cloche (sans polling) et centre de notifications. */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import NotificationsPage from "../../pages/NotificationsPage";
import type { AppNotification } from "../../api/notifications";
import NotificationBell, { NOTIFICATIONS_CHANGED } from "../NotificationBell";
import { errorResponse, jsonResponse } from "./fixtures";

function note(id: number, overrides: Partial<AppNotification> = {}): AppNotification {
  return {
    id,
    event_type: "EvidenceRejected",
    title: `Notification ${id}`,
    body: "Projet Bonamoussadi.",
    count: 1,
    project: 12,
    is_read: false,
    read_at: null,
    last_event_at: "2026-09-28T10:00:00Z",
    created_at: "2026-09-28T10:00:00Z",
    items: [],
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("NotificationBell", () => {
  it("affiche la pastille des non lues et se met à jour sur événement, sans polling", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let unread = 3;
    const fetchMock = vi.fn(async () => jsonResponse({ unread_count: unread }));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId("bell-badge")).toHaveTextContent("3");
    expect(screen.getByRole("link", { name: "Notifications : 3 non lue(s)" })).toHaveAttribute("href", "/notifications");

    await vi.advanceTimersByTimeAsync(5 * 60_000);
    expect(fetchMock).toHaveBeenCalledTimes(1); // aucune requête périodique

    unread = 0;
    await act(async () => {
      window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED));
    });
    await waitFor(() => expect(screen.queryByTestId("bell-badge")).not.toBeInTheDocument());
  });

  it("garde la dernière valeur quand le réseau est coupé", async () => {
    let online = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        if (!online) throw new TypeError("Failed to fetch");
        return jsonResponse({ unread_count: 2 });
      }),
    );
    render(
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>,
    );
    await screen.findByTestId("bell-badge");

    online = false;
    await act(async () => {
      window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED));
    });

    expect(screen.getByTestId("bell-badge")).toHaveTextContent("2");
  });
});

describe("NotificationsPage", () => {
  it("liste, regroupe visuellement et marque comme lu", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push(`${init?.method ?? "GET"} ${url}`);
        if (url.endsWith("/1/read/")) return jsonResponse(note(1, { is_read: true, read_at: "2026-09-29T08:00:00Z", count: 3 }));
        return jsonResponse({
          count: 2,
          next: null,
          previous: null,
          unread_count: 2,
          results: [note(1, { count: 3, title: "3 preuves rejetées" }), note(2)],
        });
      }),
    );
    render(
      <MemoryRouter>
        <NotificationsPage />
      </MemoryRouter>,
    );

    const items = await screen.findAllByTestId("notification");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("3 preuves rejetées");
    expect(items[0]).toHaveTextContent("×3");

    await userEvent.click(within(items[0]).getByRole("button", { name: "Marquer comme lu" }));

    await waitFor(() => expect(screen.getAllByTestId("notification")[0]).toHaveClass("read"));
    expect(calls).toContain("POST /api/notifications/1/read/");
    expect(screen.getByText(/1 non lue\(s\)/)).toBeInTheDocument();
  });

  it("état vide, puis erreur avec possibilité de réessayer", async () => {
    let fail = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        fail
          ? errorResponse("server_error", 500, "Panne")
          : jsonResponse({ count: 0, next: null, previous: null, unread_count: 0, results: [] }),
      ),
    );
    const { unmount } = render(
      <MemoryRouter>
        <NotificationsPage />
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("notifications-empty")).toBeInTheDocument();
    unmount();

    fail = true;
    render(
      <MemoryRouter>
        <NotificationsPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("Panne")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Réessayer" })).toBeInTheDocument();
  });

  it("charge la page suivante à la demande", async () => {
    const pages: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        pages.push(url);
        const second = url.includes("page=2");
        return jsonResponse({
          count: 2,
          next: second ? null : "http://x/api/notifications/?page=2",
          previous: null,
          unread_count: 2,
          results: [second ? note(2) : note(1)],
        });
      }),
    );
    render(
      <MemoryRouter>
        <NotificationsPage />
      </MemoryRouter>,
    );
    await screen.findByText("Notification 1");
    await userEvent.click(screen.getByRole("button", { name: "Charger plus" }));

    expect(await screen.findByText("Notification 2")).toBeInTheDocument();
    expect(screen.getByText("Notification 1")).toBeInTheDocument();
    expect(pages.some((url) => url.includes("page=2"))).toBe(true);
    expect(screen.queryByRole("button", { name: "Charger plus" })).not.toBeInTheDocument();
  });
});
