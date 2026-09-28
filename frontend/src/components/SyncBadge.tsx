/** Badge de synchronisation : l'état de la file est visible depuis tous les écrans (MVP-009). */

import { Link } from "react-router-dom";

import { useSync } from "../sync/SyncProvider";

export default function SyncBadge() {
  const { counts, online, syncing, syncNow } = useSync();
  const waiting = counts.pending + counts.uploading;
  const problems = counts.failed + counts.conflict;

  const label = !online
    ? "Hors ligne"
    : syncing
      ? "Synchronisation…"
      : problems > 0
        ? `${problems} à vérifier`
        : waiting > 0
          ? `${waiting} en attente`
          : "À jour";

  const tone = !online
    ? "badge-offline"
    : problems > 0
      ? "badge-late"
      : waiting > 0
        ? "badge-pending"
        : "badge-ok";

  return (
    <span className="sync-badge" data-testid="sync-badge">
      <span className={`evidence-status ${tone}`} data-testid="sync-badge-state">
        {label}
      </span>
      {problems > 0 ? (
        <button
          type="button"
          className="link-button"
          onClick={() => void syncNow()}
          data-testid="sync-badge-retry"
        >
          Réessayer
        </button>
      ) : null}
      <Link to="/synchronisation">Suivi</Link>
    </span>
  );
}
