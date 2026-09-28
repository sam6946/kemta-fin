/**
 * Tableau de bord minimal de la chaîne d'accès (phase 2).
 *
 * Le dashboard agrégé (MVP-011) arrive en phase 8 : ici on affiche le profil,
 * les capacités réellement accordées par le backend et l'ajout facultatif de l'email.
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { authApi } from "../api/auth";
import { ApiError } from "../api/client";
import { organizationsApi } from "../api/organizations";
import { projectsApi } from "../api/projects";
import { useAuth } from "../auth/AuthContext";
import { messageForErrorCode } from "../auth/passwordPolicy";
import SyncBadge from "../components/SyncBadge";
import { Alert, Button, Field } from "../components/ui";

export default function DashboardPage() {
  const { user, logout, refreshProfile } = useAuth();
  const [counts, setCounts] = useState<{ projects: number; organizations: number } | null>(null);
  const [email, setEmail] = useState(user?.email ?? "");
  const [code, setCode] = useState("");
  const [stage, setStage] = useState<"idle" | "code">("idle");
  const [feedback, setFeedback] = useState<{ tone: "success" | "error" | "info"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const loadCounts = useCallback(async () => {
    try {
      const [projectPage, organizationPage] = await Promise.all([
        projectsApi.list(),
        organizationsApi.list(),
      ]);
      setCounts({ projects: projectPage.count, organizations: organizationPage.count });
    } catch {
      setCounts(null); // l'échec d'un compteur ne doit pas casser le tableau de bord
    }
  }, []);

  useEffect(() => {
    void loadCounts();
  }, [loadCounts]);

  if (!user) return null;

  async function requestEmail(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFeedback(null);
    try {
      await authApi.requestEmailVerification({ email });
      setStage("code");
      setFeedback({ tone: "info", text: "Un code de vérification a été envoyé à cette adresse." });
    } catch (caught) {
      setFeedback({
        tone: "error",
        text: caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("server_error"),
      });
    } finally {
      setBusy(false);
    }
  }

  async function confirmEmail(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFeedback(null);
    try {
      await authApi.confirmEmailVerification({ email, code });
      await refreshProfile();
      setStage("idle");
      setFeedback({ tone: "success", text: "Adresse email vérifiée." });
    } catch (caught) {
      setFeedback({
        tone: "error",
        text: caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("otp_invalid"),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dashboard">
      <header className="brand" style={{ marginBottom: 16 }}>
        <span className="brand-mark">KEMTA SUIVI</span>
        <span className="brand-sub">{user.role_label}</span>
      </header>

      <section className="card">
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <h1 style={{ margin: 0 }}>Bonjour {user.first_name}</h1>
          {/* Phase 6 — l'état de la file hors ligne est visible dès l'ouverture de l'application. */}
          <SyncBadge />
        </div>
        <p className="subtitle">
          {user.phone_masked} · {user.is_phone_verified ? "téléphone vérifié" : "téléphone non vérifié"}
        </p>
        <div className="grid">
          <div className="metric">
            <div className="metric-label">Projets accessibles</div>
            <div className="metric-value">{counts ? counts.projects : "…"}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Organisations</div>
            <div className="metric-value">{counts ? counts.organizations : "…"}</div>
          </div>
        </div>
        <div className="links" style={{ flexDirection: "row", gap: 16 }}>
          <Link to="/projets">Voir mes projets</Link>
          <Link to="/organisations">Mes organisations</Link>
        </div>
        <p className="field-hint" style={{ marginTop: 12 }}>
          Les indicateurs consolidés (avancement, budget consommé, alertes) arriveront avec
          l'endpoint agrégé de la phase 8. Aucune valeur n'est simulée ici.
        </p>
      </section>

      <section className="card">
        <h2 style={{ fontSize: "1rem", marginTop: 0 }}>Adresse email (facultatif)</h2>
        {user.email ? (
          <Alert tone="success">{user.email}</Alert>
        ) : (
          <>
            {feedback ? <Alert tone={feedback.tone}>{feedback.text}</Alert> : null}
            <form onSubmit={stage === "idle" ? requestEmail : confirmEmail} noValidate>
              <Field label="Adresse email" hint="Elle sert uniquement aux notifications et à la récupération de secours.">
                <input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  disabled={stage === "code"}
                  required
                />
              </Field>
              {stage === "code" ? (
                <Field label="Code de vérification">
                  <input
                    inputMode="numeric"
                    maxLength={6}
                    value={code}
                    onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
                    required
                  />
                </Field>
              ) : null}
              <Button type="submit" loading={busy}>
                {stage === "idle" ? "Ajouter mon email" : "Vérifier le code"}
              </Button>
            </form>
          </>
        )}
      </section>

      <section className="card">
        <h2 style={{ fontSize: "1rem", marginTop: 0 }}>Vos permissions</h2>
        <p className="field-hint">
          Affichées à titre indicatif : le backend applique ces règles à chaque requête.
        </p>
        <div className="grid">
          {user.capabilities.map((capability) => (
            <div className="metric" key={capability}>
              <div className="metric-label">{capability}</div>
            </div>
          ))}
        </div>
      </section>

      <Button variant="ghost" onClick={() => void logout()}>
        Se déconnecter
      </Button>
    </div>
  );
}
