/**
 * Contexte de synchronisation (MVP-009).
 *
 * Il relie la file locale aux écrans :
 *
 *  - au montage : reprise de la file (une appli rouverte après une coupure envoie ce qui reste) ;
 *  - au retour du réseau (`online`) et quand l'onglet redevient visible : nouvelle tentative ;
 *  - après un échec : un **seul** réveil programmé à l'échéance du retry exponentiel
 *    (aucun polling, conformément à l'exigence « pas de boucle < 30 s ») ;
 *  - expose les compteurs (badge, galerie) et une relance manuelle.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ApiError } from "../api/client";
import { evidenceApi } from "../api/evidences";
import { syncApi } from "../api/sync";
import { isPersistent } from "../lib/db";
import {
  counts as outboxCounts,
  fromStoredFile,
  isOnline as defaultIsOnline,
  nextAttemptDelay,
  runQueue,
  subscribe,
  type OutboxCounts,
  type SyncDeps,
  type SyncSummary,
} from "../lib/outbox";
import type { OutboxOperation } from "../lib/outboxTypes";

type SyncContextValue = {
  counts: OutboxCounts;
  online: boolean;
  persistent: boolean;
  syncing: boolean;
  lastSummary: SyncSummary | null;
  lastSyncAt: number | null;
  /** Envoie tout ce qui est en attente (bouton « Synchroniser maintenant »). */
  syncNow: () => Promise<SyncSummary>;
  /** Rafraîchit les compteurs (après une mise en file depuis un écran). */
  refresh: () => Promise<void>;
};

const SyncContext = createContext<SyncContextValue | null>(null);

const EMPTY_COUNTS: OutboxCounts = {
  total: 0,
  pending: 0,
  uploading: 0,
  failed: 0,
  conflict: 0,
  synced: 0,
};

/** Envoi réel d'une preuve mise en file : rejoue **la même** clé d'idempotence. */
async function uploadEvidence(operation: OutboxOperation) {
  const payload = operation.payload as { captured_at?: string; gps_status?: string };
  // Le binaire est reconstruit en fichier au moment de l'envoi (même contenu, même type).
  const file = operation.file ? fromStoredFile(operation.file) : null;
  if (!file) throw new ApiError("operation_requires_file", "Photo manquante.", 400);

  const created = await evidenceApi.upload(
    {
      project: operation.projectId,
      file,
      captured_at: payload.captured_at ?? new Date().toISOString(),
      latitude: (operation.payload.latitude as number | null) ?? null,
      longitude: (operation.payload.longitude as number | null) ?? null,
      gps_accuracy: (operation.payload.gps_accuracy as number | null) ?? null,
      gps_status: (payload.gps_status as "AVAILABLE" | "UNAVAILABLE" | "DENIED") ?? "UNAVAILABLE",
      device_model: operation.payload.device_model as string | undefined,
      device_platform: operation.payload.device_platform as string | undefined,
      app_version: operation.payload.app_version as string | undefined,
      description: operation.payload.description as string | undefined,
      task: (operation.payload.task as number | null) ?? null,
    },
    operation.idempotencyKey,
  );
  return { id: created.id };
}

export const syncDeps: SyncDeps = {
  uploadEvidence,
  sendBatch: async (operations) => {
    const response = await syncApi.batch(operations);
    return response.results;
  },
  isOnline: defaultIsOnline,
};

export function SyncProvider({ children }: { children: ReactNode }) {
  const [counts, setCounts] = useState<OutboxCounts>(EMPTY_COUNTS);
  const [online, setOnline] = useState<boolean>(defaultIsOnline());
  const [syncing, setSyncing] = useState(false);
  const [lastSummary, setLastSummary] = useState<SyncSummary | null>(null);
  const [lastSyncAt, setLastSyncAt] = useState<number | null>(null);
  const timerRef = useRef<number | null>(null);
  const runningRef = useRef(false);
  /** Un événement est arrivé pendant un passage : on enchaîne un second passage. */
  const rerunRef = useRef(false);

  const refresh = useCallback(async () => {
    setCounts(await outboxCounts());
  }, []);

  const scheduleRetry = useCallback(
    async (run: () => void) => {
      const delay = await nextAttemptDelay();
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      if (delay === null) {
        timerRef.current = null;
        return;
      }
      timerRef.current = window.setTimeout(run, Math.max(1_000, delay));
    },
    [],
  );

  const run = useCallback(async (): Promise<SyncSummary> => {
    const empty: SyncSummary = { attempted: 0, synced: 0, conflict: 0, failed: 0, skipped: 0 };

    // Un passage est déjà en cours (démarrage, retour de l'onglet…) : on demande un second
    // passage à la suite plutôt que d'ignorer l'événement. Sans cela, un retour de réseau
    // pendant une synchronisation en cours serait silencieusement perdu — inacceptable pour
    // une application de terrain.
    if (runningRef.current) {
      rerunRef.current = true;
      return empty;
    }

    runningRef.current = true;
    setSyncing(true);
    let summary: SyncSummary = empty;
    try {
      do {
        rerunRef.current = false;
        const pass = await runQueue(syncDeps);
        summary = {
          attempted: summary.attempted + pass.attempted,
          synced: summary.synced + pass.synced,
          conflict: summary.conflict + pass.conflict,
          failed: summary.failed + pass.failed,
          skipped: pass.skipped,
        };
        if (pass.attempted > 0) setLastSyncAt(Date.now());
        await refresh();
        // Le drapeau n'est consulté qu'ici : un événement arrivé pendant ce passage déclenche
        // un nouveau tour complet, jamais un tour perdu.
      } while (rerunRef.current);
    } finally {
      runningRef.current = false;
      setSyncing(false);
    }

    // Toute demande arrivée pendant la clôture (rafraîchissement, mise en file) est honorée
    // maintenant que le verrou est libéré : le dernier événement ne peut pas être avalé.
    if (rerunRef.current) {
      rerunRef.current = false;
      const followUp = await run();
      return { ...summary, attempted: summary.attempted + followUp.attempted };
    }

    setLastSummary(summary);
    await scheduleRetry(() => void run());
    return summary;
  }, [refresh, scheduleRetry]);

  const syncNow = useCallback(async () => {
    const summary = await run();
    await refresh();
    return summary;
  }, [run, refresh]);

  // Compteurs temps réel : la file notifie à chaque changement d'état.
  useEffect(() => {
    void refresh();
    return subscribe(() => {
      void refresh();
    });
  }, [refresh]);

  // Reprise automatique : au démarrage, au retour du réseau et au retour sur l'onglet.
  useEffect(() => {
    void run();
    const handleOnline = () => {
      setOnline(true);
      void run();
    };
    const handleOffline = () => setOnline(false);
    const handleVisible = () => {
      if (document.visibilityState === "visible") void run();
    };
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    document.addEventListener("visibilitychange", handleVisible);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      document.removeEventListener("visibilitychange", handleVisible);
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [run]);

  const value = useMemo<SyncContextValue>(
    () => ({
      counts,
      online,
      persistent: isPersistent(),
      syncing,
      lastSummary,
      lastSyncAt,
      syncNow,
      refresh,
    }),
    [counts, online, syncing, lastSummary, lastSyncAt, syncNow, refresh],
  );

  return <SyncContext.Provider value={value}>{children}</SyncContext.Provider>;
}

export function useSync(): SyncContextValue {
  const context = useContext(SyncContext);
  if (!context) {
    throw new Error("useSync doit être utilisé dans <SyncProvider>.");
  }
  return context;
}
