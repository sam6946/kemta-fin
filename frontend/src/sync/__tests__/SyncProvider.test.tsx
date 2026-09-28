/**
 * Reprise automatique de la synchronisation (MVP-009).
 *
 * Critère de sortie vérifié ici : « le retour online déclenche la synchronisation sans action
 * obligatoire de l'utilisateur », y compris quand l'application vient d'être rouverte.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { evidenceApi } from "../../api/evidences";
import { syncApi } from "../../api/sync";
import { resetLocalStorageForTests } from "../../lib/db";
import { enqueue, listOperations } from "../../lib/outbox";
import { SyncProvider, useSync } from "../SyncProvider";

vi.mock("../../api/evidences", () => ({
  evidenceApi: { upload: vi.fn() },
}));
vi.mock("../../api/sync", () => ({
  syncApi: { batch: vi.fn(), status: vi.fn(), forget: vi.fn() },
}));

const uploadMock = vi.mocked(evidenceApi.upload);
const batchMock = vi.mocked(syncApi.batch);

function setOnline(value: boolean) {
  Object.defineProperty(globalThis.navigator, "onLine", { value, configurable: true });
}

function Probe() {
  const { counts, online, syncing } = useSync();
  return (
    <div>
      <span data-testid="state">
        {online ? "en ligne" : "hors ligne"} · attente {counts.pending} · envoyées {counts.synced} ·
        erreurs {counts.failed}
      </span>
      {syncing ? <span data-testid="syncing">synchronisation…</span> : null}
    </div>
  );
}

function renderProvider() {
  return render(
    <SyncProvider>
      <Probe />
    </SyncProvider>,
  );
}

function queuedEvidence() {
  return enqueue({
    type: "EVIDENCE_UPLOAD",
    projectId: 12,
    label: "Preuve · Ferraillage",
    idempotencyKey: "key-1",
    payload: { captured_at: "2026-09-26T08:00:00Z", gps_status: "UNAVAILABLE" },
    file: new Blob([new Uint8Array([1, 2, 3])], { type: "image/jpeg" }),
    fileMeta: {
      name: "p.jpg",
      type: "image/jpeg",
      hash: "a".repeat(64),
      width: 100,
      height: 80,
      bytes: 3,
      originalBytes: 9,
    },
  });
}

beforeEach(async () => {
  await resetLocalStorageForTests();
  uploadMock.mockReset();
  batchMock.mockReset();
  setOnline(true);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SyncProvider", () => {
  it("envoie au démarrage ce qu'une session précédente avait laissé en attente", async () => {
    await queuedEvidence();
    uploadMock.mockResolvedValue({ id: 51 } as never);

    renderProvider();

    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(1));
    expect(uploadMock.mock.calls[0][1]).toBe("key-1"); // même clé qu'à la mise en file
    await waitFor(() =>
      expect(screen.getByTestId("state")).toHaveTextContent("envoyées 1"),
    );
    expect((await listOperations())[0].status).toBe("SYNCED");
  });

  it("déclenche l'envoi au retour du réseau, sans action de l'utilisateur", async () => {
    await queuedEvidence();
    setOnline(false);
    uploadMock.mockResolvedValue({ id: 52 } as never);

    renderProvider();
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("hors ligne"));
    expect(uploadMock).not.toHaveBeenCalled();

    // Le réseau revient : l'événement navigateur suffit.
    setOnline(true);
    window.dispatchEvent(new Event("online"));

    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("envoyées 1"));
  });

  it("ne bloque pas l'interface quand le serveur est injoignable", async () => {
    await queuedEvidence();
    uploadMock.mockRejectedValue(Object.assign(new Error("Réseau perdu"), { code: "offline" }));

    renderProvider();

    await waitFor(() => expect(uploadMock).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByTestId("state")).toHaveTextContent("erreurs 1"),
    );
    // L'opération reste dans la file, avec un nouvel essai programmé.
    const [operation] = await listOperations();
    expect(operation.status).toBe("FAILED");
    expect(operation.nextAttemptAt).toBeGreaterThan(0);
  });

  it("envoie un lot d'opérations sans fichier en une seule requête", async () => {
    await enqueue({
      type: "TASK_UPDATE",
      projectId: 12,
      label: "Tâche : coulage",
      idempotencyKey: "t-1",
      payload: { task: 5, progress: "50.00" },
    });
    await enqueue({
      type: "EVIDENCE_TRANSITION",
      projectId: 12,
      label: "Preuve #8 · validation",
      idempotencyKey: "t-2",
      payload: { evidence: 8, action: "VALIDATE" },
    });
    batchMock.mockImplementation(async (operations) => ({
      // Le serveur répond opération par opération, avec les identifiants envoyés.
      results: operations.map((operation, index) => ({
        op_id: operation.opId,
        type: operation.type,
        status: "SYNCED" as const,
        replayed: false,
        http_status: 200,
        entity_type: operation.type === "TASK_UPDATE" ? "Task" : "Evidence",
        entity_id: index + 5,
        entity: null,
        error: null,
      })),
      counts: { synced: operations.length, conflict: 0, failed: 0, replayed: 0 },
      server_time: "2026-09-26T08:00:00Z",
    }));

    renderProvider();

    await waitFor(() => expect(batchMock).toHaveBeenCalledTimes(1));
    const sent = batchMock.mock.calls[0][0];
    expect(sent.map((operation) => operation.idempotencyKey).sort()).toEqual(["t-1", "t-2"]);
    await waitFor(() =>
      expect(screen.getByTestId("state")).toHaveTextContent("envoyées 2"),
    );
  });
});
