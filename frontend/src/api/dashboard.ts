/** Dashboard agrégé et espace de travail — contrat `docs/api-contract.md` §11. */

import { request } from "./client";
import type { ProjectPermissions, ProjectStatus } from "./projects";

export type Audience = "manager" | "engineer" | "field" | "investor";
export type AlertSeverity = "critical" | "warning" | "info";

export type DashboardAlert = {
  code: string;
  severity: AlertSeverity;
  message: string;
  entity_type?: string;
  entity_id?: number;
  days_late?: number;
};

export type DashboardMilestone = {
  id: number;
  title: string;
  status: string;
  status_label: string;
  planned_date: string | null;
  actual_date: string | null;
  days_late: number;
};

export type DashboardBudget = {
  planned: number;
  committed: number;
  paid: number;
  outstanding: number;
  balance: number;
  consumption_rate: number;
  threshold: "OK" | "WARNING" | "EXCEEDED";
  currency: "XAF";
};

export type DashboardEvidence = {
  id: number;
  status: string;
  status_label: string;
  captured_at: string;
  author: { id: number; name: string } | null;
  task_title: string | null;
  description: string;
  thumbnail_url: string;
};

export type DashboardExpense = {
  id: number;
  title: string;
  amount: number;
  status: string;
  status_label: string;
  incurred_on: string;
  supplier: string;
};

export type DashboardActivity = {
  id: number;
  action: string;
  action_label: string;
  actor: { id: number; name: string } | null;
  entity_type: string;
  entity_id: string;
  created_at: string;
};

export type ProjectDashboard = {
  reference_date: string;
  generated_at: string;
  audience: Audience;
  role: string | null;
  project: {
    id: number;
    name: string;
    code: string;
    status: ProjectStatus;
    status_label: string;
    city: string;
    region: string;
    currency: "XAF";
    planned_start_date: string | null;
    planned_end_date: string | null;
    days_to_end: number | null;
  };
  progress: {
    progress: number;
    milestones_total: number;
    milestones_done: number;
    milestones_late: number;
    tasks_total: number;
    tasks_done: number;
    tasks_late: number;
  };
  milestones: { last_completed: DashboardMilestone | null; next: DashboardMilestone | null };
  budget: DashboardBudget | null;
  alerts: DashboardAlert[];
  alerts_total: number;
  evidences: { counts: Record<string, number>; latest: DashboardEvidence[] };
  expenses: { counts: Record<string, number>; latest: DashboardExpense[] } | null;
  activity: DashboardActivity[] | null;
  permissions: ProjectPermissions & Record<string, boolean>;
};

export type WorkspaceProject = {
  id: number;
  name: string;
  code: string;
  organization_name: string;
  status: ProjectStatus;
  status_label: string;
  progress: number;
  role: string | null;
  tasks_late: number;
  milestones_late: number;
  evidences_to_validate: number;
  expenses_to_approve: number;
  planned_end_date: string | null;
  budget_visible: boolean;
};

export type WorkspaceTask = {
  id: number;
  title: string;
  project: number;
  project_name: string;
  status: string;
  status_label: string;
  progress: string | number;
  planned_end_date: string | null;
  days_late: number;
};

export type Workspace = {
  reference_date: string;
  generated_at: string;
  profile: Audience | "none";
  totals: {
    projects: number;
    tasks_late: number;
    milestones_late: number;
    evidences_to_validate: number;
    expenses_to_approve: number;
    my_open_tasks: number;
  };
  projects: WorkspaceProject[];
  projects_truncated: boolean;
  my_tasks: WorkspaceTask[];
  to_validate: {
    count: number;
    items: Array<{
      id: number;
      project: number;
      project_name: string;
      captured_at: string;
      author: { id: number; name: string } | null;
      thumbnail_url: string;
    }>;
  };
  to_approve: {
    count: number;
    items: Array<{ id: number; project: number; project_name: string; title: string; amount: number }>;
  };
  unread_notifications: number;
};

export const dashboardApi = {
  project: (projectId: number | string) =>
    request<ProjectDashboard>(`/projects/${projectId}/dashboard/`, { auth: true }),
  workspace: () => request<Workspace>("/workspace/", { auth: true }),
};
