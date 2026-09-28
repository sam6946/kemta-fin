/**
 * E2E léger du parcours « mot de passe oublié » côté interface (MVP-017).
 * Le serveur est simulé : on vérifie ici les états de l'écran et la charge utile envoyée.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import ForgotPasswordPage from "../ForgotPasswordPage";
import ResetPasswordPage from "../ResetPasswordPage";

function mockFetch(handlers: Record<string, () => Response | Promise<Response>>) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = `${init?.method ?? "GET"} ${url}`;
    const handler = handlers[key];
    if (!handler) throw new Error(`Requête inattendue : ${key}`);
    return handler();
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ForgotPasswordPage", () => {
  it("affiche le message neutre après une demande sur numéro inconnu", async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch({
      "POST /api/auth/password/reset/request/": () =>
        new Response(
          JSON.stringify({
            detail: "Si ce numéro est associé à un compte KEMTA, un code vient d'être envoyé par SMS.",
            retry_in: 60,
            otp_ttl: 300,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    }) as unknown as typeof fetch;

    render(
      <MemoryRouter>
        <ForgotPasswordPage />
      </MemoryRouter>,
    );

    await user.type(screen.getByLabelText(/numéro de téléphone/i), "699000111");
    await user.click(screen.getByRole("button", { name: /recevoir un code/i }));

    expect(await screen.findByText(/si ce numéro est associé à un compte/i)).toBeInTheDocument();
  });

  it("explique le quota de renvoi (429) sans fuite d'information", async () => {
    const user = userEvent.setup();
    globalThis.fetch = mockFetch({
      "POST /api/auth/password/reset/request/": () =>
        new Response(
          JSON.stringify({
            error: {
              code: "otp_resend_limited",
              message: "Merci de patienter 60 s avant de renvoyer un code.",
              details: { retry_in: 60 },
              request_id: "abc",
            },
          }),
          { status: 429, headers: { "Content-Type": "application/json" } },
        ),
    }) as unknown as typeof fetch;

    render(
      <MemoryRouter>
        <ForgotPasswordPage />
      </MemoryRouter>,
    );

    await user.type(screen.getByLabelText(/numéro de téléphone/i), "699000111");
    await user.click(screen.getByRole("button", { name: /recevoir un code/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/patientez 60 secondes/i);
  });
});

describe("ResetPasswordPage", () => {
  const submitButtonName = /réinitialiser mon mot de passe/i;

  function renderReset(phone = "+237690123456") {
    return render(
      <MemoryRouter initialEntries={[{ pathname: "/reinitialiser-mot-de-passe", state: { phone } }]}>
        <ResetPasswordPage />
      </MemoryRouter>,
    );
  }

  it("bloque l'envoi tant que le mot de passe ne respecte pas les règles", async () => {
    const user = userEvent.setup();
    renderReset();

    await user.type(screen.getByLabelText(/code reçu par sms/i), "123456");
    await user.type(screen.getByLabelText(/^nouveau mot de passe/i), "abc");
    await user.type(screen.getByLabelText(/confirmer le mot de passe/i), "abc");
    await user.click(screen.getByRole("button", { name: submitButtonName }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/trop faible/i);
  });

  it("signale une confirmation différente", async () => {
    const user = userEvent.setup();
    renderReset();

    await user.type(screen.getByLabelText(/code reçu par sms/i), "123456");
    await user.type(screen.getByLabelText(/^nouveau mot de passe/i), "Kemta#2026Douala");
    await user.type(screen.getByLabelText(/confirmer le mot de passe/i), "Kemta#2026DoualaX");

    expect(screen.getByText(/ne sont pas identiques/i)).toBeInTheDocument();
  });

  it("envoie le code, le numéro et le nouveau mot de passe au backend", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch({
      "POST /api/auth/password/reset/confirm/": () =>
        new Response(JSON.stringify({ detail: "Mot de passe réinitialisé.", sessions_revoked: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    renderReset();
    await user.type(screen.getByLabelText(/code reçu par sms/i), "123456");
    await user.type(screen.getByLabelText(/^nouveau mot de passe/i), "Kemta#2026Douala");
    await user.type(screen.getByLabelText(/confirmer le mot de passe/i), "Kemta#2026Douala");
    await user.click(screen.getByRole("button", { name: submitButtonName }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, init] = fetchMock.mock.calls[0];
    const payload = JSON.parse(String(init?.body));
    expect(payload).toMatchObject({
      phone: "+237690123456",
      code: "123456",
      new_password: "Kemta#2026Douala",
    });
    expect((init?.headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();

    expect(await screen.findByText(/toutes vos sessions actives ont été déconnectées/i)).toBeInTheDocument();
  });

  it("affiche un état explicite quand le réseau est absent", async () => {
    const user = userEvent.setup();
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError("Network request failed");
    }) as unknown as typeof fetch;

    renderReset();
    await user.type(screen.getByLabelText(/code reçu par sms/i), "123456");
    await user.type(screen.getByLabelText(/^nouveau mot de passe/i), "Kemta#2026Douala");
    await user.type(screen.getByLabelText(/confirmer le mot de passe/i), "Kemta#2026Douala");
    await user.click(screen.getByRole("button", { name: submitButtonName }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/pas de connexion/i);
  });
});
