// Display formatting. Pure functions (unit-tested in format.test.ts).

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const usdCompact = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
const intFmt = new Intl.NumberFormat("en-US");
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export const money = (v: number | null | undefined, currency = "USD"): string => {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  if (currency === "USD") return usd.format(v);
  return `${v.toLocaleString("en-US", { maximumFractionDigits: 8 })} ${currency}`;
};

export const moneyCompact = (v: number | null | undefined): string =>
  v === null || v === undefined ? "-" : Math.abs(v) < 10_000 ? usd.format(v) : usdCompact.format(v);

export const int = (v: number | null | undefined): string => (v === null || v === undefined ? "-" : intFmt.format(v));

export const num = (v: number | null | undefined, digits = 2): string =>
  v === null || v === undefined || Number.isNaN(v) ? "-" : v.toFixed(digits);

export const compactNum = (v: number | null | undefined): string =>
  v === null || v === undefined ? "-" : Math.abs(v) < 10_000 ? intFmt.format(v) : compact.format(v);

export const pct = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined || Number.isNaN(v) ? "-" : `${(v * 100).toFixed(digits)}%`;

/** Probabilities span orders of magnitude (0.0004 to 0.97); show small ones with more precision. */
export const prob = (v: number | null | undefined): string => {
  if (v === null || v === undefined) return "-";
  if (v > 0 && v < 0.01) return `${(v * 100).toFixed(2)}%`;
  return `${(v * 100).toFixed(1)}%`;
};

export const dateTime = (iso: string | null | undefined): string => {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC";
};

export const dateShort = (iso: string): string => {
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return iso.length === 10
    ? d.toLocaleDateString("en-GB", { month: "short", day: "2-digit", timeZone: "UTC" })
    : d.toLocaleString("en-GB", { month: "short", day: "2-digit", hour: "2-digit", timeZone: "UTC" });
};

export const duration = (seconds: number | null | undefined): string => {
  if (seconds === null || seconds === undefined) return "-";
  const s = Math.abs(seconds);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
};

export const RAIL_LABEL: Record<string, string> = {
  card: "Card",
  bank_transfer: "Bank transfer",
  mobile_money: "Mobile money",
  crypto: "Crypto",
};

export const humanize = (s: string): string => s.replace(/[_.:]/g, " ").replace(/\s+/g, " ").trim();
