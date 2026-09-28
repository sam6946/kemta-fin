/**
 * MVP-017 — étape 1 : « Mot de passe oublié ? ».
 *
 * L'utilisateur ne saisit que son **numéro de téléphone** (aucun email).
 * La réponse du serveur est neutre : l'écran affiche toujours le même message,
 * ce qui évite de révéler si un compte existe.
 */

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { authApi } from "../api/auth";
import { ApiError } from "../api/client";
import { messageForErrorCode } from "../auth/passwordPolicy";
import { Alert, AuthLayout, Button, Field } from "../components/ui";

const NEUTRAL_MESSAGE =
  "Si ce numéro est associé à un compte KEMTA, un code vient d'être envoyé par SMS.";

export default function ForgotPasswordPage() {
  const navigate = useNavigate();
  const [phone, setPhone] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryIn, setRetryIn] = useState<number | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const response = await authApi.requestPasswordReset({ phone });
      setRetryIn(response.retry_in ?? null);
      setSent(true);
      // On passe le numéro à l'écran suivant : le second écran ne le redemande pas.
      setTimeout(() => navigate("/reinitialiser-mot-de-passe", { state: { phone } }), 1200);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(
          caught.code === "otp_resend_limited"
            ? `Un code a déjà été envoyé. Patientez ${caught.details?.retry_in ?? 60} secondes.`
            : messageForErrorCode(caught.code),
        );
      } else {
        setError(messageForErrorCode("server_error"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout
      title="Mot de passe oublié"
      subtitle="Saisissez le numéro de téléphone associé à votre compte : nous vous envoyons un code par SMS."
      footer={<Link to="/connexion">← Retour à la connexion</Link>}
    >
      {sent ? (
        <>
          <Alert tone="success">{NEUTRAL_MESSAGE}</Alert>
          <p className="field-hint">
            Le code est valable quelques minutes. S'il n'arrive pas, vérifiez le réseau puis
            demandez un nouveau code{retryIn ? ` après ${retryIn} secondes` : ""}.
          </p>
          <div className="links">
            <Link
              to="/reinitialiser-mot-de-passe"
              state={{ phone }}
              data-testid="continue-to-reset"
            >
              Saisir le code reçu →
            </Link>
          </div>
        </>
      ) : (
        <form onSubmit={onSubmit} noValidate>
          {error ? <Alert tone="error">{error}</Alert> : null}
          <Field label="Numéro de téléphone" hint="Exemple : 6 90 12 34 56 ou +237 690 12 34 56">
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
          <Button type="submit" loading={submitting} disabled={phone.trim().length < 6}>
            {submitting ? "Envoi…" : "Recevoir un code par SMS"}
          </Button>
          <p className="field-hint">
            Pas de réseau à cet instant ? La réinitialisation nécessite une connexion : elle
            ne peut pas être mise en attente comme une preuve terrain.
          </p>
        </form>
      )}
    </AuthLayout>
  );
}
