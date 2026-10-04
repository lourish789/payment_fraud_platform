// Every English message in the console must have a translation in every language, with the same {placeholders}.
// Messages are found by scanning the source for t("..."), tr("...") and msg("...") literals.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { CATALOGS, translate } from "./index";

const SRC = join(__dirname, "..");
const CALL = /\b(?:t|tr|msg)\(\s*"((?:[^"\\]|\\.)*)"/g;

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) return f === "locales" ? [] : sources(p);
    return /\.tsx?$/.test(f) && !f.endsWith(".test.ts") ? [p] : [];
  });
}

export function messages(): string[] {
  const found = new Set<string>();
  for (const file of sources(SRC)) {
    for (const m of readFileSync(file, "utf8").matchAll(CALL)) found.add(JSON.parse(`"${m[1]}"`));
  }
  return [...found].sort();
}

const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

describe("translation catalogs", () => {
  const ids = messages();

  it("finds the console's messages", () => {
    expect(ids.length).toBeGreaterThan(300);
    expect(ids).toContain("Case queue");
  });

  for (const [locale, catalog] of Object.entries(CATALOGS)) {
    it(`${locale} translates every message and keeps its placeholders`, () => {
      const missing = ids.filter((id) => !catalog[id]?.trim());
      expect(missing, `${locale} is missing translations`).toEqual([]);
      const broken = ids.filter((id) => placeholders(id).join() !== placeholders(catalog[id]).join());
      expect(broken, `${locale} changes placeholders`).toEqual([]);
      const unused = Object.keys(catalog).filter((k) => !ids.includes(k));
      expect(unused, `${locale} has entries no code uses`).toEqual([]);
    });
  }

  it("interpolates and falls back to English", () => {
    expect(translate("fr", "Cases: {n}", { n: 3 })).toBe(CATALOGS.fr["Cases: {n}"].replace("{n}", "3"));
    expect(translate("yo", "a string nobody translated")).toBe("a string nobody translated");
    expect(translate("en", "{from}-{to} of {total}", { from: 1, to: 25, total: 90 })).toBe("1-25 of 90");
  });
});
