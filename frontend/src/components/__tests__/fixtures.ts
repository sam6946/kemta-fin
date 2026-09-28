import type { ProjectDashboard, Workspace } from "../../api/dashboard";

export function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function errorResponse(code: string, status: number, message = "Erreur") {
  return jsonResponse({ error: { code, message, details: {}, request_id: "r1" } }, status);
}

export function dashboard(overrides: Partial<ProjectDashboard> = {}): ProjectDashboard {
  return {
    reference_date: "2026-09-29",
    generated_at: "2026-09-29T09:00:00Z",
    audience: "manager",
    role: "PROJECT_OWNER",
    project: {
      id: 12,
      name: "Résidence Bonamoussadi",
      code: "RBS-T1",
      status: "ACTIVE",
      status_label: "En cours",
      city: "Douala",
      region: "Littoral",
      currency: "XAF",
      planned_start_date: "2026-01-05",
      planned_end_date: "2026-12-20",
      days_to_end: 82,
    },
    progress: {
      progress: 46.7,
      milestones_total: 3,
      milestones_done: 1,
      milestones_late: 1,
      tasks_total: 3,
      tasks_done: 1,
      tasks_late: 1,
    },
    milestones: {
      last_completed: {
        id: 1,
        title: "Fondations",
        status: "DONE",
        status_label: "Terminé",
        planned_date: "2026-08-20",
        actual_date: "2026-08-22",
        days_late: 0,
      },
      next: {
        id: 3,
        title: "Toiture",
        status: "PLANNED",
        status_label: "Planifié",
        planned_date: "2026-10-29",
        actual_date: null,
        days_late: 0,
      },
    },
    budget: {
      planned: 50_000_000,
      committed: 41_000_000,
      paid: 10_000_000,
      outstanding: 31_000_000,
      balance: 9_000_000,
      consumption_rate: 82,
      threshold: "WARNING",
      currency: "XAF",
    },
    alerts: [
      {
        code: "MILESTONE_LATE",
        severity: "critical",
        message: "Jalon « Élévation R+1 » en retard de 9 jour(s).",
        entity_type: "Milestone",
        entity_id: 2,
        days_late: 9,
      },
      {
        code: "BUDGET_THRESHOLD_REACHED",
        severity: "warning",
        message: "Le budget engagé a atteint 82 %.",
      },
    ],
    alerts_total: 4,
    evidences: {
      counts: { pending: 2, validated: 1, rejected: 0, flagged: 0, stale: 1 },
      latest: [
        {
          id: 5,
          status: "PENDING",
          status_label: "En attente de validation",
          captured_at: "2026-09-28T10:00:00Z",
          author: { id: 4, name: "Test Field Agent" },
          task_title: null,
          description: "Coulage dalle",
          thumbnail_url: "/api/evidences/5/thumbnail/",
        },
      ],
    },
    expenses: {
      counts: { draft: 0, submitted: 1, approved: 2, paid: 0, rejected: 0 },
      latest: [
        {
          id: 3,
          title: "Achat ciment",
          amount: 1_500_000,
          status: "SUBMITTED",
          status_label: "Soumise à validation",
          incurred_on: "2026-09-10",
          supplier: "CIMENCAM",
        },
      ],
    },
    activity: [
      {
        id: 9,
        action: "EXPENSE_SUBMITTED",
        action_label: "Dépense soumise",
        actor: { id: 6, name: "Test Finance" },
        entity_type: "Expense",
        entity_id: "3",
        created_at: "2026-09-28T09:00:00Z",
      },
    ],
    permissions: { view_finance: true, view_activity: true } as ProjectDashboard["permissions"],
    ...overrides,
  };
}

export function workspace(overrides: Partial<Workspace> = {}): Workspace {
  return {
    reference_date: "2026-09-29",
    generated_at: "2026-09-29T09:00:00Z",
    profile: "manager",
    totals: {
      projects: 1,
      tasks_late: 1,
      milestones_late: 1,
      evidences_to_validate: 1,
      expenses_to_approve: 1,
      my_open_tasks: 1,
    },
    projects: [
      {
        id: 12,
        name: "Résidence Bonamoussadi",
        code: "RBS-T1",
        organization_name: "KEMTA",
        status: "ACTIVE",
        status_label: "En cours",
        progress: 46.7,
        role: "PROJECT_OWNER",
        tasks_late: 1,
        milestones_late: 1,
        evidences_to_validate: 1,
        expenses_to_approve: 1,
        planned_end_date: "2026-12-20",
        budget_visible: true,
      },
    ],
    projects_truncated: false,
    my_tasks: [
      {
        id: 7,
        title: "Coffrage dalle R+1",
        project: 12,
        project_name: "Résidence Bonamoussadi",
        status: "IN_PROGRESS",
        status_label: "En cours",
        progress: "40.00",
        planned_end_date: "2026-09-09",
        days_late: 20,
      },
    ],
    to_validate: {
      count: 1,
      items: [
        {
          id: 5,
          project: 12,
          project_name: "Résidence Bonamoussadi",
          captured_at: "2026-09-28T10:00:00Z",
          author: { id: 4, name: "Test Field Agent" },
          thumbnail_url: "/api/evidences/5/thumbnail/",
        },
      ],
    },
    to_approve: {
      count: 1,
      items: [{ id: 3, project: 12, project_name: "Résidence Bonamoussadi", title: "Achat ciment", amount: 1_500_000 }],
    },
    unread_notifications: 2,
    ...overrides,
  };
}
