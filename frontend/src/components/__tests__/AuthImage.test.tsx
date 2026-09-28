import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { tokens } from "../../api/client";
import AuthImage from "../AuthImage";

beforeEach(() => {
  Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:thumb"), configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
  tokens.set("access-token", "refresh-token");
});

afterEach(() => {
  vi.unstubAllGlobals();
  tokens.clear();
});

describe("AuthImage", () => {
  it("récupère la vignette avec le jeton puis affiche une URL objet", async () => {
    const fetchMock = vi.fn(async () => new Response("binary", { headers: { "Content-Type": "image/webp" } }));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthImage src="/api/evidences/5/thumbnail/" alt="Preuve 5" />);

    const image = await screen.findByAltText("Preuve 5");
    expect(image).toHaveAttribute("src", "blob:thumb");
    expect(image).toHaveAttribute("loading", "lazy");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/evidences/5/thumbnail/");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer access-token");
  });

  it("n'affiche pas d'icône cassée quand le fichier est indisponible", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: { code: "file_not_available" } }), { status: 404 })),
    );

    render(<AuthImage src="/api/evidences/5/thumbnail/" alt="Preuve 5" />);

    await waitFor(() => expect(screen.getByText("Image indisponible")).toBeInTheDocument());
    expect(screen.queryByRole("img", { hidden: false, name: "Preuve 5" })).not.toBeInTheDocument();
  });

  it("attend d'être proche de l'écran avant de télécharger (chargement différé)", async () => {
    const observers: Array<{ callback: IntersectionObserverCallback; disconnect: () => void }> = [];
    class FakeObserver {
      constructor(callback: IntersectionObserverCallback) {
        observers.push({ callback, disconnect: () => undefined });
      }
      observe() {}
      disconnect() {}
      unobserve() {}
      takeRecords() {
        return [];
      }
    }
    vi.stubGlobal("IntersectionObserver", FakeObserver);
    const fetchMock = vi.fn(async () => new Response("binary"));
    vi.stubGlobal("fetch", fetchMock);

    render(<AuthImage src="/api/evidences/9/thumbnail/" alt="Preuve 9" />);
    expect(fetchMock).not.toHaveBeenCalled();

    observers[0].callback([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);

    await screen.findByAltText("Preuve 9");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
