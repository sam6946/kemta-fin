/** Planning du projet : jalons, tâches, alertes de retard (MVP-006).
 *
 * L'avancement affiché vient du **serveur** (`schedule.project.progress`) : l'écran ne
 * calcule jamais l'avancement ni ne l'envoie. Les actions d'écriture ne sont proposées que
 * si le backend les autorise (`permissions.manage_schedule`, ou `update_task` lorsque la
 * tâche est assignée à l'utilisateur courant).
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../api/client";
import type { Project, ProjectMember } from "../api/projects";
import {
  fetchStatuses,
  planningApi,
  type Milestone,
  type MilestoneStatus,
  type PlanningTask,
  type Schedule,
  type StatusMeta,
  type TaskStatus,
} from "../api/planning";
import { useAuth } from "../auth/AuthContext";
import { messageForErrorCode } from "../auth/passwordPolicy";
import { Alert, Button, Field } from "../components/ui";
import { formatDate, formatPercent } from "../lib/format";

type Props = {
  project: Project;
  members: ProjectMember[];
  onChanged: () => Promise<void> | void;
};

const EMPTY_MILESTONE = { title: "", planned_date: "", weight: 1 };
const EMPTY_TASK = { title: "", milestone: "", planned_end_date: "", weight: 1 };

export default function ProjectPlanning({ project, members, onChanged }: Props) {
  const { user } = useAuth();
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [milestoneStatuses, setMilestoneStatuses] = useState<StatusMeta[]>([]);
  const [taskStatuses, setTaskStatuses] = useState<StatusMeta[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [newMilestone, setNewMilestone] = useState(EMPTY_MILESTONE);
  const [newTask, setNewTask] = useState(EMPTY_TASK);

  const canPlan = project.permissions.manage_schedule;
  const canUpdateTasks = project.permissions.manage_schedule || project.permissions.update_task;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [scheduleData, statuses] = await Promise.all([
        planningApi.schedule(project.id),
        fetchStatuses(),
      ]);
      setSchedule(scheduleData);
      setMilestoneStatuses(statuses.milestone);
      setTaskStatuses(statuses.task);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? messageForErrorCode(caught.code)
          : messageForErrorCode("server_error"),
      );
    } finally {
      setLoading(false);
    }
  }, [project.id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      await action();
      setFeedback(success);
      await load();
      await onChanged();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? messageForErrorCode(caught.code)
          : messageForErrorCode("server_error"),
      );
    } finally {
      setBusy(false);
    }
  }

  async function addMilestone(event: React.FormEvent) {
    event.preventDefault();
    if (!canPlan || newMilestone.title.trim().length < 3) return;
    await run(
      () =>
        planningApi.createMilestone(project.id, {
          title: newMilestone.title.trim(),
          planned_date: newMilestone.planned_date || null,
          weight: Number(newMilestone.weight) || 1,
        }),
      "Jalon créé : l'avancement du projet a été recalculé.",
    );
    setNewMilestone(EMPTY_MILESTONE);
  }

  async function addTask(event: React.FormEvent) {
    event.preventDefault();
    if (!canPlan || newTask.title.trim().length < 3) return;
    await run(
      () =>
        planningApi.createTask(project.id, {
          title: newTask.title.trim(),
          milestone: newTask.milestone ? Number(newTask.milestone) : null,
          planned_end_date: newTask.planned_end_date || null,
          weight: Number(newTask.weight) || 1,
        }),
      "Tâche créée.",
    );
    setNewTask(EMPTY_TASK);
  }

  async function setMilestoneStatus(milestone: Milestone, status: MilestoneStatus) {
    // Une date réelle est exigée par le backend pour un jalon terminé.
    const actual_date =
      status === "DONE" ? milestone.actual_date ?? new Date().toISOString().slice(0, 10) : milestone.actual_date;
    await run(
      () => planningApi.updateMilestone(milestone.id, { status, actual_date }),
      "Jalon mis à jour : l'avancement du projet a été recalculé.",
    );
  }

  async function setTaskStatus(task: PlanningTask, status: TaskStatus) {
    if (status === "DONE" && !task.actual_end_date) {
      await run(
        () =>
          planningApi.updateTask(task.id, {
            status,
            actual_end_date: new Date().toISOString().slice(0, 10),
          }),
        "Tâche terminée : l'avancement du projet a été recalculé.",
      );
      return;
    }
    await run(
      () => planningApi.updateTask(task.id, { status }),
      "Statut de la tâche mis à jour.",
    );
  }

  async function setTaskProgress(task: PlanningTask, progress: number) {
    await run(
      () => planningApi.updateTask(task.id, { progress }),
      "Avancement de la tâche enregistré.",
    );
  }

  function canEditTask(task: PlanningTask): boolean {
    if (project.permissions.manage_schedule) return true;
    if (!project.permissions.update_task) return false;
    return task.assignee?.id === user?.id;
  }

  if (loading && !schedule) return <div className="field-hint">Chargement du planning…</div>;

  return (
    <section className="card" id="planning" data-testid="planning">
      <h2 style={{ fontSize: "1rem", marginTop: 0 }}>
        Planning — avancement calculé : {formatPercent(schedule?.project.progress ?? 0)}
      </h2>

      {error ? <Alert tone="error">{error}</Alert> : null}
      {feedback ? <Alert tone="success">{feedback}</Alert> : null}

      {schedule && schedule.alerts.length > 0 ? (
        <Alert tone="warning">
          {schedule.alerts.length} alerte(s) de retard :{" "}
          {schedule.alerts
            .slice(0, 3)
            .map((alert) => `${alert.title} (${alert.days_late} j)`)
            .join(", ")}
          {schedule.alerts.length > 3 ? "…" : ""}
        </Alert>
      ) : (
        <p className="field-hint">Aucun retard détecté à ce jour.</p>
      )}

      {schedule ? (
        <p className="field-hint">
          {schedule.summary.milestones_done}/{schedule.summary.milestones_total} jalon(s) terminé(s)
          · {schedule.summary.tasks_done}/{schedule.summary.tasks_total} tâche(s) terminée(s) ·{" "}
          {schedule.summary.tasks_late} tâche(s) en retard
        </p>
      ) : null}

      <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 12 }}>
        {(schedule?.milestones ?? []).map((milestone) => {
          const tasks = milestone.tasks ?? [];
          return (
            <li key={milestone.id} className="metric" data-testid="milestone-row">
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <strong>{milestone.title}</strong>
                <span className={milestone.is_late ? "badge-late" : "field-hint"}>
                  {milestone.is_late
                    ? `En retard de ${milestone.days_late} j`
                    : milestone.status_label}
                </span>
              </div>
              <div className="field-hint">
                Prévu le {formatDate(milestone.planned_date)}
                {milestone.actual_date ? ` · réalisé le ${formatDate(milestone.actual_date)}` : ""} ·
                avancement {formatPercent(milestone.progress)} · {milestone.task_done}/
                {milestone.task_total} tâche(s)
              </div>
              <div className="progress-track" aria-hidden="true">
                <div className="progress-fill" style={{ width: `${milestone.progress}%` }} />
              </div>

              {tasks.length > 0 ? (
                <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 6, marginTop: 8 }}>
                  {tasks.map((task) => (
                    <li key={task.id} data-testid="task-row">
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                        <span>{task.title}</span>
                        <span className={task.is_late ? "badge-late" : "field-hint"}>
                          {task.is_late ? `${task.days_late} j de retard` : task.status_label}
                        </span>
                      </div>
                      <div className="field-hint">
                        {formatDate(task.planned_start_date)} → {formatDate(task.planned_end_date)}
                        {task.assignee ? ` · ${task.assignee.first_name} ${task.assignee.last_name}` : ""}
                        {` · ${formatPercent(task.progress)}`}
                      </div>
                      {canEditTask(task) ? (
                        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 4 }}>
                          <label
                            htmlFor={`task-progress-${task.id}`}
                            className="field-hint"
                            style={{ whiteSpace: "nowrap" }}
                          >
                            Avancement
                          </label>
                          <input
                            id={`task-progress-${task.id}`}
                            type="range"
                            min={0}
                            max={100}
                            step={5}
                            defaultValue={Number(task.progress)}
                            disabled={busy}
                            onMouseUp={(event) =>
                              void setTaskProgress(task, Number(event.currentTarget.value))
                            }
                            onTouchEnd={(event) =>
                              void setTaskProgress(task, Number(event.currentTarget.value))
                            }
                          />
                          <select
                            aria-label={`Statut de ${task.title}`}
                            value={task.status}
                            disabled={busy}
                            onChange={(event) =>
                              void setTaskStatus(task, event.target.value as TaskStatus)
                            }
                          >
                            {taskStatuses.map((status) => (
                              <option key={status.value} value={status.value}>
                                {status.label}
                              </option>
                            ))}
                          </select>
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}

              {canPlan ? (
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
                  <select
                    aria-label={`Statut du jalon ${milestone.title}`}
                    value={milestone.status}
                    disabled={busy}
                    onChange={(event) =>
                      void setMilestoneStatus(milestone, event.target.value as MilestoneStatus)
                    }
                  >
                    {milestoneStatuses.map((status) => (
                      <option key={status.value} value={status.value}>
                        {status.label}
                      </option>
                    ))}
                  </select>
                  <Button
                    variant="ghost"
                    onClick={() =>
                      void run(
                        () => planningApi.deleteMilestone(milestone.id),
                        "Jalon supprimé.",
                      )
                    }
                  >
                    Supprimer
                  </Button>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>

      {schedule && schedule.orphan_tasks.length > 0 ? (
        <div data-testid="orphan-tasks">
          <h3 style={{ fontSize: "0.95rem" }}>Tâches sans jalon</h3>
          <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 6 }}>
            {schedule.orphan_tasks.map((task) => (
              <li key={task.id} data-testid="task-row">
                {task.title} — {task.status_label} · {formatPercent(task.progress)}
                {task.is_late ? ` · ${task.days_late} j de retard` : ""} ·{" "}
                {formatDate(task.planned_end_date)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {canPlan ? (
        <div className="grid" style={{ marginTop: 12 }}>
          <form onSubmit={addMilestone} noValidate>
            <h3 style={{ fontSize: "0.95rem" }}>Nouveau jalon</h3>
            <Field label="Titre du jalon">
              <input
                value={newMilestone.title}
                onChange={(event) =>
                  setNewMilestone({ ...newMilestone, title: event.target.value })
                }
                required
              />
            </Field>
            <Field label="Date prévue">
              <input
                type="date"
                value={newMilestone.planned_date}
                onChange={(event) =>
                  setNewMilestone({ ...newMilestone, planned_date: event.target.value })
                }
              />
            </Field>
            <Field label="Poids" hint="Pondération dans l'avancement (1 = neutre)">
              <input
                type="number"
                min={1}
                step={1}
                value={newMilestone.weight}
                onChange={(event) =>
                  setNewMilestone({ ...newMilestone, weight: Number(event.target.value) })
                }
              />
            </Field>
            <Button type="submit" loading={busy}>
              Créer le jalon
            </Button>
          </form>

          <form onSubmit={addTask} noValidate>
            <h3 style={{ fontSize: "0.95rem" }}>Nouvelle tâche</h3>
            <Field label="Titre de la tâche">
              <input
                value={newTask.title}
                onChange={(event) => setNewTask({ ...newTask, title: event.target.value })}
                required
              />
            </Field>
            <Field label="Jalon (optionnel)">
              <select
                value={newTask.milestone}
                onChange={(event) => setNewTask({ ...newTask, milestone: event.target.value })}
              >
                <option value="">— Sans jalon —</option>
                {(schedule?.milestones ?? []).map((milestone) => (
                  <option key={milestone.id} value={milestone.id}>
                    {milestone.title}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Fin prévue">
              <input
                type="date"
                value={newTask.planned_end_date}
                onChange={(event) =>
                  setNewTask({ ...newTask, planned_end_date: event.target.value })
                }
              />
            </Field>
            <Field label="Poids">
              <input
                type="number"
                min={1}
                step={1}
                value={newTask.weight}
                onChange={(event) => setNewTask({ ...newTask, weight: Number(event.target.value) })}
              />
            </Field>
            <p className="field-hint">
              Responsable : {members.length} membre(s) disponible(s) — l'affectation se fait à la
              création depuis l'API (le sélecteur arrive avec la phase 5).
            </p>
            <Button type="submit" loading={busy}>
              Créer la tâche
            </Button>
          </form>
        </div>
      ) : (
        <Alert tone="info">
          {canUpdateTasks
            ? "Vous pouvez faire avancer les tâches dont vous êtes responsable."
            : "Consultation seule : votre rôle ne permet pas de modifier le planning."}
        </Alert>
      )}
    </section>
  );
}

