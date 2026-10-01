import { RAILS } from "@/api/types";
import { RAIL_LABEL } from "@/lib/format";

export const RAIL_OPTIONS = [{ value: "all", label: "All rails" }, ...RAILS.map((r) => ({ value: r, label: RAIL_LABEL[r] }))];

export const DECISION_OPTIONS = [
  { value: "all", label: "All" },
  { value: "approve", label: "Approve" },
  { value: "review", label: "Review" },
  { value: "decline", label: "Decline" },
];
