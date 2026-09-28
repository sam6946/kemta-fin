/**
 * MVP-017 — étapes 2 et 3 : saisie du code OTP puis du nouveau mot de passe.
 *
 * États couverts : loading, erreur (code invalide, quota, hors ligne), succès.
 * L'écran indique explicitement que les autres appareils seront déconnectés.
 */

import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { authApi } from "../api/auth";
import { ApiError } from "../api/client";
import { evaluatePassword, messageForErrorCode } from "../auth/passwordPolicy";
import DevOtpPanel from "../components/DevOtpPanel";
import { Alert, AuthLayout, Button, Field } from "../components/ui";

type LocationState = { phone?: string } | null;

export default function ResetPasswordPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const initialPhone = (location.state as LocationState)?.phone ?? "";

  const [phone, setPhone] = useState(initialPhone);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [online, setOnline] = useState(navigator.onLine);

  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  const { checks, valid } = evaluatePassword(password);
  const mismatch = confirm.length > 0 && password !== confirm;

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (!valid) {
      setError(messageForErrorCode("password_too_weak"));
      return;
    }
    if (password !== confirm) {
      setError(messageForErrorCode("password_mismatch"));
      return;
    }
    setSubmitting(true);
    try {
      await authApi.confirmPasswordReset({
        phone,
        code,
        new_password: password,
        new_password_confirm: confirm,
      });
      setSuccess(true);
      setTimeout(() => navigate("/connexion", { replace: true }), 2500);
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (caught.code === "otp_invalid" && typeof caught.details?.remaining_attempts === "number") {
          setError(`${messageForErrorCode("otp_invalid")} (essais restants : ${caught.details.remaining_attempts})`);
        } else {
          setError(messageForErrorCode(caught.code));
        }
      } else {
        setError(messageForErrorCode("server_error"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (success) {
    return (
      <AuthLayout title="Mot de passe réinitialisé" footer={<Link to="/connexion">Aller à la connexion</Link>}>
        <Alert tone="success">
          Votre mot de passe a été modifié. Toutes vos sessions actives ont été déconnectées :
          reconnectez-vous avec votre nouveau mot de passe.
        </Alert>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Nouveau mot de passe"
      subtitle="Saisissez le code reçu par SMS, puis choisissez un nouveau mot de passe."
      footer={<Link to="/mot-de-passe-oublie">Je n'ai pas reçu de code</Link>}
    >
      {!online ? (
        <div className="offline-banner" role="status">
          Hors ligne : la réinitialisation nécessite le réseau. Le formulaire reste rempli,
          réessayez dès que la connexion revient.
        </div>
      ) : null}

      <form onSubmit={onSubmit} noValidate>
        {error ? <Alert tone="error">{error}</Alert> : null}

        <Field label="Numéro de téléphone">
          <input
            type="tel"
            name="phone"
            inputMode="tel"
            autoComplete="tel"
            required
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
          />
        </Field>

        <Field label="Code reçu par SMS" hint="6 chiffres, valable quelques minutes.">
          <input
            type="text"
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]*"
            maxLength={6}
            required
            value={code}
            onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
          />
        </Field>

        <Field label="Nouveau mot de passe">
          <input
            type="password"
            name="new_password"
            autoComplete="new-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>

        <ul className="rules" aria-live="polite">
          {checks.map((check) => (
            <li key={check.id} className={check.ok ? "ok" : undefined}>
              {check.ok ? "✓" : "•"} {check.label}
            </li>
          ))}
        </ul>

        <Field label="Confirmer le mot de passe" error={mismatch ? messageForErrorCode("password_mismatch") : undefined}>
          <input
            type="password"
            name="new_password_confirm"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </Field>

        <Button type="submit" loading={submitting} disabled={!online || code.length < 4}>
          {submitting ? "Réinitialisation…" : "Réinitialiser mon mot de passe"}
        </Button>

        <p className="field-hint">
          Par sécurité, toutes vos sessions actives seront déconnectées après la
          réinitialisation.
        </p>
      </form>

      <DevOtpPanel phone={phone} onPick={setCode} />
    </AuthLayout>
  );
}
