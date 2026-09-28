/** Rôles exposés par le backend (`GET /api/meta/roles/`) — aucune liste en dur côté client. */

import { request } from "./client";

export type RoleMeta = { code: string; label: string; capabilities: string[] };

let cache: RoleMeta[] | null = null;

export async function fetchRoles(): Promise<RoleMeta[]> {
  if (cache) return cache;
  const response = await request<{ results: RoleMeta[] }>("/meta/roles/");
  cache = response.results;
  return cache;
}

export function resetRolesCache(): void {
  cache = null;
}
