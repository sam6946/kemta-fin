/**
 * Politique de mot de passe — **miroir d'affichage** de la règle backend
 * (`config/settings.py` → `AUTH_PASSWORD_VALIDATORS`).
 *
 * Le backend reste l'autorité : ces contrôles servent uniquement à guider
 * l'utilisateur avant l'envoi. Toute divergence est sans conséquence (le serveur refuse),
 * mais elle doit être corrigée (test `passwordPolicy.test.ts`).
 */

export const PASSWORD_MIN_LENGTH = 10;

export type PasswordCheck = { id: string; label: string; ok: boolean };

export function evaluatePassword(password: string): { checks: PasswordCheck[]; valid: boolean; score: number } {
  const checks: PasswordCheck[] = [
    {
      id: "length",
      label: `Au moins ${PASSWORD_MIN_LENGTH} caractères`,
      ok: password.length >= PASSWORD_MIN_LENGTH,
    },
    { id: "letter", label: "Au moins une lettre", ok: /[a-zA-ZÀ-ÿ]/.test(password) },
    { id: "digit", label: "Au moins un chiffre", ok: /\d/.test(password) },
    {
      id: "not_numeric_only",
      label: "Pas uniquement des chiffres",
      ok: password.length > 0 && !/^\d+$/.test(password),
    },
    {
      id: "mixed_case",
      label: "Mélange de majuscules et de minuscules (recommandé)",
      ok: /[a-zà-ÿ]/.test(password) && /[A-ZÀ-Ý]/.test(password),
    },
  ];

  const score = checks.filter((check) => check.ok).length;
  const valid = checks.slice(0, 4).every((check) => check.ok);
  return { checks, valid, score };
}

/** Messages français associés aux codes d'erreur du backend. */
export function messageForErrorCode(code: string): string {
  const messages: Record<string, string> = {
    offline: "Pas de connexion. La réinitialisation nécessite le réseau : réessayez dès que possible.",
    rate_limited: "Trop de tentatives. Patientez une minute avant de réessayer.",
    otp_invalid: "Code invalide ou expiré. Demandez un nouveau code.",
    otp_max_attempts: "Nombre de tentatives dépassé. Demandez un nouveau code.",
    otp_resend_limited: "Un code a déjà été envoyé. Patientez avant de demander un nouveau code.",
    password_too_weak: "Mot de passe trop faible : suivez les règles affichées.",
    password_reused: "Le nouveau mot de passe doit être différent de l'actuel.",
    password_mismatch: "Les deux mots de passe ne sont pas identiques.",
    invalid_credentials: "Numéro ou mot de passe incorrect.",
    account_not_confirmed: "Compte non activé : validez d'abord le code reçu par SMS.",
    account_locked: "Compte temporairement verrouillé. Réessayez dans quelques minutes.",
    phone_invalid: "Numéro de téléphone invalide.",
    phone_already_used: "Ce numéro est déjà utilisé.",
    phone_pending_activation: "Ce numéro est déjà inscrit mais pas encore activé.",
    server_error: "Erreur technique. Réessayez dans un instant.",
  };
  return messages[code] ?? "Une erreur est survenue. Réessayez.";
}
