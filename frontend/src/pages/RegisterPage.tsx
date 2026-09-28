import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { authApi } from "../api/auth";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { evaluatePassword, messageForErrorCode } from "../auth/passwordPolicy";
import DevOtpPanel from "../components/DevOtpPanel";
import { Alert, AuthLayout, Button, Field } from "../components/ui";

export default function RegisterPage() {
  const { setSession } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({
    phone: "",
    first_name: "",
    last_name: "",
    password: "",
    password_confirm: "",
  });
  const [stage, setStage] = useState<"identity" | "otp">("identity");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const { valid } = evaluatePassword(form.password);

  function update(key: keyof typeof form) {
    return (event: React.ChangeEvent<HTMLInputElement>) =>
      setForm((previous) => ({ ...previous, [key]: event.target.value }));
  }

  async function submitIdentity(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await authApi.register(form);
      setStage("otp");
    } catch (caught) {
      setError(caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("server_error"));
    } finally {
      setSubmitting(false);
    }
  }

  async function submitOtp(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const session = await authApi.verifyOtp({ phone: form.phone, code });
      await setSession(session.access, session.refresh);
      navigate("/tableau-de-bord", { replace: true });
    } catch (caught) {
      setError(caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("server_error"));
    } finally {
      setSubmitting(false);
    }
  }

  async function resend() {
    setError(null);
    try {
      await authApi.resendOtp({ phone: form.phone });
    } catch (caught) {
      setError(caught instanceof ApiError ? messageForErrorCode(caught.code) : messageForErrorCode("server_error"));
    }
  }

  if (stage === "otp") {
    return (
      <AuthLayout
        title="Activation du compte"
        subtitle={`Saisissez le code à 6 chiffres envoyé au ${form.phone}.`}
        footer={<Link to="/connexion">J'ai déjà un compte</Link>}
      >
        <form onSubmit={submitOtp} noValidate>
          {error ? <Alert tone="error">{error}</Alert> : null}
          <Field label="Code reçu par SMS">
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              required
              value={code}
              onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
            />
          </Field>
          <Button type="submit" loading={submitting}>
            Activer mon compte
          </Button>
          <Button type="button" variant="ghost" onClick={resend}>
            Renvoyer un code
          </Button>
        </form>

        <DevOtpPanel phone={form.phone} onPick={setCode} />
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Créer un compte"
      subtitle="Inscription par numéro de téléphone — aucune adresse email n'est demandée."
      footer={<Link to="/connexion">J'ai déjà un compte</Link>}
    >
      <form onSubmit={submitIdentity} noValidate>
        {error ? <Alert tone="error">{error}</Alert> : null}
        <Field label="Numéro de téléphone">
          <input type="tel" name="phone" inputMode="tel" required value={form.phone} onChange={update("phone")} />
        </Field>
        <Field label="Prénom">
          <input name="first_name" autoComplete="given-name" required value={form.first_name} onChange={update("first_name")} />
        </Field>
        <Field label="Nom">
          <input name="last_name" autoComplete="family-name" required value={form.last_name} onChange={update("last_name")} />
        </Field>
        <Field label="Mot de passe">
          <input type="password" name="password" autoComplete="new-password" required value={form.password} onChange={update("password")} />
        </Field>
        <Field label="Confirmer le mot de passe">
          <input
            type="password"
            name="password_confirm"
            autoComplete="new-password"
            required
            value={form.password_confirm}
            onChange={update("password_confirm")}
          />
        </Field>
        <Button type="submit" loading={submitting} disabled={!valid}>
          Recevoir le code par SMS
        </Button>
      </form>
    </AuthLayout>
  );
}
