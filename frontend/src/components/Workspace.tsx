/**
 * Espace de travail (MVP-011) : ce qui attend l'utilisateur, tous projets confondus.
 *
 * Alimenté par `GET /api/workspace/` — une seule requête, quelle que soit la taille du
 * portefeuille. Les files « à valider » / « à approuver » ne sont visibles que si le rôle a le
 * droit d'agir, et les projets qui demandent de l'attention sont listés en premier.
 */

import { Link } from "react-router-dom";

import { dashboardApi, type Workspace as WorkspaceData } from "../api/dashboard";
import { errorMessage, useAsync } from "../lib/useAsync";
import { formatDate, formatFcfa, formatPercent } from "../lib/format";
import AuthImage from "./AuthImage";
import { Alert, Button } from "./ui";

const PROFILE_LABEL: Record<WorkspaceData["profile"], string> = {
  manager: "Pilotage de projets",
  engineer: "Ingénierie et suivi technique",
  field: "Terrain et contrôle",
  investor: "Suivi investisseur",
  none: "Aucun projet",
};

export default function Workspace() {
  const { data, loading, error, stale, updatedAt, reload } = useAsync(() => dashboardApi.workspace(), []);

  if (!data && loading) {
    return (
      <section className="card" aria-busy="true" data-testid="workspace-loading">
        <div className="skeleton" />
        <div className="skeleton" />
      </section>
    );
  }
  if (!data) {
    return (
      <section className="card" data-testid="workspace-error">
        <Alert tone="error">{errorMessage(error)}</Alert>
        <Button variant="ghost" onClick={() => void reload()}>
          Réessayer
        </Button>
      </section>
    );
  }

  const { totals } = data;

  return (
    <section className="card" data-testid="workspace" aria-label="Espace de travail">
      <div className="section-head">
        <div>
          <h2 className="section-title">Mon espace de travail</h2>
          <div className="field-hint">
            {PROFILE_LABEL[data.profile]}
            {updatedAt ? ` · à jour à ${updatedAt.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}` : ""}
          </div>
        </div>
        <Button variant="ghost" onClick={() => void reload()} loading={loading}>
          Actualiser
        </Button>
      </div>

      {stale ? <Alert tone="warning">{errorMessage(error)} Données précédemment chargées affichées.</Alert> : null}

      <div className="grid">
        <div className="metric">
          <div className="metric-label">Projets</div>
          <div className="metric-value">{totals.projects}</div>
        </div>
        <div className="metric">
          <div className="metric-label">En retard</div>
          <div className="metric-value" data-testid="ws-late">
            {totals.tasks_late + totals.milestones_late}
          </div>
          <div className="field-hint">
            {totals.tasks_late} tâche(s) · {totals.milestones_late} jalon(s)
          </div>
        </div>
        {data.to_validate.count > 0 || totals.evidences_to_validate > 0 ? (
          <div className="metric">
            <div className="metric-label">Preuves à valider</div>
            <div className="metric-value" data-testid="ws-to-validate">
              {data.to_validate.count}
            </div>
          </div>
        ) : null}
        {data.to_approve.count > 0 || totals.expenses_to_approve > 0 ? (
          <div className="metric">
            <div className="metric-label">Dépenses à approuver</div>
            <div className="metric-value" data-testid="ws-to-approve">
              {data.to_approve.count}
            </div>
          </div>
        ) : null}
        {data.profile !== "investor" ? (
          <div className="metric">
            <div className="metric-label">Mes tâches ouvertes</div>
            <div className="metric-value">{totals.my_open_tasks}</div>
          </div>
        ) : null}
      </div>

      {data.projects.length === 0 ? (
        <p className="field-hint" data-testid="workspace-empty" style={{ marginTop: 12 }}>
          Vous n'êtes membre d'aucun projet pour le moment. Demandez à un responsable de vous ajouter
          à l'équipe d'un chantier.
        </p>
      ) : (
        <ul className="plain-list project-cards" data-testid="workspace-projects">
          {data.projects.map((project) => {
            const late = project.tasks_late + project.milestones_late;
            return (
              <li key={project.id}>
                <div>
                  <Link to={`/projets/${project.id}`}>
                    <strong>{project.name}</strong>
                  </Link>
                  <div className="field-hint">
                    {project.status_label} · {formatPercent(project.progress)}
                    {project.planned_end_date ? ` · fin ${formatDate(project.planned_end_date)}` : ""}
                  </div>
                </div>
                <div className="chips">
                  {late > 0 ? <span className="badge-late">{late} en retard</span> : null}
                  {project.evidences_to_validate > 0 ? (
                    <span className="badge-pending">{project.evidences_to_validate} preuve(s)</span>
                  ) : null}
                  {project.expenses_to_approve > 0 ? (
                    <span className="badge-pending">{project.expenses_to_approve} dépense(s)</span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {data.projects_truncated ? (
        <p className="field-hint">
          Seuls les projets les plus urgents sont affichés. <Link to="/projets">Voir tous les projets</Link>
        </p>
      ) : null}

      {data.my_tasks.length > 0 ? (
        <div style={{ marginTop: 16 }}>
          <h3 className="section-subtitle">Mes tâches</h3>
          <ul className="plain-list" data-testid="workspace-tasks">
            {data.my_tasks.map((task) => (
              <li key={task.id}>
                <span>
                  <Link to={`/projets/${task.project}#planning`}>{task.title}</Link>
                  <span className="field-hint">
                    {" "}
                    · {task.project_name} · {task.status_label}
                  </span>
                </span>
                {task.days_late > 0 ? (
                  <span className="badge-late">{task.days_late} j de retard</span>
                ) : (
                  <span className="field-hint">{formatDate(task.planned_end_date)}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {data.to_validate.items.length > 0 ? (
        <div style={{ marginTop: 16 }}>
          <h3 className="section-subtitle">À valider ({data.to_validate.count})</h3>
          <ul className="thumb-row" data-testid="workspace-validate">
            {data.to_validate.items.map((item) => (
              <li key={item.id}>
                <Link to={`/projets/${item.project}/preuves`} title={item.project_name}>
                  <AuthImage
                    src={item.thumbnail_url}
                    alt={`Preuve ${item.id} de ${item.project_name}`}
                    width={72}
                    height={72}
                    className="thumb"
                  />
                </Link>
                <span className="field-hint">{item.author?.name ?? "—"}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {data.to_approve.items.length > 0 ? (
        <div style={{ marginTop: 16 }}>
          <h3 className="section-subtitle">À approuver ({data.to_approve.count})</h3>
          <ul className="plain-list" data-testid="workspace-approve">
            {data.to_approve.items.map((item) => (
              <li key={item.id}>
                <span>
                  <Link to={`/projets/${item.project}#finances`}>{item.title}</Link>
                  <span className="field-hint"> · {item.project_name}</span>
                </span>
                <strong>{formatFcfa(item.amount)}</strong>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
