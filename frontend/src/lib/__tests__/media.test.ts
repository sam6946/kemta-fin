/**
 * Traitement local des preuves : empreinte, GPS (refus, indisponibilité, délai), appareil.
 * La compression d'image est vérifiée côté API navigateur (canvas) dans un test dédié.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  capturedAtNow,
  deviceInfo,
  formatBytes,
  getPosition,
  newIdempotencyKey,
  sha256Hex,
} from "../media";

describe("sha256Hex", () => {
  it("calcule l'empreinte d'un contenu connu (vecteurs de référence)", async () => {
    // Vecteurs publiés : SHA-256("abc") et SHA-256("kemta").
    expect(await sha256Hex(new TextEncoder().encode("abc").buffer)).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    expect(await sha256Hex(new TextEncoder().encode("kemta").buffer)).toBe(
      "8d510aad14b0bca0ab51138d6b89f4207f709c149855f105ef3bc6c95a573e95",
    );
  });

  it("produit une empreinte stable et un hexadécimal de 64 caractères", async () => {
    const payload = new Uint8Array([1, 2, 3, 4, 5]).buffer;
    const first = await sha256Hex(payload);
    expect(first).toMatch(/^[0-9a-f]{64}$/);
    expect(await sha256Hex(payload)).toBe(first);
    expect(await sha256Hex(new Uint8Array([1, 2, 3, 4, 6]).buffer)).not.toBe(first);
  });
});

describe("getPosition", () => {
  const navigatorWithoutGeolocation = {} as unknown as Navigator;

  it("renvoie la position quand le GPS répond", async () => {
    const geolocation = {
      getCurrentPosition: (success: PositionCallback) =>
        success({ coords: { latitude: 4.0891, longitude: 9.7406, accuracy: 12 } } as GeolocationPosition),
    } as unknown as Geolocation;

    const result = await getPosition({ geolocation });

    expect(result.status).toBe("AVAILABLE");
    expect(result.latitude).toBeCloseTo(4.0891);
    expect(result.accuracy).toBe(12);
    expect(result.message).toMatch(/Position obtenue/);
  });

  it("explique un refus sans bloquer le dépôt", async () => {
    const geolocation = {
      getCurrentPosition: (_s: PositionCallback, error?: PositionErrorCallback) =>
        error?.({ code: 1, message: "User denied Geolocation" } as GeolocationPositionError),
    } as unknown as Geolocation;

    const result = await getPosition({ geolocation });

    expect(result.status).toBe("DENIED");
    expect(result.latitude).toBeNull();
    expect(result.message).toMatch(/refus/i);
  });

  it("distingue l'indisponibilité du refus", async () => {
    const geolocation = {
      getCurrentPosition: (_s: PositionCallback, error?: PositionErrorCallback) =>
        error?.({ code: 2, message: "Position unavailable" } as GeolocationPositionError),
    } as unknown as Geolocation;

    const result = await getPosition({ geolocation });

    expect(result.status).toBe("UNAVAILABLE");
    expect(result.message).toMatch(/indisponible/i);
  });

  it("n'attend pas indéfiniment si le navigateur ne rappelle jamais", async () => {
    vi.useFakeTimers();
    const geolocation = {
      getCurrentPosition: () => undefined,
    } as unknown as Geolocation;

    const promise = getPosition({ geolocation, timeoutMs: 100 });
    await vi.advanceTimersByTimeAsync(1000);
    const result = await promise;

    expect(result.status).toBe("UNAVAILABLE");
    expect(result.message).toMatch(/délai/i);
    vi.useRealTimers();
  });

  it("gère un appareil sans géolocalisation", async () => {
    const result = await getPosition({ geolocation: undefined });
    expect(result.status).toBe("UNAVAILABLE");
    expect(result.message).toMatch(/pas de position/i);
    expect(navigatorWithoutGeolocation).toBeDefined();
  });
});

describe("deviceInfo", () => {
  it("reconnaît un téléphone Android et son modèle", () => {
    const info = deviceInfo({
      userAgent:
        "Mozilla/5.0 (Linux; Android 13; Tecno Spark 10) AppleWebKit/537.36 Chrome/120 Mobile",
    } as Navigator);

    expect(info.device_platform).toBe("Android");
    expect(info.device_model).toBe("Tecno Spark 10");
  });

  it("reconnaît un iPhone", () => {
    const info = deviceInfo({
      userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15",
    } as Navigator);

    expect(info.device_platform).toBe("iOS");
    expect(info.device_model).toBe("iPhone");
  });

  it("reste robuste sur un agent inconnu", () => {
    const info = deviceInfo({ userAgent: "" } as Navigator);
    expect(info.device_platform).toBe("Inconnu");
    expect(info.device_model).toBe("Navigateur");
  });
});

describe("utilitaires", () => {
  it("formate les tailles de fichier", () => {
    expect(formatBytes(512)).toBe("512 o");
    expect(formatBytes(2048)).toBe("2 Ko");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 Mo");
  });

  it("génère des clés d'idempotence distinctes et sûres pour une URL", () => {
    const keys = new Set(Array.from({ length: 50 }, () => newIdempotencyKey()));
    expect(keys.size).toBe(50);
    for (const key of keys) expect(key).toMatch(/^[A-Za-z0-9-]+$/);
  });

  it("horodate la capture en ISO (comparable côté serveur)", () => {
    const iso = capturedAtNow(new Date("2026-09-26T09:30:00Z"));
    expect(iso).toBe("2026-09-26T09:30:00.000Z");
    expect(new Date(iso).getTime()).toBe(Date.parse("2026-09-26T09:30:00Z"));
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});
