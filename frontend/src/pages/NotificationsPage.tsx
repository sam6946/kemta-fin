/** Centre de notifications (MVP-014) : liste paginée, lecture individuelle ou globale. */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { notificationsApi, type AppNotification } from "../api/notifications";
import { NOTIFICATIONS_CHANGED } from "../components/NotificationBell";
import { Alert, Button } from "../components/ui";
import { formatDate } from "../lib/format";
import { errorMessage } from "../lib/useAsync";

export default function NotificationsPage() {
  const [items, setItems] = useState<AppNotification[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [unread, setUnread] = useState(0);
  const [onlyUnread, setOnlyUnread] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const load = useCallback(
    async (target: number, replace: boolean) => {
      setLoading(true);
      setError(null);
      try {
        const data = await notificationsApi.list({ page: target, unread: onlyUnread });
        setItems((current) => (replace ? data.results : [...current, ...data.results]));
        setHasMore(Boolean(data.next));
        setUnread(data.unread_count);
        setPage(target);
      } catch (caught) {
        setError(caught instanceof ApiError ? caught : new ApiError("server_error", "Erreur", 500));
      } finally {
        setLoading(false);
      }
    },
    [onlyUnread],
  );

  useEffect(() => {
    void load(1, true);
  }, [load]);

  async function markRead(notification: AppNotification) {
    if (notification.is_read) return;
    try {
      const updated = await notificationsApi.markRead(notification.id);
      setItems((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setUnread((value) => Math.max(0, value - 1));
      window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError("server_error", "Erreur", 500));
    }
  }

  async function markAll() {
    try {
      await notificationsApi.markAllRead();
      await load(1, true);
      window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError("server_error", "Erreur", 500));
    }
  }

  return (
    <div className="dashboard">
      <section className="card">
        <div className="section-head">
          <h1 style={{ margin: 0 }}>Notifications</h1>
          <Button variant="ghost" onClick={() => void markAll()} disabled={unread === 0}>
            Tout marquer comme lu
          </Button>
        </div>
        <p className="field-hint">
          {unread > 0 ? `${unread} non lue(s). ` : "Tout est lu. "}
          Les événements similaires non lus sont regroupés en une seule notification.
        </p>
        <label className="field-hint">
          <input type="checkbox" checked={onlyUnread} onChange={(event) => setOnlyUnread(event.target.checked)} />{" "}
          Afficher seulement les non lues
        </label>

        {error ? (
          <Alert tone={error.isOffline ? "warning" : "error"}>
            {errorMessage(error)}{" "}
            <button type="button" className="link-button" onClick={() => void load(1, true)}>
              Réessayer
            </button>
          </Alert>
        ) : null}

        {loading && items.length === 0 ? (
          <div className="skeleton" aria-busy="true" data-testid="notifications-loading" />
        ) : null}

        {!loading && !error && items.length === 0 ? (
          <p className="field-hint" data-testid="notifications-empty">
            {onlyUnread ? "Aucune notification non lue." : "Vous n'avez encore reçu aucune notification."}
          </p>
        ) : null}

        <ul className="notification-list" data-testid="notification-list">
          {items.map((item) => (
            <li key={item.id} className={item.is_read ? "read" : "unread"} data-testid="notification">
              <div>
                <strong>{item.title}</strong>
                {item.count > 1 ? <span className="badge-pending"> ×{item.count}</span> : null}
                {item.body ? <div className="field-hint">{item.body}</div> : null}
                <div className="field-hint">
                  {formatDate(item.last_event_at)}
                  {item.project ? (
                    <>
                      {" · "}
                      <Link to={`/projets/${item.project}`} onClick={() => void markRead(item)}>
                        Ouvrir le projet
                      </Link>
                    </>
                  ) : null}
                </div>
              </div>
              {!item.is_read ? (
                <button type="button" className="link-button" onClick={() => void markRead(item)}>
                  Marquer comme lu
                </button>
              ) : null}
            </li>
          ))}
        </ul>

        {hasMore ? (
          <Button variant="ghost" onClick={() => void load(page + 1, false)} loading={loading}>
            Charger plus
          </Button>
        ) : null}
      </section>
    </div>
  );
}
