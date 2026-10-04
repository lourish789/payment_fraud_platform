import { afterEach, describe, expect, it } from "vitest";
import { buildUrl } from "../api/client";
import { compactNum, displayCurrency, duration, money, pct, prob, setFormatContext, usd, usdCompact } from "./format";

const DEFAULT = { intl: "en-US", currency: "USD", rates: { USD: 1 } };
const NAIRA = { intl: "en-NG", currency: "NGN", rates: { USD: 1, NGN: 1550 } };

describe("format", () => {
  afterEach(() => setFormatContext(DEFAULT));

  it("formats money and percentages", () => {
    expect(money(1234.5)).toBe("$1,234.50");
    expect(money(0.25, "BTC")).toBe("0.25 BTC");
    expect(money(null)).toBe("-");
    expect(pct(0.0457)).toBe("4.6%");
  });

  it("keeps an amount in its own currency, and shows USD risk figures in the display currency", () => {
    setFormatContext(NAIRA);
    expect(money(465000, "NGN")).toBe("₦465,000.00");
    expect(money(300, "USD")).toBe("$300.00");
    expect(usd(300)).toBe("₦465,000.00"); // a USD figure converted for display
    expect(usdCompact(12_000_000)).toBe("₦18.6B");
    expect(displayCurrency()).toBe("NGN");
  });

  it("falls back to USD when the display currency has no rate", () => {
    setFormatContext({ ...DEFAULT, currency: "NGN" });
    expect(usd(10)).toBe("$10.00");
    expect(displayCurrency()).toBe("USD");
  });

  it("follows the language's number format", () => {
    setFormatContext({ ...DEFAULT, intl: "fr-FR" });
    expect(pct(0.0457)).toMatch(/^4,6\s?%$/);
    expect(money(1234.5, "USD")).toMatch(/^1\s?234,50\s?\$$/);
  });

  it("keeps precision for small probabilities", () => {
    expect(prob(0.0004)).toBe("0.04%");
    expect(prob(0.42)).toBe("42.0%");
  });

  it("formats durations and compact numbers", () => {
    expect(duration(42)).toBe("42s");
    expect(duration(7200)).toBe("2.0h");
    expect(duration(172800)).toBe("2.0d");
    expect(compactNum(89326)).toBe("89.3K");
  });
});

describe("buildUrl", () => {
  it("drops empty filters and encodes values", () => {
    expect(buildUrl("/v1/cases", { status: "open", rail: undefined, q: "", limit: 50 })).toBe("/v1/cases?status=open&limit=50");
    expect(buildUrl("/v1/transactions", { q: "a b" })).toBe("/v1/transactions?q=a+b");
  });
});
