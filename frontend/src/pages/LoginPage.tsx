import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { messageForErrorCode } from "../auth/passwordPolicy";
import { Alert, AuthLayout, Button, Field } from "../components/ui";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [needsActivation, setNeedsActivation] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setNeedsActivation(false);
    setSubmitting(true);
    try {
      await login(phone, password);
      navigate("/tableau-de-bord", { replace: true });
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(messageForErrorCode(caught.code));
        setNeedsActivation(caught.code === "account_not_confirmed");
      } else {
        setError(messageForErrorCode("server_error"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout
      title="Connexion"
      subtitle="Connectez-vous avec votre numéro de téléphone."
      footer={<>KEMTA SUIVI — MVP. Numéros camerounais acceptés (ex. +237 6XX XX XX XX).</>}
    >
      <form onSubmit={onSubmit} noValidate>
        {error ? <Alert tone="error">{error}</Alert> : null}
        {needsActivation ? (
          <Alert tone="warning">
            Votre compte n'est pas encore activé. Utilisez « Recevoir un code » depuis l'écran
            d'inscription pour obtenir un nouveau code.
          </Alert>
        ) : null}
        <Field label="Numéro de téléphone">
          <input
            type="tel"
            name="phone"
            inputMode="tel"
            autoComplete="tel"
            required
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
            placeholder="6 90 12 34 56"
          />
        </Field>
        <Field label="Mot de passe">
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
        <Button type="submit" loading={submitting}>
          {submitting ? "Connexion…" : "Se connecter"}
        </Button>
      </form>
      <div className="links">
        {/* MVP-017 : l'accès au parcours de récupération est toujours visible. */}
        <Link to="/mot-de-passe-oublie">Mot de passe oublié ?</Link>
        <Link to="/inscription">Créer un compte</Link>
      </div>
    </AuthLayout>
  );
}
