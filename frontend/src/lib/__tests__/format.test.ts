import { describe, expect, it } from "vitest";

import { formatDate, formatFcfa, formatPercent, parseFcfaInput } from "../format";

describe("formatFcfa", () => {
  it("formate un montant entier en FCFA", () => {
    expect(formatFcfa(85000000).replace(/\u202f|\u00a0/g, " ")).toBe("85 000 000 FCFA");
  });

  it("accepte une chaîne renvoyée par l'API (Decimal)", () => {
    expect(formatFcfa("50000000").replace(/\u202f|\u00a0/g, " ")).toContain("50 000 000");
  });

  it("affiche un tiret quand la valeur est absente", () => {
    expect(formatFcfa(null)).toBe("—");
    expect(formatFcfa(undefined)).toBe("—");
  });
});

describe("parseFcfaInput", () => {
  it("accepte un montant entier avec espaces", () => {
    expect(parseFcfaInput("85 000 000")).toBe(85000000);
  });

  it("refuse les centimes (saisie non entière)", () => {
    expect(parseFcfaInput("85000000,50")).toBeNull();
    expect(parseFcfaInput("85000000.50")).toBeNull();
  });

  it("refuse une saisie non numérique", () => {
    expect(parseFcfaInput("quatre-vingt")).toBeNull();
  });

  it("accepte un champ vide comme zéro", () => {
    expect(parseFcfaInput("")).toBe(0);
  });
});

describe("formatDate et formatPercent", () => {
  it("formate une date ISO", () => {
    expect(formatDate("2026-12-18")).toMatch(/2026/);
  });

  it("affiche un tiret pour une date absente", () => {
    expect(formatDate(null)).toBe("—");
  });

  it("formate un pourcentage renvoyé par le serveur", () => {
    expect(formatPercent("62.50")).toContain("62,5");
    expect(formatPercent(0)).toContain("0");
  });
});
