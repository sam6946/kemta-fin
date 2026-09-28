/** Appels preuves terrain — contrat `docs/api-contract.md` §6 (MVP-007, MVP-008). */

import { request } from "./client";
import type { Paginated } from "./organizations";

export type EvidenceStatus = "PENDING" | "VALIDATED" | "REJECTED" | "FLAGGED";
export type EvidenceSyncStatus = "PENDING" | "UPLOADING" | "SYNCED" | "FAILED" | "CONFLICT";
export type EvidenceGpsStatus = "AVAILABLE" | "UNAVAILABLE" | "DENIED";
export type EvidenceAction = "VALIDATE" | "REJECT" | "FLAG" | "REOPEN";

export type EvidencePermissions = {
  /** L'utilisateur peut valider/rejeter cette preuve (capacité backend, hors auto-validation). */
  validate_evidence: boolean;
  /** Capacité présente mais l'utilisateur est l'auteur : un tiers doit statuer. */
  cannot_validate_own: boolean;
  can_see_location: boolean;
};

export type Evidence = {
  id: number;
  project: number;
  task: number | null;
  task_title: string | null;
  author: {
    id: number;
    first_name: string;
    last_name: string;
    phone_masked: string;
    role_label: string;
  };
  status: EvidenceStatus;
  status_label: string;
  sync_status: EvidenceSyncStatus;
  sync_status_label: string;
  captured_at: string;
  received_at: string;
  latitude: string | null;
  longitude: string | null;
  gps_accuracy: number | null;
  gps_status: EvidenceGpsStatus;
  gps_status_label: string;
  device_model: string;
  device_platform: string;
  app_version: string;
  description: string;
  hash_sha256: string;
  size_bytes: number;
  content_type: string;
  file_url: string;
  thumbnail_url: string;
  distance_from_site_m: number | null;
  inside_geofence: boolean | null;
  validation_count: number;
  permissions: EvidencePermissions;
  created_at: string;
};

export type EvidenceValidation = {
  id: number;
  actor: { id: number; first_name: string; last_name: string; role_label: string };
  action: EvidenceAction;
  action_label: string;
  from_status: EvidenceStatus;
  to_status: EvidenceStatus;
  comment: string;
  created_at: string;
};

export type EvidencePage = Paginated<Evidence> & {
  counts: { pending: number; validated: number; rejected: number; flagged: number };
};

export type EvidenceUpload = {
  project: number;
  file: Blob;
  captured_at: string;
  latitude?: number | null;
  longitude?: number | null;
  gps_accuracy?: number | null;
  gps_status: EvidenceGpsStatus;
  device_model?: string;
  device_platform?: string;
  app_version?: string;
  description?: string;
  task?: number | null;
};

/** Statuts et libellés des preuves : source unique côté backend (`/api/meta/status/`). */
export const EVIDENCE_STATUS_TONES: Record<EvidenceStatus, "info" | "success" | "error" | "warning"> = {
  PENDING: "info",
  VALIDATED: "success",
  REJECTED: "error",
  FLAGGED: "warning",
};

export const evidenceApi = {
  list: (projectId: string | number, query = "") =>
    request<EvidencePage>(`/projects/${projectId}/evidences/${query}`, { auth: true }),

  /** Dépôt multipart avec clé d'idempotence obligatoire (rejeu sûr après coupure réseau). */
  upload: (payload: EvidenceUpload, idempotencyKey: string) => {
    const body = new FormData();
    body.append("project", String(payload.project));
    body.append("file", payload.file, `preuve-${Date.now()}.jpg`);
    body.append("captured_at", payload.captured_at);
    body.append("gps_status", payload.gps_status);
    if (payload.latitude !== null && payload.latitude !== undefined) {
      body.append("latitude", String(payload.latitude));
      body.append("longitude", String(payload.longitude));
    }
    if (payload.gps_accuracy !== null && payload.gps_accuracy !== undefined) {
      body.append("gps_accuracy", String(payload.gps_accuracy));
    }
    if (payload.device_model) body.append("device_model", payload.device_model);
    if (payload.device_platform) body.append("device_platform", payload.device_platform);
    if (payload.app_version) body.append("app_version", payload.app_version);
    if (payload.description) body.append("description", payload.description);
    if (payload.task) body.append("task", String(payload.task));
    return request<Evidence>("/evidences/", {
      method: "POST",
      auth: true,
      body,
      idempotencyKey,
    });
  },

  detail: (id: number) => request<Evidence>(`/evidences/${id}/`, { auth: true }),

  history: (id: number) =>
    request<{ count: number; results: EvidenceValidation[] }>(`/evidences/${id}/history/`, {
      auth: true,
    }),

  transition: (id: number, action: EvidenceAction, comment = "") =>
    request<Evidence & { last_validation: EvidenceValidation }>(`/evidences/${id}/transition/`, {
      method: "POST",
      auth: true,
      body: { action, comment },
    }),

  pending: (query = "") =>
    request<{ count: number; results: Evidence[] }>(`/evidences/pending/${query}`, { auth: true }),
};
