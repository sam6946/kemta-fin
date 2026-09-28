/** Appels organisations — contrat `docs/api-contract.md` §4. */

import { request } from "./client";
import type { User } from "./auth";

export type Organization = {
  id: number;
  name: string;
  slug: string;
  type: string;
  type_label: string;
  country: string;
  city: string;
  address: string;
  contact_phone: string;
  owner: User;
  is_active: boolean;
  project_count: number;
  member_count: number;
  created_at: string;
  updated_at: string;
};

export type OrganizationMember = {
  id: number;
  user: User;
  role: string;
  role_label: string;
  is_active: boolean;
  created_at: string;
};

export type Paginated<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};

export const organizationsApi = {
  list: (params: { search?: string; page?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.search) query.set("search", params.search);
    if (params.page) query.set("page", String(params.page));
    const suffix = query.toString() ? `?${query}` : "";
    return request<Paginated<Organization>>(`/organizations/${suffix}`, { auth: true });
  },

  get: (id: number | string) => request<Organization>(`/organizations/${id}/`, { auth: true }),

  create: (payload: {
    name: string;
    type: string;
    city?: string;
    address?: string;
    contact_phone?: string;
  }) => request<Organization>("/organizations/", { method: "POST", body: payload, auth: true }),

  update: (id: number | string, payload: Partial<Organization>) =>
    request<Organization>(`/organizations/${id}/`, { method: "PATCH", body: payload, auth: true }),

  addMember: (id: number | string, payload: { phone: string; role: string }) =>
    request<OrganizationMember>(`/organizations/${id}/members/`, {
      method: "POST",
      body: payload,
      auth: true,
    }),

  listMembers: (id: number | string) =>
    request<Paginated<OrganizationMember>>(`/organizations/${id}/members/`, { auth: true }),
};
