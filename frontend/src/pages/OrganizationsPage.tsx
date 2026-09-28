/** Organisations : liste et création (MVP-005). */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { organizationsApi, type Organization } from "../api/organizations";
import { useAuth } from "../auth/AuthContext";
import { messageForErrorCode } from "../auth/passwordPolicy";
import { Alert, Button, Field } from "../components/ui";

const TYPE_LABELS: Record<string, string> = {
  PROMOTER: "Promoteur immobilier",
  PME: "PME / entreprise de travaux",
  ENGINEERING_FIRM: "Bureau d'études",
  INVESTOR: "Investisseur / bailleur",
  PUBLIC: "Maître d'ouvrage public",
};

export default function OrganizationsPage() {
  const { hasCapability } = useAuth();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", type: "PROMOTER", city: "", address: "", contact_phone: "" });
  const [busy, setBusy] = useState(false);

  const canCreate = hasCapability("create_organization");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await organizationsApi.list();
      setOrganizations(page.results);
    } catch (caught) {
      setError(caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("server_error"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      const organization = await organizationsApi.create(form);
      setFeedback(`Organisation « ${organization.name} » créée (${organization.slug}).`);
      setForm({ name: "", type: "PROMOTER", city: "", address: "", contact_phone: "" });
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("server_error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dashboard">
      <header className="brand" style={{ marginBottom: 12 }}>
        <span className="brand-mark">KEMTA SUIVI</span>
        <span className="brand-sub">Organisations</span>
      </header>
      <div className="links" style={{ flexDirection: "row", gap: 16 }}>
        <Link to="/tableau-de-bord">Tableau de bord</Link>
        <Link to="/projets">Projets</Link>
      </div>

      {error ? <Alert tone="error">{error}</Alert> : null}
      {feedback ? <Alert tone="success">{feedback}</Alert> : null}

      <section className="card">
        <h1>Mes organisations</h1>
        <p className="subtitle">
          Un projet est toujours rattaché à une organisation : elle porte le cadre juridique et
          les accès.
        </p>
        {loading ? <p>Chargement…</p> : null}
        {!loading && organizations.length === 0 ? (
          <Alert tone="info">Aucune organisation pour le moment.</Alert>
        ) : null}
        <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 10 }}>
          {organizations.map((organization) => (
            <li key={organization.id} className="metric" data-testid="organization-card">
              <div className="metric-label">{TYPE_LABELS[organization.type] ?? organization.type}</div>
              <div className="metric-value">{organization.name}</div>
              <p className="field-hint" style={{ margin: "6px 0 0" }}>
                {organization.city || "ville non renseignée"} · {organization.project_count} projet(s) ·{" "}
                {organization.member_count} membre(s)
              </p>
            </li>
          ))}
        </ul>
      </section>

      {canCreate ? (
        <section className="card">
          <h2 style={{ fontSize: "1rem", marginTop: 0 }}>Créer une organisation</h2>
          <form onSubmit={submit} noValidate>
            <Field label="Nom">
              <input
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                required
              />
            </Field>
            <Field label="Type">
              <select value={form.type} onChange={(event) => setForm({ ...form, type: event.target.value })}>
                {Object.entries(TYPE_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Ville">
              <input value={form.city} onChange={(event) => setForm({ ...form, city: event.target.value })} />
            </Field>
            <Field label="Adresse">
              <input value={form.address} onChange={(event) => setForm({ ...form, address: event.target.value })} />
            </Field>
            <Field label="Téléphone de contact">
              <input
                type="tel"
                value={form.contact_phone}
                onChange={(event) => setForm({ ...form, contact_phone: event.target.value })}
              />
            </Field>
            <Button type="submit" loading={busy}>
              Créer l'organisation
            </Button>
          </form>
        </section>
      ) : null}
    </div>
  );
}
