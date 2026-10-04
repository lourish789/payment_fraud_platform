// Display formatting. Pure functions over a small formatting context (language and display currency) that the
// I18nProvider sets before each render; unit-tested in format.test.ts.
//
// Money comes in two kinds, and the difference matters:
//   money(amount, currency)  an amount as it was paid, in its own currency (₦465,000.00, 0.2 BTC)
//   usd(amountUsd)           a risk figure the API computes in USD (expected loss, volume, review cost),
//                            converted to the viewer's display currency (USD or NGN)

interface FormatContext {
  intl: string; // BCP 47 tag for Intl
  currency: string; // display currency
  rates: Record<string, number>; // units per 1 USD
}

let ctx: FormatContext = { intl: "en-US", currency: "USD", rates: { USD: 1 } };
const cache = new Map<string, Intl.NumberFormat>();

export function setFormatContext(next: FormatContext) {
  ctx = next;
}

function nf(opts: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = ctx.intl + JSON.stringify(opts);
  let f = cache.get(key);
  if (!f) {
    try {
      f = new Intl.NumberFormat(ctx.intl, opts);
    } catch {
      f = new Intl.NumberFormat("en-US", opts); // a locale or currency this browser's Intl doesn't know
    }
    cache.set(key, f);
  }
  return f;
}

const blank = (v: number | null | undefined): v is null | undefined => v === null || v === undefined || Number.isNaN(v);

/** An amount in its own currency. Fiat with a known rate gets its symbol; anything else (crypto) "0.25 BTC". */
export const money = (v: number | null | undefined, currency = "USD"): string => {
  if (blank(v)) return "-";
  const cur = currency.toUpperCase();
  if (cur in ctx.rates || cur === "USD") {
    return nf({ style: "currency", currency: cur, currencyDisplay: "narrowSymbol", maximumFractionDigits: 2 }).format(v);
  }
  return `${nf({ maximumFractionDigits: 8 }).format(v)} ${cur}`;
};

/** The currency usd() shows amounts in. */
export const displayCurrency = (): string => (ctx.rates[ctx.currency] ? ctx.currency : "USD");

/** A USD figure from the API, shown in the display currency. */
export const usd = (v: number | null | undefined): string => {
  if (blank(v)) return "-";
  const cur = displayCurrency();
  return money(v * (ctx.rates[cur] ?? 1), cur);
};

/** Compact form of usd() for KPIs and chart axes: $12.3K, ₦19.1M. */
export const usdCompact = (v: number | null | undefined): string => {
  if (blank(v)) return "-";
  const cur = displayCurrency();
  const x = v * (ctx.rates[cur] ?? 1);
  if (Math.abs(x) < 10_000) return money(x, cur);
  return nf({ style: "currency", currency: cur, currencyDisplay: "narrowSymbol", notation: "compact", maximumFractionDigits: 1 }).format(x);
};

export const int = (v: number | null | undefined): string => (blank(v) ? "-" : nf({ maximumFractionDigits: 0 }).format(v));

export const num = (v: number | null | undefined, digits = 2): string =>
  blank(v) ? "-" : nf({ minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v);

export const compactNum = (v: number | null | undefined): string =>
  blank(v) ? "-" : Math.abs(v) < 10_000 ? int(v) : nf({ notation: "compact", maximumFractionDigits: 1 }).format(v);

export const pct = (v: number | null | undefined, digits = 1): string =>
  blank(v) ? "-" : nf({ style: "percent", minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v);

/** Probabilities span orders of magnitude (0.0004 to 0.97); show small ones with more precision. */
export const prob = (v: number | null | undefined): string => (blank(v) ? "-" : pct(v, v > 0 && v < 0.01 ? 2 : 1));

export const dateTime = (iso: string | null | undefined): string => {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(ctx.intl, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC";
};

export const dateShort = (iso: string): string => {
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return iso.length === 10
    ? d.toLocaleDateString(ctx.intl, { month: "short", day: "2-digit", timeZone: "UTC" })
    : d.toLocaleString(ctx.intl, { month: "short", day: "2-digit", hour: "2-digit", timeZone: "UTC" });
};

export const duration = (seconds: number | null | undefined): string => {
  if (blank(seconds)) return "-";
  const s = Math.abs(seconds);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${num(s / 3600, 1)}h`;
  return `${num(s / 86400, 1)}d`;
};

export const humanize = (s: string): string => s.replace(/[_.:]/g, " ").replace(/\s+/g, " ").trim();
