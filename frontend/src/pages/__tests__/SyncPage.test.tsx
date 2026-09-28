/**
 * Écran de suivi de synchronisation (MVP-009).
 *
 * Vérifie ce que l'utilisateur de terrain doit pouvoir constater : ce qui reste à envoyer,
 * ce qui a échoué, ce qui est en conflit — et les actions disponibles.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { evidenceApi } from "../../api/evidences";
import { syncApi } from "../../api/sync";
import { resetLocalStorageForTests } from "../../lib/db";
import { enqueue, listOperations, markConflict, markFailed, markUploading } from "../../lib/outbox";
import { SyncProvider } from "../../sync/SyncProvider";
import SyncPage from "../SyncPage";

vi.mock("../../api/evidences", () => ({ evidenceApi: { upload: vi.fn() } }));
vi.mock("../../api/sync", () => ({
  syncApi: { batch: vi.fn(), status: vi.fn(), forget: vi.fn() },
}));

const uploadMock = vi.mocked(evidenceApi.upload);
const statusMock = vi.mocked(syncApi.status);

function evidenceInput(overrides: Record<string, unknown> = {}) {
  return {
    type: "EVIDENCE_UPLOAD" as const,
    projectId: 12,
    label: "Preuve · Ferraillage poteaux",
    idempotencyKey: "key-evidence",
    payload: { captured_at: "2026-09-26T08:00:00Z", gps_status: "UNAVAILABLE" },
    file: new Blob([new Uint8Array([1, 2, 3])], { type: "image/jpeg" }),
    fileMeta: {
      name: "p.jpg",
      type: "image/jpeg",
      hash: "a".repeat(64),
      width: 1600,
      height: 1200,
      bytes: 3,
      originalBytes: 9,
    },
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/synchronisation"]}>
      <SyncProvider>
        <SyncPage />
      </SyncProvider>
    </MemoryRouter>,
  );
}

beforeEach(async () => {
  await resetLocalStorageForTests();
  uploadMock.mockReset();
  statusMock.mockReset();
  statusMock.mockResolvedValue({
    server_time: "2026-09-26T08:00:00Z",
    supported_operations: ["EVIDENCE_TRANSITION", "TASK_UPDATE"],
    file_operations: ["EVIDENCE_UPLOAD"],
    in_progress: 0,
    applied: 4,
    last_applied_at: "2026-09-26T07:30:00Z",
    batch_limit: 50,
  });
  Object.defineProperty(globalThis.navigator, "onLine", { value: true, configurable: true });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SyncPage", () => {
  it("annonce clairement qu'il n'y a rien à envoyer", async () => {
    renderPage();
    expect(await screen.findByTestId("sync-empty")).toBeInTheDocument();
  });

  it("liste les éléments en attente avec leur état et leur projet", async () => {
    const operation = await enqueue(evidenceInput());
    await markUploading(operation.opId); // interrompu en plein envoi
    uploadMock.mockImplementation(() => new Promise(() => {})); // réseau qui ne répond pas

    renderPage();

    const item = await screen.findByTestId("sync-item");
    expect(within(item).getByText("Preuve · Ferraillage poteaux")).toBeInTheDocument();
    expect(within(item).getByText(/Envoi en cours/)).toBeInTheDocument();
    expect(within(item).getByText(/Projet #12/)).toBeInTheDocument();
  });

  it("affiche le motif d'un échec et permet de relancer l'élément", async () => {
    const user = userEvent.setup();
    const operation = await enqueue(evidenceInput({ idempotencyKey: "key-retry" }));
    await markFailed(operation.opId, "offline", "Pas de connexion.");
    uploadMock.mockResolvedValue({ id: 70 } as never);

    renderPage();

    const item = await screen.findByTestId("sync-item");
    expect(within(item).getByText(/Échec — à relancer/)).toBeInTheDocument();
    expect(within(item).getByText(/Pas de connexion/)).toBeInTheDocument();

    await user.click(within(item).getByRole("button", { name: /relancer/i }));

    // La relance réutilise la même clé d'idempotence : aucun doublon possible.
    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(1));
    expect(uploadMock.mock.calls[0][1]).toBe("key-retry");
    await waitFor(() => expect(screen.queryByTestId("sync-item")).not.toBeInTheDocument());
  });

  it("présente un conflit avec son motif, sans le rejouer en boucle", async () => {
    const operation = await enqueue(evidenceInput({ idempotencyKey: "key-conflict" }));
    await markConflict(operation.opId, "evidence_out_of_geofence", "Position trop éloignée du chantier.");

    renderPage();

    const item = await screen.findByTestId("sync-item");
    expect(within(item).getByText(/Conflit — à vérifier/)).toBeInTheDocument();
    expect(within(item).getByText(/Position trop éloignée/)).toBeInTheDocument();
    expect(uploadMock).not.toHaveBeenCalled();
    expect((await listOperations())[0].status).toBe("CONFLICT");
  });

  it("permet d'abandonner une opération qui ne partira jamais", async () => {
    const user = userEvent.setup();
    const operation = await enqueue(evidenceInput({ idempotencyKey: "key-discard" }));
    await markConflict(operation.opId, "unsupported_media_type", "Format refusé.");

    renderPage();
    const item = await screen.findByTestId("sync-item");
    await user.click(within(item).getByRole("button", { name: /abandonner/i }));

    await waitFor(() => expect(screen.queryByTestId("sync-item")).not.toBeInTheDocument());
    expect(await listOperations()).toHaveLength(0);
    expect(screen.getByText(/Opération abandonnée/)).toBeInTheDocument();
  });

  it("affiche l'état du serveur et déclenche une synchronisation à la demande", async () => {
    const user = userEvent.setup();
    await enqueue(evidenceInput({ idempotencyKey: "key-now" }));
    uploadMock.mockResolvedValue({ id: 71 } as never);

    renderPage();

    await waitFor(() => expect(screen.getByTestId("sync-server")).toHaveTextContent("4 opération"));
    await user.click(screen.getByTestId("sync-now"));
    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(1));
  });
});
