/**
 * Client HTTP unique.
 *
 * - enveloppe d'erreur backend `{ error: { code, message, details, request_id } }` ;
 * - un seul essai de refresh sur `401`, puis purge de la session (pas de boucle infinie) ;
 * - toute panne réseau devient une erreur `offline` exploitable par l'interface.
 */

export type ApiErrorCode =
  | "offline"
  | "rate_limited"
  | "otp_invalid"
  | "otp_max_attempts"
  | "otp_resend_limited"
  | "password_too_weak"
  | "password_reused"
  | "password_mismatch"
  | "invalid_credentials"
  | "account_not_confirmed"
  | "account_locked"
  | "phone_invalid"
  | "phone_already_used"
  | "phone_pending_activation"
  | string;

export class ApiError extends Error {
  code: ApiErrorCode;
  status: number;
  details: Record<string, unknown>;

  constructor(code: ApiErrorCode, message: string, status: number, details: Record<string, unknown> = {}) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
  }

  /** Vrai quand l'action exige le réseau : l'interface le dit explicitement. */
  get isOffline(): boolean {
    return this.code === "offline";
  }
}

const ACCESS_KEY = "kemta.access";
const REFRESH_KEY = "kemta.refresh";

export const tokens = {
  get access() {
    // L'access token reste en mémoire autant que possible ; le stockage est un repli
    // assumé pour la PWA (voir docs/architecture.md, §6).
    return sessionStorage.getItem(ACCESS_KEY);
  },
  get refresh() {
    return sessionStorage.getItem(REFRESH_KEY);
  },
  set(access: string | null, refresh: string | null) {
    if (access) sessionStorage.setItem(ACCESS_KEY, access);
    else sessionStorage.removeItem(ACCESS_KEY);
    if (refresh) sessionStorage.setItem(REFRESH_KEY, refresh);
    else sessionStorage.removeItem(REFRESH_KEY);
  },
  clear() {
    sessionStorage.removeItem(ACCESS_KEY);
    sessionStorage.removeItem(REFRESH_KEY);
  },
};

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  auth?: boolean;
  idempotencyKey?: string;
  retryOnUnauthorized?: boolean;
};

type ErrorPayload = {
  error?: { code?: string; message?: string; details?: Record<string, unknown> };
};

async function parseError(response: Response): Promise<ApiError> {
  let payload: ErrorPayload | null = null;
  try {
    payload = (await response.json()) as ErrorPayload;
  } catch {
    payload = null; // réponse non JSON (proxy, coupure réseau) : erreur générique
  }
  const error = payload?.error;
  return new ApiError(
    error?.code ?? `http_${response.status}`,
    error?.message ?? "Une erreur est survenue.",
    response.status,
    error?.details ?? {},
  );
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, auth = false, idempotencyKey, retryOnUnauthorized = true } = options;

  const headers: Record<string, string> = { Accept: "application/json" };
  // `multipart/form-data` (preuves terrain) : le navigateur pose lui-même le Content-Type,
  // frontière comprise — on ne doit surtout pas l'écraser.
  const isMultipart = typeof FormData !== "undefined" && body instanceof FormData;
  if (body !== undefined && !isMultipart) headers["Content-Type"] = "application/json";
  if (auth && tokens.access) headers.Authorization = `Bearer ${tokens.access}`;
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;

  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : isMultipart ? (body as FormData) : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("offline", "Pas de connexion. Vérifiez votre réseau puis réessayez.", 0);
  }

  if (response.status === 401 && auth && retryOnUnauthorized && (await tryRefresh())) {
    return request<T>(path, { ...options, retryOnUnauthorized: false });
  }

  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function tryRefresh(): Promise<boolean> {
  const refresh = tokens.refresh;
  if (!refresh) return false;
  try {
    const response = await fetch("/api/auth/token/refresh/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh }),
    });
    if (!response.ok) {
      tokens.clear();
      return false;
    }
    const data = (await response.json()) as { access: string; refresh?: string };
    tokens.set(data.access, data.refresh ?? refresh);
    return true;
  } catch {
    return false;
  }
}
