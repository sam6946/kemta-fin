/** Formatage : montants FCFA (entiers) et dates locales. */

export function formatFcfa(amount: number | string | null | undefined): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  const value = typeof amount === "string" ? Number(amount) : amount;
  if (!Number.isFinite(value)) return "—";
  return `${value.toLocaleString("fr-FR")} FCFA`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "numeric" });
}

export function formatPercent(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const number = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(number)) return "—";
  return `${number.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} %`;
}

/** Les montants sont saisis en FCFA entiers : on refuse les centimes côté formulaire. */
export function parseFcfaInput(raw: string): number | null {
  const cleaned = raw.replace(/\s|\u00a0/g, "").replace(",", ".");
  if (!cleaned) return 0;
  if (!/^\d+$/.test(cleaned)) return null;
  const value = Number(cleaned);
  return Number.isSafeInteger(value) ? value : null;
}
