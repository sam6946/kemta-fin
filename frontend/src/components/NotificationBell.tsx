/**
 * Cloche de notifications : pastille du nombre de notifications non lues.
 *
 * Aucun polling à intervalle fixe (exigence phase 8) : le compteur est rechargé au montage,
 * à chaque changement d'écran, quand l'onglet redevient visible et au retour du réseau.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { notificationsApi } from "../api/notifications";

export const NOTIFICATIONS_CHANGED = "kemta:notifications-changed";

export default function NotificationBell() {
  const [unread, setUnread] = useState<number | null>(null);
  const location = useLocation();
  const lastFetch = useRef(0);

  const refresh = useCallback(async (force = false) => {
    if (!force && Date.now() - lastFetch.current < 5_000) return;
    lastFetch.current = Date.now();
    try {
      const data = await notificationsApi.unreadCount();
      setUnread(data.unread_count);
    } catch {
      /* hors ligne ou session expirée : on garde la dernière valeur connue */
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, location.pathname]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const onChanged = () => void refresh(true);
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onChanged);
    window.addEventListener(NOTIFICATIONS_CHANGED, onChanged);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onChanged);
      window.removeEventListener(NOTIFICATIONS_CHANGED, onChanged);
    };
  }, [refresh]);

  const label =
    unread && unread > 0 ? `Notifications : ${unread} non lue(s)` : "Notifications : aucune nouvelle";

  return (
    <Link to="/notifications" className="bell" aria-label={label} title={label}>
      <span aria-hidden="true">🔔</span>
      {unread && unread > 0 ? (
        <span className="bell-badge" data-testid="bell-badge">
          {unread > 99 ? "99+" : unread}
        </span>
      ) : null}
    </Link>
  );
}
