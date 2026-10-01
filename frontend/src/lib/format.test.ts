import { describe, expect, it } from "vitest";
import { buildUrl } from "../api/client";
import { compactNum, duration, money, pct, prob } from "./format";

describe("format", () => {
  it("formats money and percentages", () => {
    expect(money(1234.5)).toBe("$1,234.50");
    expect(money(0.25, "BTC")).toBe("0.25 BTC");
    expect(money(null)).toBe("-");
    expect(pct(0.0457)).toBe("4.6%");
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
