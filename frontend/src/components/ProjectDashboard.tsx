/**
 * Dashboard d'un projet (MVP-011) : tout provient de `GET /api/projects/:id/dashboard/`.
 *
 * Le frontend n'invente ni calcul ni alerte : avancement, budget, retards et permissions sont
 * ceux du serveur. Les sections absentes de la réponse (`budget: null`, `activity: null`) ne
 * sont pas affichées — le rôle n'y a pas droit. États gérés : chargement, erreur, hors ligne
 * (données précédentes conservées) et projet vide.
 */

import { Link } from "react-router-dom";

import { dashboardApi, type DashboardAlert, type ProjectDashboard as Dashboard } from "../api/dashboard";
import { errorMessage, useAsync } from "../lib/useAsync";
import { formatDate, formatFcfa, formatPercent } from "../lib/format";
import AuthImage from "./AuthImage";
import { Alert, Button } from "./ui";

const SEVERITY_TONE: Record<DashboardAlert["severity"], "error" | "warning" | "info"> = {
  critical: "error",
  warning: "warning",
  info: "info",
};

const SEVERITY_LABEL: Record<DashboardAlert["severity"], string> = {
  critical: "Critique",
  warning: "À surveiller",
  info: "Information",
};

const THRESHOLD_LABEL = { OK: "Sous contrôle", WARNING: "Seuil de 80 % atteint", EXCEEDED: "Budget dépassé" } as const;

const AUDIENCE_LABEL: Record<Dashboard["audience"], string> = {
  manager: "Vue pilotage",
  engineer: "Vue ingénierie",
  field: "Vue terrain",
  investor: "Vue investisseur",
};

export default function ProjectDashboard({ projectId }: { projectId: number | string }) {
  const { data, loading, error, stale, updatedAt, reload } = useAsync(
    () => dashboardApi.project(projectId),
    [projectId],
  );

  if (!data && loading) {
    return (
      <section className="card" aria-busy="true" data-testid="dashboard-loading">
        <h2 className="section-title">Tableau de bord</h2>
        <div className="skeleton" />
        <div className="skeleton" />
      </section>
    );
  }
  if (!data) {
    return (
      <section className="card" data-testid="dashboard-error">
        <h2 className="section-title">Tableau de bord</h2>
        <Alert tone="error">{errorMessage(error)}</Alert>
        <Button variant="ghost" onClick={() => void reload()}>
          Réessayer
        </Button>
      </section>
    );
  }

  const { progress, budget, alerts, evidences, expenses, activity } = data;
  const noPlanning = progress.milestones_total === 0 && progress.tasks_total === 0;

  return (
    <section className="card" aria-label="Tableau de bord du projet" data-testid="project-dashboard">
      <div className="section-head">
        <div>
          <h2 className="section-title">Tableau de bord</h2>
          <div className="field-hint">
            {AUDIENCE_LABEL[data.audience]} · à jour au {formatTime(updatedAt ?? new Date(data.generated_at))}
          </div>
        </div>
        <Button variant="ghost" onClick={() => void reload()} loading={loading}>
          Actualiser
        </Button>
      </div>

      {stale ? (
        <Alert tone="warning">
          {errorMessage(error)} Les chiffres affichés datent de {formatTime(updatedAt)}.
        </Alert>
      ) : null}

      {alerts.length > 0 ? (
        <ul className="alert-list" aria-label="Alertes" data-testid="dashboard-alerts">
          {alerts.map((alert, index) => (
            <li key={`${alert.code}-${alert.entity_id ?? index}`}>
              <Alert tone={SEVERITY_TONE[alert.severity]}>
                <strong>{SEVERITY_LABEL[alert.severity]} · </strong>
                {alert.message}
              </Alert>
            </li>
          ))}
          {data.alerts_total > alerts.length ? (
            <li className="field-hint">
              + {data.alerts_total - alerts.length} autre(s) alerte(s) — consultez le planning.
            </li>
          ) : null}
        </ul>
      ) : (
        <p className="field-hint" data-testid="dashboard-no-alert">
          Aucune alerte : le projet respecte son planning et son budget.
        </p>
      )}

      <div className="grid">
        <div className="metric">
          <div className="metric-label">Avancement</div>
          <div className="metric-value">{formatPercent(progress.progress)}</div>
          <div className="progress-track" aria-hidden="true">
            <div className="progress-fill" style={{ width: `${Math.min(100, progress.progress)}%` }} />
          </div>
          <div className="field-hint">
            {progress.milestones_done}/{progress.milestones_total} jalons · {progress.tasks_done}/
            {progress.tasks_total} tâches
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">Retards</div>
          <div className="metric-value" data-testid="late-count">
            {progress.tasks_late + progress.milestones_late}
          </div>
          <div className="field-hint">
            {progress.tasks_late} tâche(s) · {progress.milestones_late} jalon(s)
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">Fin prévue</div>
          <div className="metric-value">{formatDate(data.project.planned_end_date)}</div>
          <div className="field-hint">
            {data.project.days_to_end === null
              ? "Date non renseignée"
              : data.project.days_to_end >= 0
                ? `dans ${data.project.days_to_end} jour(s)`
                : `dépassée de ${Math.abs(data.project.days_to_end)} jour(s)`}
          </div>
        </div>
      </div>

      {noPlanning ? (
        <p className="field-hint">
          Aucun jalon ni tâche n'est planifié : l'avancement restera à 0 % tant que le planning est vide.
        </p>
      ) : (
        <div className="grid" style={{ marginTop: 12 }}>
          <MilestoneCard title="Dernier jalon terminé" milestone={data.milestones.last_completed} />
          <MilestoneCard title="Prochain jalon" milestone={data.milestones.next} />
        </div>
      )}

      {budget ? (
        <div style={{ marginTop: 16 }} data-testid="dashboard-budget">
          <h3 className="section-subtitle">Budget · {THRESHOLD_LABEL[budget.threshold]}</h3>
          <div className="grid">
            <Metric label="Budget prévu" value={formatFcfa(budget.planned)} />
            <Metric label="Engagé" value={formatFcfa(budget.committed)} />
            <Metric label="Payé" value={formatFcfa(budget.paid)} />
            <Metric label="Solde disponible" value={formatFcfa(budget.balance)} tone={budget.balance < 0 ? "bad" : undefined} />
          </div>
          <div
            className="progress-track"
            role="progressbar"
            aria-label="Budget consommé"
            aria-valuenow={Math.round(budget.consumption_rate)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className={`progress-fill${budget.threshold !== "OK" ? " progress-warn" : ""}`}
              style={{ width: `${Math.min(100, budget.consumption_rate)}%` }}
            />
          </div>
          <div className="field-hint">{formatPercent(budget.consumption_rate)} du budget engagé</div>
        </div>
      ) : null}

      <div style={{ marginTop: 16 }}>
        <h3 className="section-subtitle">Preuves terrain</h3>
        <div className="field-hint" data-testid="evidence-counts">
          {Object.entries(evidences.counts)
            .filter(([key]) => key !== "stale")
            .map(([key, value]) => `${value} ${EVIDENCE_COUNT_LABEL[key] ?? key}`)
            .join(" · ")}
          {evidences.counts.stale ? ` · ${evidences.counts.stale} en attente depuis plus de 72 h` : ""}
        </div>
        {evidences.latest.length === 0 ? (
          <p className="field-hint">Aucune preuve déposée pour le moment.</p>
        ) : (
          <ul className="thumb-row" aria-label="Dernières preuves">
            {evidences.latest.map((item) => (
              <li key={item.id} title={`${item.status_label} · ${formatDate(item.captured_at)}`}>
                <AuthImage
                  src={item.thumbnail_url}
                  alt={item.description || `Preuve ${item.id}`}
                  width={72}
                  height={72}
                  className="thumb"
                />
                <span className="field-hint">{item.status_label}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {expenses ? (
        <div style={{ marginTop: 16 }}>
          <h3 className="section-subtitle">Dernières dépenses</h3>
          {expenses.latest.length === 0 ? (
            <p className="field-hint">Aucune dépense enregistrée.</p>
          ) : (
            <ul className="plain-list" data-testid="dashboard-expenses">
              {expenses.latest.map((expense) => (
                <li key={expense.id}>
                  <span>
                    {expense.title}
                    <span className="field-hint"> · {expense.status_label}</span>
                  </span>
                  <strong>{formatFcfa(expense.amount)}</strong>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {activity ? (
        <div style={{ marginTop: 16 }}>
          <h3 className="section-subtitle">Activité récente</h3>
          {activity.length === 0 ? (
            <p className="field-hint">Aucune activité enregistrée.</p>
          ) : (
            <ul className="plain-list" data-testid="dashboard-activity">
              {activity.map((event) => (
                <li key={event.id}>
                  <span>
                    {event.action_label}
                    {event.actor ? <span className="field-hint"> · {event.actor.name}</span> : null}
                  </span>
                  <span className="field-hint">{formatDate(event.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
          {data.permissions.view_activity ? (
            <Link to={`/projets/${data.project.id}#journal`}>Voir le journal complet</Link>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

const EVIDENCE_COUNT_LABEL: Record<string, string> = {
  pending: "en attente",
  validated: "validée(s)",
  rejected: "rejetée(s)",
  flagged: "signalée(s)",
};

function formatTime(value: Date | null): string {
  if (!value) return "—";
  return value.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "bad" }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value" style={tone === "bad" ? { color: "#b42318" } : undefined}>
        {value}
      </div>
    </div>
  );
}

function MilestoneCard({ title, milestone }: { title: string; milestone: Dashboard["milestones"]["next"] }) {
  return (
    <div className="metric">
      <div className="metric-label">{title}</div>
      {milestone ? (
        <>
          <div style={{ fontWeight: 600 }}>{milestone.title}</div>
          <div className="field-hint">
            {milestone.actual_date
              ? `Terminé le ${formatDate(milestone.actual_date)}`
              : `Prévu le ${formatDate(milestone.planned_date)}`}
            {milestone.days_late > 0 ? ` · ${milestone.days_late} j de retard` : ""}
          </div>
        </>
      ) : (
        <div className="field-hint">Aucun</div>
      )}
    </div>
  );
}
