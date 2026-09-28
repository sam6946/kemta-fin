/**
 * Écran « Synchronisation » (MVP-009) : ce que l'appareil garde encore pour lui.
 *
 * L'utilisateur de terrain doit pouvoir répondre à trois questions sans être technicien :
 * qu'est-ce qui n'est pas encore parti, qu'est-ce qui a échoué, et que puis-je faire ?
 * Chaque opération affiche donc son type, son projet, son état, son motif d'échec et le
 * nombre de tentatives — avec les actions « relancer », « relancer tout » et « abandonner ».
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { syncApi, type SyncServerStatus } from "../api/sync";
import { Alert, Button } from "../components/ui";
import { isPersistent } from "../lib/db";
import {
  discardOperation,
  listOperations,
  retryAll,
  retryOperation,
  subscribe,
} from "../lib/outbox";
import {
  OUTBOX_STATUS_LABELS,
  OUTBOX_STATUS_TONES,
  type OutboxOperation,
} from "../lib/outboxTypes";
import { useSync } from "../sync/SyncProvider";

export default function SyncPage() {
  const { counts, online, syncing, syncNow, refresh, lastSummary, lastSyncAt } = useSync();
  const [operations, setOperations] = useState<OutboxOperation[]>([]);
  const [server, setServer] = useState<SyncServerStatus | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const load = useCallback(async () => {
    setOperations(await listOperations());
    await refresh();
  }, [refresh]);

  useEffect(() => {
    // Rafraîchissement événementiel : la file prévient à chaque changement d'état,
    // aucun polling (contrainte produit : pas de boucle < 30 s).
    void load();
    return subscribe(() => {
      void load();
    });
  }, [load]);

  useEffect(() => {
    syncApi
      .status()
      .then(setServer)
      .catch(() => setServer(null)); // l'état serveur est un complément, jamais un blocage
  }, [lastSyncAt]);

  async function handleRetry(opId: string) {
    await retryOperation(opId);
    setFeedback("Opération remise en file : elle repartira au prochain essai.");
    await load();
    await syncNow();
  }

  async function handleRetryAll() {
    const retried = await retryAll();
    setFeedback(
      retried > 0
        ? `${retried} opération(s) remise(s) en file.`
        : "Aucune opération en erreur à relancer.",
    );
    await load();
    await syncNow();
  }

  async function handleDiscard(opId: string, label: string) {
    await discardOperation(opId);
    setFeedback(`Opération abandonnée : « ${label} » ne sera plus envoyée.`);
    await load();
  }

  const waiting = operations.filter((operation) => operation.status !== "SYNCED");

  return (
    <div className="page">
      <header className="page-header">
        <h1>Synchronisation</h1>
        <Link to="/tableau-de-bord">Tableau de bord</Link>
      </header>

      <p className="field-hint" data-testid="sync-summary">
        {online ? "Connecté" : "Hors ligne — le travail continue, les envois partiront au retour du réseau"}
        {" · "}
        En attente : {counts.pending + counts.uploading} · Échecs : {counts.failed} · Conflits :{" "}
        {counts.conflict} · Envoyées : {counts.synced}
      </p>

      {!isPersistent() ? (
        <Alert tone="warning">
          Le stockage local du navigateur est indisponible : la file fonctionne, mais elle sera
          perdue si l'application est fermée. Ne fermez pas l'onglet avant la synchronisation.
        </Alert>
      ) : null}

      {feedback ? <Alert tone="info">{feedback}</Alert> : null}

      {lastSummary && lastSummary.attempted > 0 ? (
        <p className="field-hint" data-testid="sync-last-run">
          Dernier passage : {lastSummary.synced} envoyée(s), {lastSummary.conflict} conflit(s),{" "}
          {lastSummary.failed} échec(s).
        </p>
      ) : null}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <Button onClick={() => void syncNow()} loading={syncing} data-testid="sync-now">
          Synchroniser maintenant
        </Button>
        <Button variant="ghost" onClick={() => void handleRetryAll()}>
          Tout relancer
        </Button>
      </div>

      <h2 style={{ fontSize: "1rem" }}>Éléments à synchroniser</h2>
      {waiting.length === 0 ? (
        <p className="field-hint" data-testid="sync-empty">
          Rien en attente : tout ce qui a été fait sur le terrain est arrivé sur le serveur.
        </p>
      ) : (
        <ul className="sync-list" data-testid="sync-list">
          {waiting.map((operation) => (
            <li key={operation.opId} data-testid="sync-item">
              <div>
                <span className={`evidence-status status-${OUTBOX_STATUS_TONES[operation.status]}`}>
                  {OUTBOX_STATUS_LABELS[operation.status]}
                </span>{" "}
                <strong>{operation.label}</strong>
              </div>
              <div className="field-hint">
                Projet #{operation.projectId} · essais : {operation.attempts} ·{" "}
                {operation.fileMeta
                  ? `${operation.fileMeta.width}×${operation.fileMeta.height}`
                  : "sans fichier"}
                {operation.lastError ? ` · ${operation.lastError}` : ""}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => void handleRetry(operation.opId)}
                >
                  Relancer
                </button>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => void handleDiscard(operation.opId, operation.label)}
                >
                  Abandonner
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {server ? (
        <p className="field-hint" data-testid="sync-server">
          Serveur : {server.applied} opération(s) appliquée(s)
          {server.in_progress > 0 ? `, ${server.in_progress} en cours de traitement` : ""} · types
          acceptés : {server.supported_operations.length} (+ fichiers à part).
        </p>
      ) : null}
    </div>
  );
}
