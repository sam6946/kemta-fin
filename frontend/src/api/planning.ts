/** Appels planification : jalons, tâches, planning et retards — contrat `docs/api-contract.md` §5. */

import { request } from "./client";

export type MilestoneStatus = "PLANNED" | "IN_PROGRESS" | "DONE" | "BLOCKED" | "CANCELLED";
export type TaskStatus = "TODO" | "IN_PROGRESS" | "DONE" | "BLOCKED" | "CANCELLED";

export type PlanningTask = {
  id: number;
  title: string;
  status: TaskStatus;
  status_label: string;
  progress: string;
  is_late: boolean;
  days_late: number;
  planned_start_date: string | null;
  planned_end_date: string | null;
  actual_end_date: string | null;
  assignee: Task["assignee"];
  depends_on: number[];
};

export type Milestone = {
  id: number;
  project: number;
  title: string;
  description: string;
  status: MilestoneStatus;
  status_label: string;
  planned_date: string | null;
  actual_date: string | null;
  order: number;
  weight: string;
  is_late: boolean;
  days_late: number;
  progress: number;
  task_total: number;
  task_done: number;
  /** Tâches du jalon : renvoyées imbriquées par `GET /schedule/`. */
  tasks?: PlanningTask[];
  created_at: string;
  updated_at: string;
};

export type Task = {
  id: number;
  project: number;
  milestone: number | null;
  milestone_title: string | null;
  title: string;
  description: string;
  status: TaskStatus;
  status_label: string;
  planned_start_date: string | null;
  planned_end_date: string | null;
  actual_start_date: string | null;
  actual_end_date: string | null;
  progress: string;
  weight: string;
  assignee: { id: number; first_name: string; last_name: string; phone_masked: string } | null;
  depends_on: number[];
  is_late: boolean;
  days_late: number;
  created_at: string;
  updated_at: string;
};

export type ScheduleAlert = {
  type: "task_late" | "milestone_late";
  id: number;
  title: string;
  days_late: number;
};

export type Schedule = {
  project: {
    id: number;
    name: string;
    status: string;
    progress: number;
    planned_start_date: string | null;
    planned_end_date: string | null;
  };
  milestones: Milestone[];
  orphan_tasks: Task[];
  summary: {
    milestones_total: number;
    milestones_done: number;
    tasks_total: number;
    tasks_done: number;
    tasks_late: number;
    milestones_late: number;
    names_late: string[];
  };
  alerts: ScheduleAlert[];
};

export type MilestoneInput = {
  title: string;
  description?: string;
  status?: MilestoneStatus;
  planned_date?: string | null;
  actual_date?: string | null;
  order?: number;
  weight?: number;
};

export type TaskInput = {
  title: string;
  description?: string;
  milestone?: number | null;
  status?: TaskStatus;
  planned_start_date?: string | null;
  planned_end_date?: string | null;
  actual_start_date?: string | null;
  actual_end_date?: string | null;
  progress?: number;
  weight?: number;
  assignee_id?: number | null;
};

type Collection<T> = { count: number; results: T[] };

/** Statuts officiels : servis par le backend (`GET /api/meta/status/`), jamais codés en dur. */
export type StatusMeta = { value: string; label: string };
export type StatusPayload = {
  project: StatusMeta[];
  milestone: StatusMeta[];
  task: StatusMeta[];
};

let statusCache: StatusPayload | null = null;

export async function fetchStatuses(): Promise<StatusPayload> {
  if (!statusCache) {
    statusCache = await request<StatusPayload>("/meta/status/", { auth: true });
  }
  return statusCache;
}

export function resetStatusCache(): void {
  statusCache = null;
}

export const planningApi = {
  schedule: (projectId: string | number) =>
    request<Schedule>(`/projects/${projectId}/schedule/`, { auth: true }),

  delays: (projectId: string | number) =>
    request<{
      project: number;
      reference_date: string;
      tasks: { id: number; title: string; days_late: number; planned_end_date: string }[];
      milestones: { id: number; title: string; days_late: number; planned_date: string }[];
      progress: number;
    }>(`/projects/${projectId}/delays/`, { auth: true }),

  milestones: (projectId: string | number) =>
    request<Collection<Milestone>>(`/projects/${projectId}/milestones/`, { auth: true }),

  createMilestone: (projectId: string | number, payload: MilestoneInput) =>
    request<Milestone>(`/projects/${projectId}/milestones/`, {
      method: "POST",
      auth: true,
      body: payload,
    }),

  updateMilestone: (milestoneId: number, payload: Partial<MilestoneInput>) =>
    request<Milestone>(`/milestones/${milestoneId}/`, {
      method: "PATCH",
      auth: true,
      body: payload,
    }),

  deleteMilestone: (milestoneId: number) =>
    request<void>(`/milestones/${milestoneId}/`, { method: "DELETE", auth: true }),

  tasks: (projectId: string | number, query = "") =>
    request<Collection<Task>>(`/projects/${projectId}/tasks/${query}`, { auth: true }),

  createTask: (projectId: string | number, payload: TaskInput) =>
    request<Task & { project_progress?: number }>(`/projects/${projectId}/tasks/`, {
      method: "POST",
      auth: true,
      body: payload,
    }),

  updateTask: (taskId: number, payload: Partial<TaskInput>) =>
    request<Task>(`/tasks/${taskId}/`, { method: "PATCH", auth: true, body: payload }),

  deleteTask: (taskId: number) =>
    request<void>(`/tasks/${taskId}/`, { method: "DELETE", auth: true }),
};
