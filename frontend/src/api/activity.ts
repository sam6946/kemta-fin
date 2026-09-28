/** Journal d'activité (lecture seule) — contrat `docs/api-contract.md` §13. */

import { request } from "./client";
import type { Paginated } from "./organizations";

export type ActivityEvent = {
  id: number;
  action: string;
  action_label: string;
  group: string;
  actor: { id: number; name: string; role: string } | null;
  entity_type: string;
  entity_id: string;
  project: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
  ip_address?: string | null;
  user_agent?: string;
};

export type ActivityGroup = { code: string; label: string; actions: Array<{ code: string; label: string }> };

export const activityApi = {
  meta: () => request<{ groups: ActivityGroup[] }>("/activity/meta/", { auth: true }),
  project: (projectId: number | string, params: { page?: number; group?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.page) query.set("page", String(params.page));
    if (params.group) query.set("group", params.group);
    const suffix = query.toString() ? `?${query}` : "";
    return request<Paginated<ActivityEvent>>(`/projects/${projectId}/activity/${suffix}`, { auth: true });
  },
};
