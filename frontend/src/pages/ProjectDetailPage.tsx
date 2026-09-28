/** Détail d'un projet : informations, membres, gestion des accès (MVP-005). */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { projectsApi, type Project, type ProjectMember } from "../api/projects";
import { fetchRoles, type RoleMeta } from "../api/roles";
import { messageForErrorCode } from "../auth/passwordPolicy";
import { Alert, Button, Field } from "../components/ui";
import ProjectEvidences from "./ProjectEvidences";
import ProjectFinance from "./ProjectFinance";
import ProjectPlanning from "./ProjectPlanning";
import { formatDate, formatFcfa, formatPercent } from "../lib/format";

export default function ProjectDetailPage() {
  const { id = "" } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [roles, setRoles] = useState<RoleMeta[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [newMember, setNewMember] = useState({ phone: "", role: "ENGINEER", can_validate_evidence: false, can_manage_finance: false });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [projectData, memberPage, roleList] = await Promise.all([
        projectsApi.get(id),
        projectsApi.listMembers(id),
        fetchRoles(),
      ]);
      setProject(projectData);
      setMembers(memberPage.results);
      setRoles(roleList);
    } catch (caught) {
      setError(caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("server_error"));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function addMember(event: React.FormEvent) {
    event.preventDefault();
    if (!project?.permissions.manage_members) return;
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      await projectsApi.addMember(id, newMember);
      setNewMember({ phone: "", role: "ENGINEER", can_validate_evidence: false, can_manage_finance: false });
      setFeedback("Membre ajouté.");
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("server_error"));
    } finally {
      setBusy(false);
    }
  }

  async function changeRole(member: ProjectMember, role: string) {
    setError(null);
    try {
      await projectsApi.updateMember(id, member.id, {
        phone: member.user.phone,
        role,
        can_validate_evidence: member.can_validate_evidence,
        can_manage_finance: member.can_manage_finance,
      });
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("server_error"));
    }
  }

  async function removeMember(member: ProjectMember) {
    setError(null);
    try {
      await projectsApi.removeMember(id, member.id);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("server_error"));
    }
  }

  async function archive() {
    setError(null);
    try {
      await projectsApi.archive(id);
      setFeedback("Projet archivé.");
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("server_error"));
    }
  }

  if (loading) return <div className="screen-center">Chargement du projet…</div>;
  if (error && !project) {
    return (
      <div className="dashboard">
        <Alert tone="error">{error}</Alert>
        <Link to="/projets">← Retour aux projets</Link>
      </div>
    );
  }
  if (!project) return null;

  const canManageMembers = project.permissions.manage_members;

  return (
    <div className="dashboard">
      <header className="brand" style={{ marginBottom: 12 }}>
        <span className="brand-mark">KEMTA SUIVI</span>
        <span className="brand-sub">{project.organization_name}</span>
      </header>
      <Link to="/projets">← Tous les projets</Link>

      <section className="card" style={{ marginTop: 12 }}>
        <h1>{project.name}</h1>
        <p className="subtitle">
          {project.code ? `${project.code} · ` : ""}
          {project.status_label} · {project.city || "localisation à préciser"}
          {project.region ? ` (${project.region})` : ""}
        </p>
        <div className="grid">
          <div className="metric">
            <div className="metric-label">Budget prévu</div>
            <div className="metric-value">{formatFcfa(project.budget_total)}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Avancement</div>
            <div className="metric-value">{formatPercent(project.progress)}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Fin prévue</div>
            <div className="metric-value">{formatDate(project.planned_end_date)}</div>
          </div>
        </div>
        <div className="links">
          <a href="#planning">Planning</a>
          <a href="#preuves">Preuves terrain</a>
          <a href="#finances">Finances</a>
        </div>
        {project.permissions.archive_project ? (
          <Button variant="ghost" onClick={archive}>
            Archiver le projet
          </Button>
        ) : null}
      </section>

      <ProjectPlanning project={project} members={members} onChanged={load} />

      <ProjectEvidences project={project} onChanged={load} />

      {/* MVP-010 — budget, dépenses, paiements et grand livre (calculs serveur uniquement) */}
      {project.permissions.view_finance ? (
        <ProjectFinance project={project} onChanged={load} />
      ) : (
        <section className="card">
          <Alert tone="info">
            Les informations financières de ce projet ne sont pas accessibles avec votre rôle.
          </Alert>
        </section>
      )}

      <section className="card">
        <h2 style={{ fontSize: "1rem", marginTop: 0 }}>Membres ({members.length})</h2>
        <div data-testid="members-feedback">
          {error ? <Alert tone="error">{error}</Alert> : null}
          {feedback ? <Alert tone="success">{feedback}</Alert> : null}
        </div>

        <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 10 }}>
          {members.map((member) => (
            <li key={member.id} className="metric" data-testid="member-row">
              <strong>{member.user.first_name} {member.user.last_name}</strong>
              <div className="field-hint">{member.user.phone_masked}</div>
              {canManageMembers ? (
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
                  <select
                    aria-label={`Rôle de ${member.user.first_name}`}
                    value={member.role}
                    onChange={(event) => void changeRole(member, event.target.value)}
                  >
                    {roles.map((role) => (
                      <option key={role.code} value={role.code}>
                        {role.label}
                      </option>
                    ))}
                  </select>
                  <Button variant="ghost" onClick={() => void removeMember(member)}>
                    Retirer
                  </Button>
                </div>
              ) : (
                <div className="field-hint">{member.role_label}</div>
              )}
              {member.can_validate_evidence ? <div className="field-hint">✓ validation des preuves</div> : null}
              {member.can_manage_finance ? <div className="field-hint">✓ gestion financière</div> : null}
            </li>
          ))}
        </ul>

        {canManageMembers ? (
          <form onSubmit={addMember} noValidate>
            <h3 style={{ fontSize: "0.95rem", margin: "8px 0 0" }}>Ajouter un membre</h3>
            <p className="field-hint">
              Le compte doit déjà exister : saisissez son numéro de téléphone.
            </p>
            <Field label="Numéro de téléphone">
              <input
                type="tel"
                value={newMember.phone}
                onChange={(event) => setNewMember({ ...newMember, phone: event.target.value })}
                required
              />
            </Field>
            <Field label="Rôle sur ce projet">
              <select
                value={newMember.role}
                onChange={(event) => setNewMember({ ...newMember, role: event.target.value })}
              >
                {roles.map((role) => (
                  <option key={role.code} value={role.code}>
                    {role.label}
                  </option>
                ))}
              </select>
            </Field>
            <label className="field">
              <span className="field-label">
                <input
                  type="checkbox"
                  checked={newMember.can_validate_evidence}
                  onChange={(event) =>
                    setNewMember({ ...newMember, can_validate_evidence: event.target.checked })
                  }
                />{" "}
                Peut valider les preuves
              </span>
            </label>
            <label className="field">
              <span className="field-label">
                <input
                  type="checkbox"
                  checked={newMember.can_manage_finance}
                  onChange={(event) =>
                    setNewMember({ ...newMember, can_manage_finance: event.target.checked })
                  }
                />{" "}
                Peut gérer les finances
              </span>
            </label>
            <Button type="submit" loading={busy}>
              Ajouter au projet
            </Button>
          </form>
        ) : (
          <Alert tone="info">
            Vous consultez les membres en lecture seule : votre rôle ne permet pas de les modifier.
          </Alert>
        )}
      </section>
    </div>
  );
}
