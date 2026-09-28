/** Appels d'authentification — miroir du contrat `docs/api-contract.md` §2. */

import { request } from "./client";

export type User = {
  id: string;
  phone: string;
  phone_masked: string;
  email: string | null;
  first_name: string;
  last_name: string;
  role: string;
  role_label: string;
  is_phone_verified: boolean;
  capabilities: string[];
};

export type AuthResponse = { access: string; refresh: string; user: User };

export type ResetRequestResponse = { detail: string; retry_in: number; otp_ttl: number };

export const authApi = {
  register: (payload: {
    phone: string;
    password: string;
    password_confirm: string;
    first_name: string;
    last_name: string;
  }) => request<{ phone_masked: string; otp_ttl: number; retry_in: number }>("/auth/register/", {
    method: "POST",
    body: payload,
  }),

  verifyOtp: (payload: { phone: string; code: string; purpose?: string }) =>
    request<AuthResponse>("/auth/otp/verify/", { method: "POST", body: payload }),

  resendOtp: (payload: { phone: string; purpose?: string }) =>
    request<{ detail: string }>("/auth/otp/resend/", { method: "POST", body: payload }),

  login: (payload: { phone: string; password: string }) =>
    request<AuthResponse>("/auth/login/", { method: "POST", body: payload }),

  logout: (refresh: string) => request<void>("/auth/logout/", { method: "POST", body: { refresh }, auth: true }),

  me: () => request<User>("/auth/me/", { auth: true }),

  /** MVP-017 — réponse volontairement neutre (pas d'énumération de compte). */
  requestPasswordReset: (payload: { phone: string }) =>
    request<ResetRequestResponse>("/auth/password/reset/request/", { method: "POST", body: payload }),

  confirmPasswordReset: (payload: {
    phone: string;
    code: string;
    new_password: string;
    new_password_confirm: string;
  }) =>
    request<{ detail: string; sessions_revoked: boolean }>("/auth/password/reset/confirm/", {
      method: "POST",
      body: payload,
      idempotencyKey: crypto.randomUUID(),
    }),

  requestEmailVerification: (payload: { email: string }) =>
    request<{ detail: string }>("/auth/email/request/", {
      method: "POST",
      body: payload,
      auth: true,
    }),

  confirmEmailVerification: (payload: { email: string; code: string }) =>
    request<User>("/auth/email/confirm/", { method: "POST", body: payload, auth: true }),

  changePassword: (payload: {
    current_password: string;
    new_password: string;
    new_password_confirm: string;
  }) =>
    request<{ detail: string; sessions_revoked: boolean; access: string; refresh: string }>(
      "/auth/password/change/",
      { method: "POST", body: payload, auth: true },
    ),
};
