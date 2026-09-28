/**
 * Journal d'activité d'un projet (MVP-012) — lecture seule, paginé, filtrable par famille.
 *
 * Affiché uniquement si le backend accorde `view_activity`. Aucune action d'écriture n'existe :
 * l'historique est immuable côté serveur (405 sur tout verbe d'écriture).
 */

import { useCallback, useEffect, useState } from "react";

import { activityApi, type ActivityEvent, type ActivityGroup } from "../api/activity";
import { ApiError } from "../api/client";
import { formatDate } from "../lib/format";
import { errorMessage } from "../lib/useAsync";
import { Alert, Button } from "./ui";

export default function ProjectActivity({ projectId }: { projectId: number | string }) {
  const [groups, setGroups] = useState<ActivityGroup[]>([]);
  const [group, setGroup] = useState("");
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    activityApi
      .meta()
      .then((meta) => setGroups(meta.groups))
      .catch(() => setGroups([])); // les filtres sont un confort : le journal reste lisible
  }, []);

  const load = useCallback(
    async (target: number, replace: boolean) => {
      setLoading(true);
      setError(null);
      try {
        const data = await activityApi.project(projectId, { page: target, group: group || undefined });
        setEvents((current) => (replace ? data.results : [...current, ...data.results]));
        setTotal(data.count);
        setHasMore(Boolean(data.next));
        setPage(target);
      } catch (caught) {
        setError(caught instanceof ApiError ? caught : new ApiError("server_error", "Erreur", 500));
      } finally {
        setLoading(false);
      }
    },
    [projectId, group],
  );

  useEffect(() => {
    void load(1, true);
  }, [load]);

  return (
    <section className="card" id="journal" aria-label="Journal d'activité" data-testid="project-activity">
      <h2 className="section-title">Journal d'activité</h2>
      <p className="field-hint">
        Historique en lecture seule des actions sensibles (qui, quoi, quand). Il ne peut être ni modifié ni
        supprimé.
      </p>
      <label className="field-hint" htmlFor="activity-group">
        Famille d'événements{" "}
      </label>
      <select id="activity-group" value={group} onChange={(event) => setGroup(event.target.value)}>
        <option value="">Toutes</option>
        {groups.map((item) => (
          <option key={item.code} value={item.code}>
            {item.label}
          </option>
        ))}
      </select>

      {error ? (
        <Alert tone={error.isOffline ? "warning" : "error"}>{errorMessage(error)}</Alert>
      ) : null}
      {loading && events.length === 0 ? <div className="skeleton" aria-busy="true" /> : null}
      {!loading && !error && events.length === 0 ? (
        <p className="field-hint" data-testid="activity-empty">
          Aucun événement pour ce filtre.
        </p>
      ) : null}

      <ul className="plain-list" data-testid="activity-list">
        {events.map((event) => (
          <li key={event.id}>
            <span>
              <strong>{event.action_label}</strong>
              <span className="field-hint">
                {event.actor ? ` · ${event.actor.name}` : " · système"}
                {event.entity_type ? ` · ${event.entity_type} #${event.entity_id}` : ""}
              </span>
            </span>
            <span className="field-hint">
              {new Date(event.created_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}
            </span>
          </li>
        ))}
      </ul>
      {events.length > 0 ? (
        <div className="field-hint">
          {events.length} sur {total} · dernier : {formatDate(events[0].created_at)}
        </div>
      ) : null}
      {hasMore ? (
        <Button variant="ghost" onClick={() => void load(page + 1, false)} loading={loading}>
          Charger plus
        </Button>
      ) : null}
    </section>
  );
}
