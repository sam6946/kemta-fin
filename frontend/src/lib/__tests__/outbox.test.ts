/**
 * File d'attente hors ligne (MVP-009) : persistance, idempotence, retry et conflits.
 *
 * Ces tests s'exécutent sur un vrai moteur IndexedDB (`fake-indexeddb`) : ce qui est vérifié ici
 * est exactement ce que fera le téléphone du terrain — y compris après fermeture de l'onglet.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../api/client";
import { isPersistent, outboxStore, resetLocalStorageForTests } from "../db";
import {
  MAX_ATTEMPTS,
  backoffDelay,
  counts,
  discardOperation,
  dueOperations,
  enqueue,
  listOperations,
  listPendingEvidence,
  markFailed,
  markUploading,
  nextAttemptDelay,
  pendingTotal,
  retryAll,
  retryOperation,
  runQueue,
  subscribe,
} from "../outbox";

const HASH = "a".repeat(64);

function photo(overrides: Record<string, unknown> = {}) {
  return {
    type: "EVIDENCE_UPLOAD" as const,
    projectId: 12,
    label: "Preuve · Ferraillage",
    payload: { captured_at: "2026-09-26T08:00:00Z", gps_status: "UNAVAILABLE" },
    file: new Blob([new Uint8Array([1, 2, 3])], { type: "image/jpeg" }),
    fileMeta: {
      name: "preuve.jpg",
      type: "image/jpeg",
      hash: HASH,
      width: 1600,
      height: 1200,
      bytes: 3,
      originalBytes: 9,
    },
    idempotencyKey: "key-photo-1",
    ...overrides,
  };
}

function taskOperation(overrides: Record<string, unknown> = {}) {
  return {
    type: "TASK_UPDATE" as const,
    projectId: 12,
    label: "Tâche : coulage dalle",
    payload: { task: 5, progress: "60.00" },
    idempotencyKey: "key-task-1",
    ...overrides,
  };
}

beforeEach(async () => {
  await resetLocalStorageForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("file d'attente locale", () => {
  it("conserve une preuve capturée hors ligne, même après rechargement", async () => {
    const operation = await enqueue(photo());

    // Nouvelle lecture = ce que verrait l'application rouverte.
    const stored = await outboxStore.all();
    expect(stored).toHaveLength(1);
    expect(stored[0].opId).toBe(operation.opId);
    expect(stored[0].status).toBe("PENDING");
    // Le fichier et les métadonnées sont bien là : la photo n'est pas perdue.
    expect(new Uint8Array(stored[0].file!.data)).toEqual(new Uint8Array([1, 2, 3]));
    expect(stored[0].file!.type).toBe("image/jpeg");
    expect(stored[0].fileMeta?.hash).toBe(HASH);
    expect(stored[0].payload.captured_at).toBe("2026-09-26T08:00:00Z");
    expect(isPersistent()).toBe(true);
  });

  it("repasse en attente une opération interrompue en plein envoi", async () => {
    const operation = await enqueue(photo());
    await markUploading(operation.opId);

    // Coupure : l'application est fermée sans réponse du serveur.
    const due = await dueOperations(Date.now());
    expect(due.map((item) => item.opId)).toEqual([operation.opId]);
    expect((await listOperations())[0].status).toBe("UPLOADING");
  });

  it("respecte le délai croissant entre deux tentatives", async () => {
    const operation = await enqueue(taskOperation());
    const now = 1_000_000;

    await markFailed(operation.opId, "offline", "Pas de connexion.", {
      random: () => 0.5,
      now: () => now,
    });
    const afterFirst = (await listOperations())[0];
    expect(afterFirst.attempts).toBe(1);
    expect(afterFirst.nextAttemptAt).toBe(now + 2_000); // 1 s × 2¹, gigue neutre

    // Tout de suite après : rien à envoyer.
    expect(await dueOperations(now)).toHaveLength(0);
    expect(await dueOperations(now + 2_000)).toHaveLength(1);
    expect(await nextAttemptDelay(now)).toBe(2_000);
  });

  it("arrête la reprise automatique après la limite d'essais", async () => {
    const operation = await enqueue(taskOperation());
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
      await markFailed(operation.opId, "server_error", "Erreur serveur.", {
        random: () => 0.5,
      });
    }
    const stored = (await listOperations())[0];
    expect(stored.attempts).toBe(MAX_ATTEMPTS);
    expect(stored.status).toBe("FAILED");
    expect(stored.nextAttemptAt).toBe(0);
    // Le temps peut passer : plus aucune reprise automatique n'est planifiée.
    expect(await dueOperations(Date.now() + 86_400_000)).toHaveLength(0);
    expect(await nextAttemptDelay(Date.now())).toBeNull();
  });

  it("ne réessaie plus une erreur définitive mais la laisse relançable à la main", async () => {
    const operation = await enqueue(taskOperation());
    await markFailed(operation.opId, "unsupported_media_type", "Format non accepté.");

    const stored = (await listOperations())[0];
    expect(stored.nextAttemptAt).toBe(0);
    expect(stored.lastError).toBe("Format non accepté.");

    await retryOperation(operation.opId);
    expect(await dueOperations(Date.now())).toHaveLength(1);
    expect((await listOperations())[0].attempts).toBe(0);
  });

  it("expose les compteurs et prévient les abonnés à chaque changement", async () => {
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);

    const operation = await enqueue(photo());
    await markUploading(operation.opId);
    expect(listener).toHaveBeenCalled();

    const tally = await counts();
    expect(tally).toMatchObject({ total: 1, uploading: 1, pending: 0 });
    expect(await pendingTotal()).toBe(1);

    unsubscribe();
    await markFailed(operation.opId, "offline", "Pas de connexion.");
    const called = listener.mock.calls.length;
    await markFailed(operation.opId, "offline", "Pas de connexion.");
    expect(listener.mock.calls.length).toBe(called); // désabonné : plus de notification
  });

  it("permet d'abandonner une opération", async () => {
    const operation = await enqueue(photo());
    await discardOperation(operation.opId);
    expect(await listOperations()).toHaveLength(0);
  });
});

describe("moteur de synchronisation", () => {
  it("n'envoie rien hors ligne et garde tout en attente", async () => {
    await enqueue(photo());
    await enqueue(taskOperation());
    const uploadEvidence = vi.fn();
    const sendBatch = vi.fn();

    const summary = await runQueue({
      uploadEvidence,
      sendBatch,
      isOnline: () => false,
    });

    expect(summary.skipped).toBe(2);
    expect(uploadEvidence).not.toHaveBeenCalled();
    expect(sendBatch).not.toHaveBeenCalled();
    expect((await counts()).pending).toBe(2);
  });

  it("envoie la photo avec sa clé d'idempotence puis la marque synchronisée", async () => {
    await enqueue(photo());
    const uploadEvidence = vi.fn().mockResolvedValue({ id: 41 });

    const summary = await runQueue({ uploadEvidence, sendBatch: vi.fn() });

    expect(summary.synced).toBe(1);
    expect(uploadEvidence).toHaveBeenCalledTimes(1);
    const sent = uploadEvidence.mock.calls[0][0];
    expect(sent.idempotencyKey).toBe("key-photo-1");
    expect(sent.type).toBe("EVIDENCE_UPLOAD");

    const stored = (await listOperations())[0];
    expect(stored.status).toBe("SYNCED");
    expect(stored.serverId).toBe(41);
    // Plus rien à envoyer : la boucle ne peut pas créer de doublon.
    await runQueue({ uploadEvidence, sendBatch: vi.fn() });
    expect(uploadEvidence).toHaveBeenCalledTimes(1);
  });

  it("regroupe les opérations sans fichier en un seul appel réseau", async () => {
    await enqueue(taskOperation({ idempotencyKey: "k1" }));
    await enqueue(taskOperation({ idempotencyKey: "k2", payload: { task: 6, progress: "10.00" } }));
    await enqueue(photo());

    const sendBatch = vi.fn(async (operations: Array<{ opId: string }>) =>
      operations.map((operation) => ({
        op_id: operation.opId,
        status: "SYNCED" as const,
        entity_id: 7,
      })),
    );
    const uploadEvidence = vi.fn().mockResolvedValue({ id: 42 });

    const summary = await runQueue({ uploadEvidence, sendBatch });

    expect(sendBatch).toHaveBeenCalledTimes(1); // un seul appel pour les deux tâches
    expect(sendBatch.mock.calls[0][0]).toHaveLength(2);
    expect(uploadEvidence).toHaveBeenCalledTimes(1);
    expect(summary.synced).toBe(3);
  });

  it("classe les échecs : conflit pour un état incompatible, échec pour un réseau absent", async () => {
    await enqueue(taskOperation({ idempotencyKey: "conflict" }));
    await enqueue(taskOperation({ idempotencyKey: "network", payload: { task: 9 } }));

    const sendBatch = vi.fn(
      async (operations: Array<{ idempotencyKey: string; opId: string }>) =>
        operations.map((operation) =>
          operation.idempotencyKey === "conflict"
            ? {
                op_id: operation.opId,
                status: "CONFLICT" as const,
                error: { code: "invalid_transition", message: "Transition impossible." },
              }
            : {
                op_id: operation.opId,
                status: "FAILED" as const,
                error: { code: "offline", message: "Pas de connexion." },
              },
        ),
    );

    const summary = await runQueue({ uploadEvidence: vi.fn(), sendBatch });

    expect(summary.conflict).toBe(1);
    expect(summary.failed).toBe(1);
    const operations = await listOperations();
    const conflict = operations.find((operation) => operation.idempotencyKey === "conflict")!;
    const network = operations.find((operation) => operation.idempotencyKey === "network")!;
    expect(conflict.status).toBe("CONFLICT");
    expect(conflict.errorCode).toBe("invalid_transition");
    expect(conflict.nextAttemptAt).toBe(0); // un conflit n'est jamais rejoué en boucle
    expect(network.status).toBe("FAILED");
    expect(network.nextAttemptAt).toBeGreaterThan(0); // le réseau, lui, reviendra
  });

  it("traite un doublon comme un succès sans second envoi", async () => {
    const operation = await enqueue(photo());
    const uploadEvidence = vi
      .fn()
      .mockRejectedValue(
        new ApiError("duplicate_evidence", "Cette photo a déjà été déposée.", 409, {
          evidence: { id: 90 },
        }),
      );

    const summary = await runQueue({ uploadEvidence, sendBatch: vi.fn() });

    expect(summary.synced).toBe(1);
    const stored = (await listOperations())[0];
    expect(stored.opId).toBe(operation.opId);
    expect(stored.status).toBe("SYNCED");
    expect(stored.serverId).toBe(90);
    expect(stored.lastError).toContain("Déjà présente");
  });

  it("retient un conflit de périmètre au lieu de le réessayer indéfiniment", async () => {
    await enqueue(photo());
    const uploadEvidence = vi
      .fn()
      .mockRejectedValue(
        new ApiError("evidence_out_of_geofence", "Position trop éloignée du chantier.", 422),
      );

    const summary = await runQueue({ uploadEvidence, sendBatch: vi.fn() });

    expect(summary.conflict).toBe(1);
    const stored = (await listOperations())[0];
    expect(stored.status).toBe("CONFLICT");
    expect(stored.nextAttemptAt).toBe(0);
  });

  it("marque en échec toutes les opérations d'un lot interrompu", async () => {
    await enqueue(taskOperation({ idempotencyKey: "k1" }));
    await enqueue(taskOperation({ idempotencyKey: "k2" }));
    const sendBatch = vi.fn().mockRejectedValue(new ApiError("offline", "Réseau perdu.", 0));

    const summary = await runQueue({ uploadEvidence: vi.fn(), sendBatch });

    expect(summary.failed).toBe(2);
    const operations = await listOperations();
    expect(operations.every((operation) => operation.status === "FAILED")).toBe(true);
    expect(operations.every((operation) => operation.nextAttemptAt > 0)).toBe(true);
  });

  it("relance toutes les erreurs à la demande de l'utilisateur", async () => {
    const first = await enqueue(photo({ idempotencyKey: "k1" }));
    const second = await enqueue(taskOperation({ idempotencyKey: "k2" }));
    const uploadEvidence = vi.fn().mockRejectedValue(new ApiError("offline", "Réseau perdu.", 0));
    await runQueue({ uploadEvidence, sendBatch: vi.fn() });
    await markFailed(second.opId, "server_error", "Erreur serveur.");

    expect(await retryAll()).toBe(2);
    const operations = await listOperations();
    expect(operations.map((operation) => operation.status)).toEqual(["PENDING", "PENDING"]);
    expect(operations.every((operation) => operation.attempts === 0)).toBe(true);
    expect(first.opId).toBeTruthy();
  });

  it("expose les preuves encore locales pour un projet", async () => {
    await enqueue(photo({ idempotencyKey: "k1" }));
    await enqueue(taskOperation());
    await enqueue(photo({ idempotencyKey: "k2", projectId: 99 }));

    const local = await listPendingEvidence(12);
    expect(local).toHaveLength(1);
    expect(local[0].label).toContain("Preuve");
  });

  it("borne le délai d'attente et garde une gigue maîtrisée", () => {
    expect(backoffDelay(0, () => 0.5)).toBe(1_000);
    expect(backoffDelay(3, () => 0.5)).toBe(8_000);
    expect(backoffDelay(30, () => 0.5)).toBe(30_000); // plafond
    expect(backoffDelay(3, () => 0)).toBeLessThan(8_000); // gigue basse
    expect(backoffDelay(3, () => 1)).toBeGreaterThan(8_000); // gigue haute
  });
});
