// Language and display currency for the console.
//
// Messages are keyed by their English text (gettext style): t("Case queue") renders the translation for the
// current language, or the English if there is none. src/i18n/catalog.test.ts scans the source for every
// t() and msg() string literal and fails if a language is missing one, so a new string can't ship
// untranslated by accident.
//
// Text the API writes (error messages, decision reasons, explanations, agent reports) is translated by the
// server: the client sends Accept-Language, and the server also knows the key's profile language.
//
// Choice order: the key's profile (set from any device, follows the user) > this browser's last choice >
// the browser language > English. Changing it here saves it to the profile.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { apiUrl, setLocaleHeader } from "@/api/client";
import { setFormatContext } from "@/lib/format";
import { fr } from "./locales/fr";
import { ha } from "./locales/ha";
import { ig } from "./locales/ig";
import { pcm } from "./locales/pcm";
import { yo } from "./locales/yo";

export const LOCALES = [
  { code: "en", name: "English", intl: "en-NG" },
  { code: "fr", name: "Français", intl: "fr-FR" },
  { code: "yo", name: "Yorùbá", intl: "yo-NG" },
  { code: "ha", name: "Hausa", intl: "ha-NG" },
  { code: "ig", name: "Igbo", intl: "ig-NG" },
  { code: "pcm", name: "Naijá (Pidgin)", intl: "en-NG" },
] as const;
export type Locale = (typeof LOCALES)[number]["code"];
export const CATALOGS: Record<Exclude<Locale, "en">, Record<string, string>> = { fr, yo, ha, ig, pcm };

/** Marks an English string for translation where it is defined (e.g. a nav label) rather than rendered. */
export const msg = (s: string): string => s;

export type Params = Record<string, string | number | null | undefined>;

export function translate(locale: Locale, text: string, params?: Params): string {
  const template = locale === "en" ? text : CATALOGS[locale][text] ?? text;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (m, k: string) => (params[k] === undefined || params[k] === null ? m : String(params[k])));
}

export function normalizeLocale(tag: string | null | undefined): Locale | null {
  if (!tag) return null;
  const t = tag.trim().toLowerCase().replace("_", "-");
  const hit = LOCALES.find((l) => l.code === t) ?? LOCALES.find((l) => l.code === t.split("-")[0]);
  return hit ? hit.code : null;
}

const LOCALE_KEY = "payguard.locale";
const CURRENCY_KEY = "payguard.currency";

function readPref(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writePref(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage blocked: the choice lasts until reload */
  }
}

function initialLocale(): Locale {
  return normalizeLocale(readPref(LOCALE_KEY))
    ?? (typeof navigator !== "undefined" ? (navigator.languages ?? [navigator.language]).map(normalizeLocale).find(Boolean) : null)
    ?? "en";
}

export interface Rates {
  rates: Record<string, number>; // units per 1 USD
  display: string[];
}

interface I18nState {
  locale: Locale;
  currency: string;
  rates: Rates;
  t: (text: string, params?: Params) => string;
  setLocale: (l: Locale, opts?: { persist?: boolean }) => void;
  setCurrency: (c: string, opts?: { persist?: boolean }) => void;
  setRates: (r: Rates) => void;
  /** Called with the new value when the user changes a preference (AuthContext saves it to the profile). */
  onUserChange: (fn: ((p: { locale?: Locale; currency?: string }) => void) | null) => void;
}

const I18nContext = createContext<I18nState | null>(null);
const DEFAULT_RATES: Rates = { rates: { USD: 1 }, display: ["USD"] };

function apply(locale: Locale, currency: string, rates: Rates) {
  const intl = LOCALES.find((l) => l.code === locale)!.intl;
  setLocaleHeader(locale);
  setFormatContext({ intl, currency, rates: rates.rates });
  if (typeof document !== "undefined") document.documentElement.lang = locale;
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  const [currency, setCurrencyState] = useState<string>(() => readPref(CURRENCY_KEY) ?? "USD");
  const [rates, setRates] = useState<Rates>(DEFAULT_RATES);
  // A ref, not state: the setters below must stay stable, or every consumer that depends on them (AuthContext's
  // login, and through it the session-restore effect) would re-run each time a listener is registered.
  const listener = useRef<((p: { locale?: Locale; currency?: string }) => void) | null>(null);
  const effectiveCurrency = rates.rates[currency] ? currency : "USD";
  apply(locale, effectiveCurrency, rates); // synchronously, so children format with the new values this render

  useEffect(() => {
    fetch(apiUrl("/v1/meta"))
      .then((r) => (r.ok ? r.json() : null))
      .then((m) => m?.currencies && setRates({ rates: m.currencies.rates, display: m.currencies.display }))
      .catch(() => undefined); // offline/older server: USD only
  }, []);

  const setLocale = useCallback((l: Locale, opts?: { persist?: boolean }) => {
    setLocaleState(l);
    writePref(LOCALE_KEY, l);
    if (opts?.persist !== false) listener.current?.({ locale: l });
  }, []);

  const setCurrency = useCallback((c: string, opts?: { persist?: boolean }) => {
    setCurrencyState(c);
    writePref(CURRENCY_KEY, c);
    if (opts?.persist !== false) listener.current?.({ currency: c });
  }, []);

  const onUserChange = useCallback((fn: ((p: { locale?: Locale; currency?: string }) => void) | null) => {
    listener.current = fn;
  }, []);

  const value = useMemo<I18nState>(() => ({
    locale, currency: effectiveCurrency, rates,
    t: (text, params) => translate(locale, text, params),
    setLocale, setCurrency, setRates, onUserChange,
  }), [locale, effectiveCurrency, rates, setLocale, setCurrency, onUserChange]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nState {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n outside I18nProvider");
  return ctx;
}

/** The translate function for the current language (re-renders the caller when the language changes). */
export function useT() {
  return useI18n().t;
}

/** Language and display-currency pickers (top bar and sign-in page). */
export function LocalePicker({ compact }: { compact?: boolean }) {
  const { locale, setLocale, currency, setCurrency, rates, t } = useI18n();
  return (
    <div className="row locale-picker">
      <label className={compact ? "inline-field" : "field"}>
        <span className={compact ? "sr-only" : undefined}>{t("Language")}</span>
        <select className="select small" value={locale} aria-label={t("Language")} onChange={(e) => setLocale(e.target.value as Locale)}>
          {LOCALES.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
        </select>
      </label>
      {rates.display.length > 1 && (
        <label className={compact ? "inline-field" : "field"}>
          <span className={compact ? "sr-only" : undefined}>{t("Show amounts in")}</span>
          <select className="select small" value={currency} aria-label={t("Show amounts in")} onChange={(e) => setCurrency(e.target.value)}>
            {rates.display.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
      )}
    </div>
  );
}
