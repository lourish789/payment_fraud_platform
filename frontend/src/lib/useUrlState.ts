import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

/** Filters and pagination live in the URL, so every view of a list is linkable and survives reloads.
 * Changing a filter resets the offset to the first page. */
export function useUrlState<K extends string>(defaults: Record<K, string>) {
  const [params, setParams] = useSearchParams();
  const values = Object.fromEntries(
    (Object.keys(defaults) as K[]).map((k) => [k, params.get(k) ?? defaults[k]]),
  ) as Record<K, string>;
  const offset = Number(params.get("offset") ?? 0) || 0;

  const set = useCallback(
    (key: K, value: string) => {
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        if (value === defaults[key] || value === "") next.delete(key);
        else next.set(key, value);
        next.delete("offset");
        return next;
      }, { replace: true });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [setParams],
  );

  const setOffset = useCallback(
    (o: number) => {
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        if (o > 0) next.set("offset", String(o));
        else next.delete("offset");
        return next;
      });
    },
    [setParams],
  );

  return { values, set, offset, setOffset };
}

/** Undefined for "all"/empty so the API client drops the parameter. */
export const opt = (v: string): string | undefined => (v && v !== "all" ? v : undefined);
