/**
 * File d'attente hors ligne et moteur de synchronisation (MVP-009).
 *
 * Principe : **l'action terrain n'attend jamais le réseau**. Une capture de preuve (ou une
 * décision de validation) est d'abord écrite localement avec sa clé d'idempotence, puis
 * rejouée automatiquement dès que la connexion revient. Aucun envoi n'est perdu, aucun envoi
 * n'est dupliqué.
 *
 * Règles appliquées :
 *
 *  - **une clé d'idempotence par opération**, conservée telle quelle entre deux tentatives : le
 *    serveur reconnaît un renvoi et ne crée pas de second effet ;
 *  - **retry exponentiel** `min(30 s, 1 s × 2^essais)` avec gigue de ±20 %, limité à 8 essais ;
 *  - au-delà de la limite, l'opération est `FAILED` et n'est relancée que sur action de
 *    l'utilisateur (jamais de boucle infinie silencieuse) ;
 *  - un **conflit** (état serveur incompatible : hors périmètre, permission, transition
 *    impossible…) n'est jamais résolu en silence : il est présenté avec son motif.
 */

import { outboxStore } from "./db";
import type {
  FileMeta,
  OutboxOperation,
  OutboxOperationType,
  OutboxStatus,
  StoredFile,
} from "./outboxTypes";

export const MAX_ATTEMPTS = 8;
const BASE_DELAY_MS = 1_000;
const MAX_DELAY_MS = 30_000;
const BATCH_SIZE = 50;

/** Erreurs définitives : réessayer ne changera rien, l'utilisateur doit corriger. */
const PERMANENT_ERROR_CODES = new Set([
  "operation_requires_file",
  "unsupported_operation",
  "invalid_operation_payload",
  "validation_error",
  "unsupported_media_type",
  "image_dimensions_too_large",
  "file_too_large",
  "file_empty",
]);

/** Erreurs qui exigent une décision humaine : l'opération passe `CONFLICT`. */
const CONFLICT_ERROR_CODES = new Set([
  "permission_denied",
  "cannot_validate_own_evidence",
  "invalid_transition",
  "comment_required",
  "not_found",
  "evidence_out_of_geofence",
  "captured_at_in_future",
  "idempotency_key_conflict",
  "op_in_progress",
]);

export type SyncDeps = {
  uploadEvidence: (operation: OutboxOperation) => Promise<{ id: number; duplicate?: boolean }>;
  sendBatch: (
    operations: OutboxOperation[],
  ) => Promise<
    Array<{
      op_id: string;
      status: "SYNCED" | "CONFLICT" | "FAILED";
      entity_id?: number | null;
      error?: { code: string; message: string } | null;
    }>
  >;
  isOnline?: () => boolean;
  now?: () => number;
  random?: () => number;
};

export type SyncSummary = {
  attempted: number;
  synced: number;
  conflict: number;
  failed: number;
  skipped: number;
};

// --------------------------------------------------------------------------- helpers
function newId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return uuid.replace(/-/g, "");
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

/** Délai avant nouvel essai (exponentiel borné, avec gigue pour ne pas synchroniser en meute). */
export function backoffDelay(attempts: number, random: () => number = Math.random): number {
  const base = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempts);
  const jitter = 1 + (random() * 0.4 - 0.2);
  return Math.round(base * jitter);
}

export function isOnline(): boolean {
  if (typeof navigator === "undefined" || typeof navigator.onLine !== "boolean") return true;
  return navigator.onLine;
}

const listeners = new Set<() => void>();

/** Les écrans s'abonnent pour rafraîchir le badge et la galerie locale. */
export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notify(): void {
  for (const listener of listeners) listener();
}

// --------------------------------------------------------------------------- écriture
type EnqueueInput = {
  type: OutboxOperationType;
  projectId: number;
  label: string;
  payload?: Record<string, unknown>;
  file?: Blob | null;
  fileMeta?: FileMeta | null;
  idempotencyKey?: string;
};

/** Convertit le fichier à conserver : le binaire survit au rechargement, pas le `Blob`. */
export async function toStoredFile(blob: Blob): Promise<StoredFile> {
  return { data: await blob.arrayBuffer(), type: blob.type || "image/jpeg" };
}

/** Reconstruit le fichier à envoyer au serveur. */
export function fromStoredFile(file: StoredFile): Blob {
  return new Blob([file.data], { type: file.type || "image/jpeg" });
}

async function buildOperation(input: EnqueueInput): Promise<OutboxOperation> {
  const now = Date.now();
  const file = input.file ? await toStoredFile(input.file) : null;
  return {
    opId: newId(),
    localId: newId(),
    type: input.type,
    projectId: input.projectId,
    idempotencyKey: input.idempotencyKey ?? newId(),
    label: input.label,
    payload: input.payload ?? {},
    file,
    fileMeta: input.fileMeta ?? null,
    status: "PENDING",
    attempts: 0,
    nextAttemptAt: 0,
    lastError: null,
    errorCode: null,
    serverId: null,
    createdAt: now,
    updatedAt: now,
  };
}

export async function enqueue(input: EnqueueInput): Promise<OutboxOperation> {
  const operation = await buildOperation(input);
  await outboxStore.put(operation);
  notify();
  return operation;
}

export async function listOperations(): Promise<OutboxOperation[]> {
  const operations = await outboxStore.all();
  // Ordre d'insertion, départagé par l'identifiant : deux captures de la même milliseconde
  // doivent toujours sortir dans le même ordre (reprise déterministe après rechargement).
  return operations.sort(
    (a, b) => a.createdAt - b.createdAt || a.opId.localeCompare(b.opId),
  );
}

export async function listOperationsForProject(projectId: number): Promise<OutboxOperation[]> {
  const operations = await listOperations();
  return operations.filter((operation) => operation.projectId === projectId);
}

/** Opérations locales encore visibles dans la galerie (pas encore confirmées par le serveur). */
export async function listPendingEvidence(projectId: number): Promise<OutboxOperation[]> {
  const operations = await listOperationsForProject(projectId);
  return operations.filter(
    (operation) => operation.type === "EVIDENCE_UPLOAD" && operation.status !== "SYNCED",
  );
}

export type OutboxCounts = {
  total: number;
  pending: number;
  uploading: number;
  failed: number;
  conflict: number;
  synced: number;
};

export async function counts(): Promise<OutboxCounts> {
  const operations = await listOperations();
  const tally = (status: OutboxStatus) =>
    operations.filter((operation) => operation.status === status).length;
  return {
    total: operations.length,
    pending: tally("PENDING"),
    uploading: tally("UPLOADING"),
    failed: tally("FAILED"),
    conflict: tally("CONFLICT"),
    synced: tally("SYNCED"),
  };
}

// --------------------------------------------------------------------------- lecture/écriture d'une opération
async function update(
  opId: string,
  changes: Partial<OutboxOperation>,
): Promise<OutboxOperation | null> {
  const operations = await outboxStore.all();
  const current = operations.find((operation) => operation.opId === opId);
  if (!current) return null;
  const next = { ...current, ...changes, updatedAt: Date.now() };
  await outboxStore.put(next);
  notify();
  return next;
}

export function markUploading(opId: string) {
  return update(opId, { status: "UPLOADING" });
}

export function markSynced(opId: string, serverId: number | null, note: string | null = null) {
  return update(opId, {
    status: "SYNCED",
    attempts: 0,
    nextAttemptAt: 0,
    serverId,
    lastError: note,
    errorCode: null,
  });
}

export function markConflict(opId: string, code: string, message: string) {
  return update(opId, {
    status: "CONFLICT",
    attempts: 0,
    nextAttemptAt: 0,
    errorCode: code,
    lastError: message,
  });
}

export async function markFailed(
  opId: string,
  code: string,
  message: string,
  options: { retryable?: boolean; random?: () => number; now?: () => number } = {},
) {
  const operations = await outboxStore.all();
  const current = operations.find((operation) => operation.opId === opId);
  if (!current) return null;
  const attempts = current.attempts + 1;
  const retryable = options.retryable ?? !PERMANENT_ERROR_CODES.has(code);
  const exhausted = attempts >= MAX_ATTEMPTS;
  const now = options.now?.() ?? Date.now();
  return update(opId, {
    status: "FAILED",
    attempts,
    // Au-delà de la limite (ou erreur définitive) : plus de reprise automatique, l'utilisateur décide.
    nextAttemptAt: retryable && !exhausted ? now + backoffDelay(attempts, options.random) : 0,
    errorCode: code,
    lastError: message,
  });
}

/** Relance manuelle : remet l'opération en file même après la limite d'essais. */
export async function retryOperation(opId: string): Promise<OutboxOperation | null> {
  return update(opId, { status: "PENDING", attempts: 0, nextAttemptAt: 0, lastError: null, errorCode: null });
}

export async function retryAll(): Promise<number> {
  const operations = await listOperations();
  const toRetry = operations.filter(
    (operation) => operation.status === "FAILED" || operation.status === "CONFLICT",
  );
  for (const operation of toRetry) {
    await update(operation.opId, {
      status: "PENDING",
      attempts: 0,
      nextAttemptAt: 0,
      lastError: null,
      errorCode: null,
    });
  }
  return toRetry.length;
}

/** Abandonne une opération (l'utilisateur choisit de ne pas insister). */
export async function discardOperation(opId: string): Promise<void> {
  await outboxStore.delete(opId);
  notify();
}

/** Opérations à envoyer maintenant : en attente, ou échouées dont le délai est écoulé. */
export async function dueOperations(now: number = Date.now()): Promise<OutboxOperation[]> {
  const operations = await listOperations();
  return operations
    .filter((operation) => {
      if (operation.status === "PENDING" || operation.status === "UPLOADING") {
        return operation.nextAttemptAt <= now;
      }
      if (operation.status === "FAILED") {
        // `nextAttemptAt = 0` signifie « limite d'essais atteinte » : plus de reprise auto.
        return operation.nextAttemptAt > 0 && operation.nextAttemptAt <= now;
      }
      return false;
    })
    .sort((a, b) => a.createdAt - b.createdAt);
}

/** Prochain essai automatique prévu (permet de programmer un seul réveil, sans polling). */
export async function nextAttemptDelay(now: number = Date.now()): Promise<number | null> {
  const operations = await listOperations();
  const deadlines = operations
    .filter((operation) => operation.status === "FAILED" && operation.nextAttemptAt > now)
    .map((operation) => operation.nextAttemptAt);
  if (deadlines.length === 0) return null;
  return Math.min(...deadlines) - now;
}

/** Nombre total d'opérations en attente (badge temps réel). */
export async function pendingTotal(): Promise<number> {
  const tally = await counts();
  return tally.pending + tally.uploading + tally.failed + tally.conflict;
}

// --------------------------------------------------------------------------- moteur
function chunks<T>(items: T[], size: number): T[][] {
  const result: T[][] = [];
  for (let index = 0; index < items.length; index += size) {
    result.push(items.slice(index, index + size));
  }
  return result;
}

/**
 * Envoie les opérations dues. Ne lève jamais : chaque échec est enregistré dans la file
 * (l'interface doit toujours pouvoir dire où en est chaque élément).
 */
export async function runQueue(deps: SyncDeps): Promise<SyncSummary> {
  const summary: SyncSummary = {
    attempted: 0,
    synced: 0,
    conflict: 0,
    failed: 0,
    skipped: 0,
  };
  const now = deps.now ?? Date.now;
  const random = deps.random ?? Math.random;
  const online = deps.isOnline ?? isOnline;

  if (!online()) {
    summary.skipped = (await dueOperations(now())).length;
    return summary;
  }

  const due = await dueOperations(now());
  if (due.length === 0) return summary;

  const fileOperations = due.filter((operation) => operation.file);
  const batchOperations = due.filter((operation) => !operation.file);
  summary.attempted = due.length;

  // 1) Les pièces jointes partent une par une (reprise possible, erreurs ciblées).
  for (const operation of fileOperations) {
    await markUploading(operation.opId);
    try {
      const created = await deps.uploadEvidence(operation);
      await markSynced(
        operation.opId,
        created.id,
        created.duplicate ? "Déjà présente sur le serveur (aucun doublon créé)." : null,
      );
      summary.synced += 1;
    } catch (caught) {
      const { code, message } = describeError(caught);
      if (code === "duplicate_evidence") {
        // Règle documentée : le doublon est un succès sans second envoi.
        await markSynced(
          operation.opId,
          duplicateEvidenceId(caught),
          "Déjà présente sur le serveur (aucun doublon créé).",
        );
        summary.synced += 1;
      } else if (code === "offline") {
        await markFailed(operation.opId, code, message, { random, now });
        summary.failed += 1;
      } else if (CONFLICT_ERROR_CODES.has(code)) {
        await markConflict(operation.opId, code, message);
        summary.conflict += 1;
      } else {
        await markFailed(operation.opId, code, message, { random, now });
        summary.failed += 1;
      }
    }
  }

  // 2) Les opérations sans fichier voyagent en lot (une requête pour plusieurs actions).
  for (const group of chunks(batchOperations, BATCH_SIZE)) {
    for (const operation of group) await markUploading(operation.opId);
    try {
      const results = await deps.sendBatch(group);
      const byOpId = new Map(results.map((result) => [result.op_id, result]));
      for (const operation of group) {
        const result = byOpId.get(operation.opId);
        if (!result) {
          await markFailed(operation.opId, "no_result", "Aucune réponse pour cette opération.", {
            random,
            now,
          });
          summary.failed += 1;
          continue;
        }
        if (result.status === "SYNCED") {
          await markSynced(operation.opId, result.entity_id ?? null);
          summary.synced += 1;
        } else if (result.status === "CONFLICT") {
          await markConflict(
            operation.opId,
            result.error?.code ?? "conflict",
            result.error?.message ?? "Conflit à vérifier.",
          );
          summary.conflict += 1;
        } else {
          await markFailed(
            operation.opId,
            result.error?.code ?? "failed",
            result.error?.message ?? "Envoi impossible.",
            { random, now },
          );
          summary.failed += 1;
        }
      }
    } catch (caught) {
      const { code, message } = describeError(caught);
      // Une coupure réseau en plein lot : toutes les opérations du lot attendent le prochain essai.
      for (const operation of group) {
        await markFailed(operation.opId, code, message, { random, now });
        summary.failed += 1;
      }
    }
  }

  return summary;
}

type CaughtError = { code?: string; message?: string; details?: Record<string, unknown> };

function describeError(caught: unknown): { code: string; message: string } {
  const error = caught as CaughtError;
  return {
    code: error?.code ?? "server_error",
    message: error?.message ?? "Envoi impossible : réessayez.",
  };
}

function duplicateEvidenceId(caught: unknown): number | null {
  const details = (caught as CaughtError)?.details ?? {};
  const evidence = details.evidence as { id?: number } | undefined;
  return evidence?.id ?? null;
}
