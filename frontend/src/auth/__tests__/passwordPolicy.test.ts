import { describe, expect, it } from "vitest";

import { evaluatePassword, messageForErrorCode, PASSWORD_MIN_LENGTH } from "../passwordPolicy";

describe("politique de mot de passe (miroir du backend)", () => {
  it("refuse un mot de passe trop court", () => {
    expect(evaluatePassword("Kemta1").valid).toBe(false);
  });

  it("refuse un mot de passe uniquement numérique", () => {
    expect(evaluatePassword("1234567890").valid).toBe(false);
  });

  it("refuse un mot de passe sans chiffre", () => {
    expect(evaluatePassword("KemtaDoualaChantier").valid).toBe(false);
  });

  it("accepte un mot de passe conforme", () => {
    const result = evaluatePassword("Kemta#2026Douala");
    expect(result.valid).toBe(true);
    expect(result.score).toBeGreaterThanOrEqual(4);
  });

  it("signale le mélange de casses comme recommandation", () => {
    const checks = evaluatePassword("kemta#2026douala").checks;
    const mixedCase = checks.find((check) => check.id === "mixed_case");
    expect(mixedCase?.ok).toBe(false);
    expect(checks.find((check) => check.id === "length")?.ok).toBe(true);
  });

  it("expose la longueur minimale attendue par le backend", () => {
    expect(PASSWORD_MIN_LENGTH).toBe(10);
  });
});

describe("messages d'erreur", () => {
  it("explique le mode hors ligne pour la réinitialisation", () => {
    expect(messageForErrorCode("offline")).toMatch(/réseau/i);
  });

  it("reformule le dépassement de tentatives OTP", () => {
    expect(messageForErrorCode("otp_max_attempts")).toMatch(/nouveau code/i);
  });

  it("retombe sur un message générique pour un code inconnu", () => {
    expect(messageForErrorCode("inconnu_total")).toMatch(/erreur/i);
  });
});
