/**
 * Types de la file d'attente hors ligne (MVP-009).
 *
 * Une opération décrit **une intention utilisateur** (« déposer cette preuve », « valider cette
 * preuve ») et tout ce qu'il faut pour la rejouer à l'identique après une coupure réseau :
 * sa clé d'idempotence, son contenu, son nombre de tentatives et son prochain essai.
 */

export type OutboxStatus = "PENDING" | "UPLOADING" | "SYNCED" | "FAILED" | "CONFLICT";

/** Types rejouables en lot (sans fichier) + le type « avec fichier » traité un par un. */
export type OutboxOperationType =
  | "EVIDENCE_UPLOAD"
  | "EVIDENCE_TRANSITION"
  | "TASK_UPDATE"
  | "MILESTONE_UPDATE"
  | "TASK_CREATE"
  | "MILESTONE_CREATE";

/**
 * Fichier en attente d'envoi.
 *
 * Le contenu est conservé en **binaire** (`ArrayBuffer`) plutôt qu'en `Blob` : c'est la forme
 * la plus universellement clonable par IndexedDB (certains navigateurs d'entrée de gamme
 * perdent silencieusement les `Blob` dans un clone structuré). Le type MIME est conservé à part,
 * ce qui suffit à reconstruire exactement le fichier à envoyer.
 */
export type StoredFile = {
  data: ArrayBuffer;
  type: string;
};

export type FileMeta = {
  name: string;
  type: string;
  hash: string;
  width: number;
  height: number;
  bytes: number;
  originalBytes: number;
};

export type OutboxOperation = {
  /** Identifiant local de l'opération (stable entre deux exécutions). */
  opId: string;
  /** Identifiant local de l'entité (affichée dans la galerie avant synchronisation). */
  localId: string;
  type: OutboxOperationType;
  projectId: number;
  /** Clé transmise au serveur : garantit qu'un renvoi ne crée jamais deux effets. */
  idempotencyKey: string;
  /** Libellé lisible pour l'utilisateur (« Preuve · Ferraillage », « Tâche : coulage »). */
  label: string;
  payload: Record<string, unknown>;
  file?: StoredFile | null;
  fileMeta?: FileMeta | null;
  status: OutboxStatus;
  attempts: number;
  /** Horodatage (ms) à partir duquel un nouvel essai est autorisé (retry exponentiel). */
  nextAttemptAt: number;
  lastError: string | null;
  errorCode: string | null;
  /** Identifiant serveur une fois l'opération appliquée (preuve créée, tâche modifiée…). */
  serverId: number | null;
  createdAt: number;
  updatedAt: number;
};

export const OUTBOX_STATUS_LABELS: Record<OutboxStatus, string> = {
  PENDING: "En attente d'envoi",
  UPLOADING: "Envoi en cours",
  SYNCED: "Synchronisée",
  FAILED: "Échec — à relancer",
  CONFLICT: "Conflit — à vérifier",
};

export const OUTBOX_STATUS_TONES: Record<OutboxStatus, "info" | "success" | "error" | "warning"> =
  {
    PENDING: "info",
    UPLOADING: "info",
    SYNCED: "success",
    FAILED: "error",
    CONFLICT: "warning",
  };
