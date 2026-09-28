/** Appels de synchronisation hors ligne — contrat `docs/api-contract.md` §6 (MVP-009). */

import { request } from "./client";
import type { OutboxOperation } from "../lib/outboxTypes";

export type SyncBatchResult = {
  op_id: string;
  type: string;
  status: "SYNCED" | "CONFLICT" | "FAILED";
  replayed: boolean;
  http_status: number;
  entity_type: string | null;
  entity_id: number | null;
  entity: Record<string, unknown> | null;
  error: { code: string; message: string; details: Record<string, unknown> } | null;
};

export type SyncBatchResponse = {
  results: SyncBatchResult[];
  counts: { synced: number; conflict: number; failed: number; replayed: number };
  server_time: string;
};

export type SyncServerStatus = {
  server_time: string;
  supported_operations: string[];
  file_operations: string[];
  in_progress: number;
  applied: number;
  last_applied_at: string | null;
  batch_limit: number;
};

export const syncApi = {
  /** Rejeu d'un lot : les opérations sans fichier voyagent ensemble. */
  batch: (operations: OutboxOperation[]) =>
    request<SyncBatchResponse>("/sync/batch/", {
      method: "POST",
      auth: true,
      body: {
        operations: operations.map((operation) => ({
          op_id: operation.opId,
          type: operation.type,
          idempotency_key: operation.idempotencyKey,
          payload: operation.payload,
        })),
      },
    }),

  status: () => request<SyncServerStatus>("/sync/status/", { auth: true }),

  /** Libère une clé restée bloquée après la disparition d'un appareil en plein envoi. */
  forget: (idempotencyKey: string) =>
    request<{ released: boolean }>(`/sync/operations/${idempotencyKey}/forget/`, {
      method: "POST",
      auth: true,
    }),
};
