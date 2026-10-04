import { RAILS } from "@/api/types";
import { msg, type Params } from "@/i18n";
import { codeLabel, RAIL_LABEL } from "@/i18n/labels";

type T = (text: string, params?: Params) => string;

export const railOptions = (t: T) => [{ value: "all", label: t("All rails") }, ...RAILS.map((r) => ({ value: r, label: t(RAIL_LABEL[r]) }))];

/** Options for a filter over API codes, with "all" first. */
export const codeOptions = (t: T, codes: string[], all = msg("All")) =>
  [{ value: "all", label: t(all) }, ...codes.map((c) => ({ value: c, label: t(codeLabel(c)) }))];

export const decisionOptions = (t: T) => codeOptions(t, ["approve", "review", "decline"]);
