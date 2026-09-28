/** Notifications in-app — contrat `docs/api-contract.md` §12. */

import { request } from "./client";
import type { Paginated } from "./organizations";

export type AppNotification = {
  id: number;
  event_type: string;
  title: string;
  body: string;
  count: number;
  project: number | null;
  is_read: boolean;
  read_at: string | null;
  last_event_at: string;
  created_at: string;
  items: Array<{ entity_type: string; entity_id: string }>;
};

export type NotificationPage = Paginated<AppNotification> & { unread_count: number };

export const notificationsApi = {
  list: (params: { page?: number; unread?: boolean } = {}) => {
    const query = new URLSearchParams();
    if (params.page) query.set("page", String(params.page));
    if (params.unread) query.set("unread", "1");
    const suffix = query.toString() ? `?${query}` : "";
    return request<NotificationPage>(`/notifications/${suffix}`, { auth: true });
  },
  unreadCount: () => request<{ unread_count: number }>("/notifications/unread-count/", { auth: true }),
  markRead: (id: number) =>
    request<AppNotification>(`/notifications/${id}/read/`, { method: "POST", auth: true }),
  markAllRead: () =>
    request<{ marked: number; unread_count: number }>("/notifications/read-all/", {
      method: "POST",
      auth: true,
    }),
};
