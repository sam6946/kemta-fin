/**
 * Preuves terrain du projet (MVP-007, MVP-008).
 *
 * Parcours terrain : choisir/prendre une photo → compression locale → position (avec statut
 * explicite) → envoi avec clé d'idempotence → statut visible immédiatement.
 *
 * Parcours de contrôle : galerie avec statuts, détail, historique des décisions, validation /
 * rejet / signalement. Les décisions autorisées viennent du backend (`permissions`), jamais
 * d'un calcul local ; une preuve sans statut n'est jamais présentée comme certaine.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import {
  EVIDENCE_STATUS_TONES,
  evidenceApi,
  type Evidence,
  type EvidenceAction,
  type EvidenceStatus,
  type EvidenceValidation,
} from "../api/evidences";
import type { Project } from "../api/projects";
import { messageForErrorCode } from "../auth/passwordPolicy";
import SyncBadge from "../components/SyncBadge";
import { Alert, Button, Field } from "../components/ui";
import { formatDate } from "../lib/format";
import {
  capturedAtNow,
  deviceInfo,
  formatBytes,
  getPosition,
  newIdempotencyKey,
  preparePhoto,
  type GeoResult,
} from "../lib/media";
import { enqueue, listPendingEvidence, retryOperation, subscribe } from "../lib/outbox";
import { isOnline } from "../lib/outbox";
import { OUTBOX_STATUS_LABELS, type OutboxOperation } from "../lib/outboxTypes";
import { useSync } from "../sync/SyncProvider";

type Props = {
  project: Project;
  onChanged?: () => Promise<void> | void;
};

const APP_VERSION = "0.5.0";

export default function ProjectEvidences({ project, onChanged }: Props) {
  const [evidences, setEvidences] = useState<Evidence[]>([]);
  const [counts, setCounts] = useState({ pending: 0, validated: 0, rejected: 0, flagged: 0 });
  const [statusFilter, setStatusFilter] = useState<EvidenceStatus | "">("");
  const [selected, setSelected] = useState<Evidence | null>(null);
  const [history, setHistory] = useState<EvidenceValidation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [pendingComments, setPendingComments] = useState<Record<number, string>>({});

  // État de la capture en cours (visible à chaque étape, jamais muet).
  const [description, setDescription] = useState("");
  const [geo, setGeo] = useState<GeoResult | null>(null);
  const [locating, setLocating] = useState(false);
  const [prepared, setPrepared] = useState<{
    blob: Blob;
    hash: string;
    width: number;
    height: number;
    originalBytes: number;
    compressedBytes: number;
    previewUrl: string;
  } | null>(null);
  const [preparing, setPreparing] = useState(false);
  // Preuves encore sur l'appareil (file d'attente hors ligne, MVP-009).
  const [localOperations, setLocalOperations] = useState<OutboxOperation[]>([]);
  const idempotencyRef = useRef<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const { online, syncNow } = useSync();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const query = statusFilter ? `?status=${statusFilter}` : "";
      const page = await evidenceApi.list(project.id, query);
      setEvidences(page.results);
      setCounts(page.counts);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? messageForErrorCode(caught.code)
          : messageForErrorCode("server_error"),
      );
    } finally {
      setLoading(false);
    }
  }, [project.id, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadLocal = useCallback(async () => {
    setLocalOperations(await listPendingEvidence(project.id));
  }, [project.id]);

  useEffect(() => {
    // La galerie locale suit la file : dès qu'une preuve part, elle devient une carte serveur.
    void loadLocal();
    return subscribe(() => {
      void loadLocal();
      void load();
    });
  }, [loadLocal, load]);

  async function onFilePicked(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setError(null);
    setFeedback(null);
    setPreparing(true);
    try {
      const ready = await preparePhoto(file);
      // Nouvelle tentative = nouvelle clé : le rejeu n'est réutilisé que pour un envoi réessayé.
      idempotencyRef.current = newIdempotencyKey();
      setPrepared({ ...ready, previewUrl: URL.createObjectURL(ready.blob) });
      setGeo(null);
      setFeedback(
        `Photo compressée : ${formatBytes(ready.originalBytes)} → ${formatBytes(
          ready.compressedBytes,
        )} (${ready.width}×${ready.height}).`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : messageForErrorCode("server_error"));
    } finally {
      setPreparing(false);
    }
  }

  async function locate() {
    setLocating(true);
    setError(null);
    try {
      const result = await getPosition();
      setGeo(result);
      setFeedback(result.message);
    } finally {
      setLocating(false);
    }
  }

  /** Met la capture en file locale : la photo ne dépend plus du réseau (MVP-009). */
  async function saveOffline(options: { reason: string }) {
    if (!prepared) return;
    const device = deviceInfo();
    const operation = await enqueue({
      type: "EVIDENCE_UPLOAD",
      projectId: project.id,
      label: description ? `Preuve · ${description}` : "Preuve terrain",
      idempotencyKey: idempotencyRef.current ?? newIdempotencyKey(),
      payload: {
        captured_at: capturedAtNow(),
        latitude: geo?.latitude ?? null,
        longitude: geo?.longitude ?? null,
        gps_accuracy: geo?.accuracy ?? null,
        gps_status: geo?.status ?? "UNAVAILABLE",
        device_model: device.device_model,
        device_platform: device.device_platform,
        app_version: APP_VERSION,
        description,
      },
      file: prepared.blob,
      fileMeta: {
        name: `preuve-${project.id}.jpg`,
        type: prepared.blob.type || "image/jpeg",
        hash: prepared.hash,
        width: prepared.width,
        height: prepared.height,
        bytes: prepared.compressedBytes,
        originalBytes: prepared.originalBytes,
      },
    });
    setFeedback(
      `${options.reason} La preuve est conservée sur l'appareil (${formatBytes(
        prepared.compressedBytes,
      )}) et partira automatiquement dès que le réseau reviendra.`,
    );
    resetCapture();
    await loadLocal();
    if (isOnline()) void syncNow();
    return operation;
  }

  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!prepared || busy) return;
    if (!idempotencyRef.current) idempotencyRef.current = newIdempotencyKey();

    // Hors ligne : inutile d'attendre un échec réseau, on met directement en file.
    if (!online) {
      await saveOffline({ reason: "Aucune connexion pour le moment." });
      return;
    }

    setBusy(true);
    setError(null);
    setFeedback(null);
    const device = deviceInfo();
    try {
      const created = await evidenceApi.upload(
        {
          project: project.id,
          file: prepared.blob,
          captured_at: capturedAtNow(),
          latitude: geo?.latitude ?? null,
          longitude: geo?.longitude ?? null,
          gps_accuracy: geo?.accuracy ?? null,
          gps_status: geo?.status ?? "UNAVAILABLE",
          device_model: device.device_model,
          device_platform: device.device_platform,
          app_version: APP_VERSION,
          description,
        },
        idempotencyRef.current,
      );
      setFeedback(
        geo?.status === "AVAILABLE"
          ? "Preuve envoyée avec sa position : elle est en attente de validation."
          : "Preuve envoyée sans position GPS : elle est en attente de validation.",
      );
      resetCapture();
      await load();
      await onChanged?.();
      setSelected(created);
    } catch (caught) {
      if (caught instanceof ApiError && caught.isOffline) {
        // Coupure pendant l'envoi : la même clé d'idempotence est conservée dans la file.
        await saveOffline({ reason: "La connexion a été coupée pendant l'envoi." });
      } else if (caught instanceof ApiError) {
        setError(describeUploadError(caught));
      } else {
        setError(messageForErrorCode("server_error"));
      }
    } finally {
      setBusy(false);
    }
  }

  function resetCapture() {
    setPrepared(null);
    setDescription("");
    setGeo(null);
    idempotencyRef.current = null;
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handleLocalRetry(opId: string) {
    await retryOperation(opId);
    setFeedback("Preuve remise en file : nouvel essai en cours.");
    await syncNow();
    await loadLocal();
  }

  async function openDetail(evidence: Evidence) {
    setSelected(evidence);
    setHistory([]);
    try {
      const payload = await evidenceApi.history(evidence.id);
      setHistory(payload.results);
    } catch {
      // L'historique est un complément : son échec ne doit pas masquer la preuve.
      setHistory([]);
    }
  }

  async function decide(evidence: Evidence, action: EvidenceAction) {
    const comment = (pendingComments[evidence.id] ?? "").trim();
    if ((action === "REJECT" || action === "FLAG") && !comment) {
      setError("Un commentaire est obligatoire pour un rejet ou un signalement.");
      return;
    }
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      const updated = await evidenceApi.transition(evidence.id, action, comment);
      setFeedback(
        action === "VALIDATE"
          ? "Preuve validée : la décision est tracée dans l'historique."
          : action === "REJECT"
            ? "Preuve rejetée : l'auteur peut la consulter avec son motif."
            : action === "FLAG"
              ? "Preuve signalée pour vérification."
              : "Preuve renvoyée en attente de validation.",
      );
      setPendingComments((current) => ({ ...current, [evidence.id]: "" }));
      setSelected(updated);
      await load();
      await openDetail(updated);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? messageForErrorCode(caught.code)
          : messageForErrorCode("server_error"),
      );
    } finally {
      setBusy(false);
    }
  }

  const canCapture =
    project.permissions.capture_evidence &&
    (project.status === "ACTIVE" || project.status === "ON_HOLD" || project.status === "DRAFT");

  return (
    <section className="card" id="preuves" data-testid="evidences">
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <h2 style={{ fontSize: "1rem", margin: 0 }}>
          Preuves terrain ({counts.pending + counts.validated + counts.rejected + counts.flagged})
        </h2>
        <SyncBadge />
      </div>

      {error ? <Alert tone="error">{error}</Alert> : null}
      {feedback ? <Alert tone="success">{feedback}</Alert> : null}

      <p className="field-hint">
        À valider : {counts.pending} · Validées : {counts.validated} · Rejetées : {counts.rejected} ·
        Signalées : {counts.flagged}
      </p>

      {canCapture ? (
        <form onSubmit={upload} noValidate data-testid="capture-form">
          <Field label="Photo du chantier" hint="Prise sur le terrain ou choisie dans la galerie du téléphone">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              onChange={(event) => void onFilePicked(event)}
            />
          </Field>

          {preparing ? <p className="field-hint">Compression de la photo…</p> : null}

          {prepared ? (
            <div className="evidence-preview" data-testid="capture-preview">
              <img src={prepared.previewUrl} alt="Aperçu de la preuve" width={160} />
              <div className="field-hint">
                {prepared.width}×{prepared.height} · {formatBytes(prepared.compressedBytes)} (avant
                compression : {formatBytes(prepared.originalBytes)})
                <br />
                Empreinte : {prepared.hash.slice(0, 16)}…
              </div>
            </div>
          ) : null}

          <Field label="Description (facultatif)">
            <input
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Ex. : ferraillage avant coulage"
            />
          </Field>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <Button type="button" variant="ghost" onClick={() => void locate()} loading={locating}>
              {geo ? "Actualiser la position" : "Obtenir ma position"}
            </Button>
            {geo ? (
              <span className={geo.status === "AVAILABLE" ? "field-hint" : "badge-late"}>
                {geo.message}
              </span>
            ) : (
              <span className="field-hint">
                Position non demandée : la preuve peut être déposée sans GPS.
              </span>
            )}
          </div>

          <p className="field-hint">
            {online
              ? "En cas de coupure réseau, la preuve est gardée sur l'appareil et renvoyée " +
                "automatiquement avec la même clé : aucun doublon."
              : "Hors ligne : la preuve sera conservée sur l'appareil et envoyée automatiquement " +
                "au retour du réseau."}
          </p>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Button type="submit" loading={busy} disabled={!prepared} data-testid="capture-submit">
              Envoyer la preuve
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={!prepared}
              onClick={() => void saveOffline({ reason: "Enregistrement local demandé." })}
              data-testid="capture-offline"
            >
              Enregistrer hors ligne
            </Button>
          </div>
        </form>
      ) : (
        <Alert tone="info">
          Votre rôle sur ce projet ne permet pas de déposer une preuve.
        </Alert>
      )}

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12 }}>
        <label htmlFor="evidence-status-filter" className="field-hint">
          Filtrer par statut
        </label>
        <select
          id="evidence-status-filter"
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value as EvidenceStatus | "")}
        >
          <option value="">Tous les statuts</option>
          <option value="PENDING">En attente</option>
          <option value="VALIDATED">Validées</option>
          <option value="REJECTED">Rejetées</option>
          <option value="FLAGGED">Signalées</option>
        </select>
      </div>

      {loading ? <p className="field-hint">Chargement des preuves…</p> : null}

      {!loading && evidences.length === 0 ? (
        <p className="field-hint" data-testid="empty-gallery">
          Aucune preuve pour ce filtre. Les photos apparaissent ici dès leur dépôt, avec leur
          statut réel.
        </p>
      ) : null}

      {localOperations.length > 0 ? (
        <div data-testid="local-evidence-list">
          <p className="field-hint">
            Encore sur cet appareil (elles partiront automatiquement) :{" "}
            <button type="button" className="link-button" onClick={() => void syncNow()}>
              Synchroniser maintenant
            </button>
          </p>
          <ul className="evidence-grid">
            {localOperations.map((operation) => (
              <li key={operation.opId} className="evidence-local" data-testid="local-evidence">
                <div className={`evidence-status status-info`}>{OUTBOX_STATUS_LABELS[operation.status]}</div>
                <div className="field-hint">{operation.label}</div>
                <div className="field-hint">
                  {operation.fileMeta
                    ? `${operation.fileMeta.width}×${operation.fileMeta.height} · ${formatBytes(
                        operation.fileMeta.bytes,
                      )}`
                    : "sans fichier"}
                  {operation.attempts > 0 ? ` · ${operation.attempts} essai(s)` : ""}
                </div>
                {operation.lastError ? (
                  <div className="field-hint">{operation.lastError}</div>
                ) : null}
                <button
                  type="button"
                  className="link-button"
                  onClick={() => void handleLocalRetry(operation.opId)}
                >
                  Relancer maintenant
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <ul className="evidence-grid">
        {evidences.map((item) => (
          <li key={item.id} data-testid="evidence-card">
            <button type="button" className="evidence-thumb" onClick={() => void openDetail(item)}>
              <img src={item.thumbnail_url} alt={item.description || "Preuve terrain"} loading="lazy" />
            </button>
            <div className={`evidence-status status-${EVIDENCE_STATUS_TONES[item.status]}`}>
              {item.status_label}
            </div>
            <div className="field-hint">
              {formatDate(item.captured_at)} ·{" "}
              {item.author.first_name} {item.author.last_name}
            </div>
            <div className="field-hint">
              {item.gps_status === "AVAILABLE"
                ? `GPS ± ${Math.round(item.gps_accuracy ?? 0)} m${
                    item.inside_geofence === false ? " · hors périmètre" : ""
                  }`
                : item.gps_status_label}
              {item.distance_from_site_m !== null
                ? ` · ${Math.round(item.distance_from_site_m)} m du site`
                : ""}
            </div>
            {item.description ? <div className="field-hint">{item.description}</div> : null}
          </li>
        ))}
      </ul>

      {selected ? (
        <div className="card" data-testid="evidence-detail">
          <h3 style={{ fontSize: "0.95rem", marginTop: 0 }}>
            Preuve #{selected.id} · {selected.status_label}
          </h3>
          <p className="field-hint">
            {formatDate(selected.captured_at)} · reçue le {formatDate(selected.received_at)} ·{" "}
            {selected.device_model} ({selected.device_platform})
          </p>
          <img
            src={selected.file_url}
            alt={selected.description || "Preuve"}
            style={{ maxWidth: "100%", borderRadius: 8 }}
          />
          <p className="field-hint">
            Empreinte SHA-256 : <code>{selected.hash_sha256}</code>
          </p>
          <p className="field-hint">
            {selected.gps_status === "AVAILABLE" && selected.latitude ? (
              <>
                Position : {selected.latitude}, {selected.longitude}
                {selected.distance_from_site_m !== null
                  ? ` (${Math.round(selected.distance_from_site_m)} m du site)`
                  : ""}
              </>
            ) : (
              `Position : ${selected.gps_status_label}`
            )}
          </p>

          {selected.permissions.validate_evidence ? (
            <div>
              <Field label="Commentaire (obligatoire pour rejeter ou signaler)">
                <textarea
                  rows={2}
                  value={pendingComments[selected.id] ?? ""}
                  onChange={(event) =>
                    setPendingComments({ ...pendingComments, [selected.id]: event.target.value })
                  }
                />
              </Field>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <Button onClick={() => void decide(selected, "VALIDATE")} loading={busy}>
                  Valider
                </Button>
                <Button variant="ghost" onClick={() => void decide(selected, "REJECT")} loading={busy}>
                  Rejeter
                </Button>
                <Button variant="ghost" onClick={() => void decide(selected, "FLAG")} loading={busy}>
                  Signaler
                </Button>
                {selected.status !== "PENDING" ? (
                  <Button variant="ghost" onClick={() => void decide(selected, "REOPEN")} loading={busy}>
                    Rouvrir
                  </Button>
                ) : null}
              </div>
            </div>
          ) : selected.permissions.cannot_validate_own ? (
            <Alert tone="info">
              Vous êtes l'auteur de cette preuve : un autre validateur doit statuer.
            </Alert>
          ) : (
            <Alert tone="info">Consultation seule : votre rôle ne permet pas de valider.</Alert>
          )}

          <h4 style={{ fontSize: "0.9rem" }}>Historique des décisions</h4>
          {history.length === 0 ? (
            <p className="field-hint" data-testid="empty-history">
              Aucune décision enregistrée pour l'instant : la preuve reste en attente.
            </p>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 6 }}>
              {history.map((entry) => (
                <li key={entry.id} className="field-hint" data-testid="history-entry">
                  <strong>{entry.action_label}</strong> · {entry.from_status} → {entry.to_status} ·{" "}
                  {entry.actor.first_name} {entry.actor.last_name} · {formatDate(entry.created_at)}
                  {entry.comment ? ` — « ${entry.comment} »` : ""}
                </li>
              ))}
            </ul>
          )}

          <Button variant="ghost" onClick={() => setSelected(null)}>
            Fermer
          </Button>
        </div>
      ) : null}
    </section>
  );
}

/** Message d'erreur précis pour les cas terrain (le code vient du backend). */
function describeUploadError(error: ApiError): string {
  switch (error.code) {
    case "duplicate_evidence":
      return "Cette photo a déjà été déposée sur ce projet : elle apparaît dans la galerie.";
    case "evidence_out_of_geofence":
      return (
        "Position trop éloignée du chantier : reprenez la photo sur site, ou signalez " +
        "l'anomalie à votre responsable."
      );
    case "file_too_large":
      return "Photo trop lourde (10 Mo maximum). Reprenez la photo, elle sera recompressée.";
    case "unsupported_media_type":
      return "Format non accepté : envoyez une photo JPEG, PNG ou WebP.";
    case "image_dimensions_too_large":
      return "Photo trop grande (4000 px maximum sur le plus grand côté).";
    case "captured_at_in_future":
      return "L'heure du téléphone est en avance : corrigez-la puis réessayez.";
    case "offline":
      return "Pas de connexion : la preuve est conservée sur l'appareil et partira toute seule.";
    default:
      return messageForErrorCode(error.code);
  }
}
