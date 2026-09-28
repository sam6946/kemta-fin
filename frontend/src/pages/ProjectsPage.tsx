/** Liste des projets + création (MVP-005). */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import type { Organization } from "../api/organizations";
import { organizationsApi } from "../api/organizations";
import { PROJECT_STATUS_LABELS, projectsApi, type Project, type ProjectStatus } from "../api/projects";
import { fetchRoles, type RoleMeta } from "../api/roles";
import { useAuth } from "../auth/AuthContext";
import { messageForErrorCode } from "../auth/passwordPolicy";
import { Alert, Button, Field } from "../components/ui";
import { formatDate, formatFcfa, formatPercent, parseFcfaInput } from "../lib/format";

const EMPTY_FORM = {
  organization: "",
  name: "",
  code: "",
  city: "",
  region: "",
  location_label: "",
  budget_total: "",
  status: "DRAFT" as ProjectStatus,
  planned_start_date: "",
  planned_end_date: "",
};

export default function ProjectsPage() {
  const { user, hasCapability } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [roles, setRoles] = useState<RoleMeta[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [creating, setCreating] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const canCreate = hasCapability("create_project");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [projectPage, organizationPage] = await Promise.all([
        projectsApi.list(statusFilter ? { status: statusFilter } : {}),
        organizationsApi.list(),
      ]);
      setProjects(projectPage.results);
      setOrganizations(organizationPage.results);
      if (canCreate) setRoles(await fetchRoles());
    } catch (caught) {
      setError(caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("server_error"));
    } finally {
      setLoading(false);
    }
  }, [statusFilter, canCreate]);

  useEffect(() => {
    void load();
  }, [load]);

  // Organisations dans lesquelles l'utilisateur peut créer un projet
  // (le backend revérifie systématiquement).
  const writableOrganizations = organizations.filter(
    (organization) => organization.owner.id === user?.id || user?.capabilities.includes("create_project"),
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setFeedback(null);
    setError(null);

    const budget = parseFcfaInput(form.budget_total);
    if (budget === null) {
      setError("Le budget doit être un montant entier en FCFA (sans centimes).");
      return;
    }
    if (!form.organization) {
      setError("Sélectionnez l'organisation qui porte le projet.");
      return;
    }

    setCreating(true);
    try {
      await projectsApi.create({
        organization: Number(form.organization),
        name: form.name,
        code: form.code || undefined,
        city: form.city || undefined,
        region: form.region || undefined,
        location_label: form.location_label || undefined,
        budget_total: budget,
        status: form.status,
        planned_start_date: form.planned_start_date || null,
        planned_end_date: form.planned_end_date || null,
      });
      setForm({ ...EMPTY_FORM });
      setFeedback("Projet créé. Vous en êtes le responsable.");
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("server_error"));
    } finally {
      setCreating(false);
    }
  }

  const roleOptions = roles.length ? roles.map((role) => role.code) : [];

  return (
    <div className="dashboard">
      <header className="brand" style={{ marginBottom: 12 }}>
        <span className="brand-mark">KEMTA SUIVI</span>
        <span className="brand-sub">Projets</span>
      </header>

      <div className="links" style={{ flexDirection: "row", gap: 16 }}>
        <Link to="/tableau-de-bord">Tableau de bord</Link>
        <Link to="/organisations">Organisations</Link>
      </div>

      {error ? <Alert tone="error">{error}</Alert> : null}
      {feedback ? <Alert tone="success">{feedback}</Alert> : null}

      <section className="card">
        <h1>Mes projets</h1>
        <p className="subtitle">
          Seuls les projets auxquels vous avez accès apparaissent ici. Les montants sont en FCFA
          entiers, calculés côté serveur.
        </p>

        <Field label="Filtrer par statut">
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">Tous les statuts</option>
            {Object.entries(PROJECT_STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>

        {loading ? <p>Chargement…</p> : null}
        {!loading && projects.length === 0 ? (
          <Alert tone="info">Aucun projet pour le moment.</Alert>
        ) : null}

        <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 12 }}>
          {projects.map((project) => (
            <li key={project.id} className="metric" data-testid="project-card">
              <div className="metric-label">
                {project.status_label} · {project.organization_name}
              </div>
              <div className="metric-value">
                <Link to={`/projets/${project.id}`}>{project.name}</Link>
              </div>
              <p className="field-hint" style={{ margin: "6px 0" }}>
                {project.code ? `${project.code} · ` : ""}
                {project.city || "localisation à préciser"} · {project.member_count} membre(s)
              </p>
              <div className="grid">
                <div>
                  <div className="metric-label">Budget prévu</div>
                  <div>{formatFcfa(project.budget_total)}</div>
                </div>
                <div>
                  <div className="metric-label">Avancement</div>
                  <div>{formatPercent(project.progress)}</div>
                </div>
                <div>
                  <div className="metric-label">Fin prévue</div>
                  <div>{formatDate(project.planned_end_date)}</div>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </section>

      {canCreate ? (
        <section className="card">
          <h2 style={{ fontSize: "1rem", marginTop: 0 }}>Créer un projet</h2>
          {writableOrganizations.length === 0 ? (
            <Alert tone="warning">
              Créez d'abord une organisation : <Link to="/organisations">Organisations</Link>.
            </Alert>
          ) : null}
          <form onSubmit={submit} noValidate>
            <Field label="Organisation">
              <select
                value={form.organization}
                onChange={(event) => setForm({ ...form, organization: event.target.value })}
                required
              >
                <option value="">— Sélectionner —</option>
                {writableOrganizations.map((organization) => (
                  <option key={organization.id} value={organization.id}>
                    {organization.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Nom du projet">
              <input
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                required
              />
            </Field>
            <Field label="Code interne" hint="Facultatif, unique par organisation (ex. RBS-T1).">
              <input value={form.code} onChange={(event) => setForm({ ...form, code: event.target.value })} />
            </Field>
            <Field label="Budget prévu (FCFA)" hint="Montant entier, sans centimes.">
              <input
                inputMode="numeric"
                value={form.budget_total}
                onChange={(event) => setForm({ ...form, budget_total: event.target.value })}
                placeholder="85000000"
                required
              />
            </Field>
            <Field label="Ville">
              <input value={form.city} onChange={(event) => setForm({ ...form, city: event.target.value })} />
            </Field>
            <Field label="Région">
              <input value={form.region} onChange={(event) => setForm({ ...form, region: event.target.value })} />
            </Field>
            <Field label="Statut">
              <select
                value={form.status}
                onChange={(event) => setForm({ ...form, status: event.target.value as ProjectStatus })}
              >
                {Object.entries(PROJECT_STATUS_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Début prévu">
              <input
                type="date"
                value={form.planned_start_date}
                onChange={(event) => setForm({ ...form, planned_start_date: event.target.value })}
              />
            </Field>
            <Field label="Fin prévue">
              <input
                type="date"
                value={form.planned_end_date}
                onChange={(event) => setForm({ ...form, planned_end_date: event.target.value })}
              />
            </Field>
            <Button type="submit" loading={creating}>
              Créer le projet
            </Button>
            {roleOptions.length ? null : null}
          </form>
        </section>
      ) : (
        <Alert tone="info">
          Votre rôle ne permet pas de créer un projet : demandez à un responsable d'organisation
          de vous ajouter.
        </Alert>
      )}
    </div>
  );
}
