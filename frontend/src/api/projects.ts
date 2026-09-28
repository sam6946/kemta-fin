/** Appels projets et membres — contrat `docs/api-contract.md` §4. */

import { request } from "./client";
import type { User } from "./auth";
import type { Paginated } from "./organizations";

export type ProjectStatus = "DRAFT" | "ACTIVE" | "ON_HOLD" | "COMPLETED" | "ARCHIVED";

export type ProjectPermissions = {
  edit_project: boolean;
  archive_project: boolean;
  manage_members: boolean;
  /** Planifier : créer/modifier jalons et tâches (Phase 4). */
  manage_schedule: boolean;
  /** Faire avancer une tâche dont on est le responsable désigné (Phase 4). */
  update_task: boolean;
  capture_evidence: boolean;
  validate_evidence: boolean;
  view_finance: boolean;
  manage_finance: boolean;
  view_activity: boolean;
};

export type Project = {
  id: number;
  organization: number;
  organization_name: string;
  name: string;
  code: string;
  description: string;
  location_label: string;
  city: string;
  region: string;
  latitude: string | null;
  longitude: string | null;
  geofence_radius_m: number;
  currency: "XAF";
  budget_total: number;
  status: ProjectStatus;
  status_label: string;
  progress: string;
  planned_start_date: string | null;
  planned_end_date: string | null;
  actual_start_date: string | null;
  actual_end_date: string | null;
  created_by: User;
  member_count: number;
  permissions: ProjectPermissions;
  created_at: string;
  updated_at: string;
};

export type ProjectMember = {
  id: number;
  user: User;
  role: string;
  role_label: string;
  can_validate_evidence: boolean;
  can_manage_finance: boolean;
  is_active: boolean;
  created_at: string;
};

export const PROJECT_STATUS_LABELS: Record<ProjectStatus, string> = {
  DRAFT: "Brouillon",
  ACTIVE: "En cours",
  ON_HOLD: "Suspendu",
  COMPLETED: "Terminé",
  ARCHIVED: "Archivé",
};

export const projectsApi = {
  list: (params: { status?: string; search?: string; page?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.status) query.set("status", params.status);
    if (params.search) query.set("search", params.search);
    if (params.page) query.set("page", String(params.page));
    const suffix = query.toString() ? `?${query}` : "";
    return request<Paginated<Project>>(`/projects/${suffix}`, { auth: true });
  },

  get: (id: number | string) => request<Project>(`/projects/${id}/`, { auth: true }),

  create: (payload: {
    organization: number;
    name: string;
    code?: string;
    description?: string;
    location_label?: string;
    city?: string;
    region?: string;
    budget_total: number;
    status: ProjectStatus;
    planned_start_date?: string | null;
    planned_end_date?: string | null;
  }) => request<Project>("/projects/", { method: "POST", body: payload, auth: true }),

  update: (id: number | string, payload: Partial<Project>) =>
    request<Project>(`/projects/${id}/`, { method: "PATCH", body: payload, auth: true }),

  archive: (id: number | string) =>
    request<void>(`/projects/${id}/`, { method: "DELETE", auth: true }),

  listMembers: (id: number | string) =>
    request<Paginated<ProjectMember>>(`/projects/${id}/members/`, { auth: true }),

  addMember: (
    id: number | string,
    payload: {
      phone: string;
      role: string;
      can_validate_evidence?: boolean;
      can_manage_finance?: boolean;
    },
  ) =>
    request<ProjectMember>(`/projects/${id}/members/`, {
      method: "POST",
      body: payload,
      auth: true,
    }),

  updateMember: (
    id: number | string,
    memberId: number,
    payload: {
      phone: string;
      role: string;
      can_validate_evidence?: boolean;
      can_manage_finance?: boolean;
    },
  ) =>
    request<ProjectMember>(`/projects/${id}/members/${memberId}/`, {
      method: "PATCH",
      body: payload,
      auth: true,
    }),

  removeMember: (id: number | string, memberId: number) =>
    request<void>(`/projects/${id}/members/${memberId}/`, { method: "DELETE", auth: true }),
};
