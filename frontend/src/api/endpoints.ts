// One function per backend endpoint, grouped by resource. Pages never build URLs themselves.
import { get, patch, post, request } from "./client";
import type {
  ApiClient,
  ApiClientCreated,
  AuditEntry,
  CaseDetail,
  CaseSummary,
  DriftReport,
  EventsReport,
  Investigation,
  LabelRow,
  Me,
  ModelMetadata,
  Overview,
  Page,
  Preferences,
  RailOut,
  ReceiptDetail,
  ReceiptResult,
  ReceiptSummary,
  Registry,
  Role,
  RuleOut,
  ScoreResponse,
  SystemInfo,
  Timeseries,
  TransactionDetail,
  TransactionSummary,
} from "./types";

export interface Paging {
  limit?: number;
  offset?: number;
}

export type WindowName = "24h" | "7d" | "30d" | "90d" | "all";
export type Anchor = "latest_event" | "now";

export const auth = {
  me: (key?: string) => request<Me>("GET", "/v1/auth/me", { key }),
  /** Omitted fields are unchanged; null clears a preference. */
  setPreferences: (p: Partial<Preferences>) => patch<Me>("/v1/auth/me/preferences", p),
};

export const scoring = {
  score: (payment: Record<string, unknown>) => post<ScoreResponse>("/v1/payments/score", payment),
};

export const transactions = {
  list: (q: Paging & { rail?: string; decision?: string; label?: string; q?: string; min_amount?: number }) =>
    get<Page<TransactionSummary>>("/v1/transactions", q),
  get: (id: string) => get<TransactionDetail>(`/v1/transactions/${encodeURIComponent(id)}`),
};

export const cases = {
  list: (q: Paging & { status?: string; rail?: string; decision?: string; resolution?: string }) =>
    get<Page<CaseSummary>>("/v1/cases", q),
  get: (id: string) => get<CaseDetail>(`/v1/cases/${encodeURIComponent(id)}`),
  resolve: (id: string, resolution: "fraud" | "legit", note?: string) =>
    post<{ case_id: string; status: string; resolution: string }>(`/v1/cases/${encodeURIComponent(id)}/resolve`, {
      resolution,
      note: note || null,
    }),
  investigate: (id: string) =>
    post<{ investigation_id: string; status: string }>(`/v1/cases/${encodeURIComponent(id)}/investigate`),
};

export const investigations = {
  list: (q: Paging & { status?: string; recommendation?: string; case_id?: string }) =>
    get<Page<Investigation>>("/v1/investigations", q),
  get: (id: string, includeTrace = false) =>
    get<Investigation>(`/v1/investigations/${encodeURIComponent(id)}`, { include_trace: includeTrace }),
};

export const labels = {
  list: (q: Paging & { source?: string; is_fraud?: boolean }) => get<Page<LabelRow>>("/v1/labels", q),
};

export const receipts = {
  list: (q: Paging & { verdict?: string; case_id?: string }) => get<Page<ReceiptSummary>>("/v1/receipts", q),
  get: (id: string) => get<ReceiptDetail>(`/v1/receipts/${encodeURIComponent(id)}`),
  verify: (file: File, claimedReference?: string, caseId?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (claimedReference) form.append("claimed_reference", claimedReference);
    if (caseId) form.append("case_id", caseId);
    return request<ReceiptResult>("POST", "/v1/receipts/verify", { form });
  },
};

export const monitoring = {
  drift: () => get<DriftReport>("/v1/monitoring/drift"),
};

export const models = {
  registry: () => get<Registry>("/v1/models"),
  get: (version: string) => get<ModelMetadata>(`/v1/models/${encodeURIComponent(version)}`),
  promote: (alias: "champion" | "challenger", version: string | null, reason: string) =>
    post<Registry>("/v1/models/promote", { alias, version, reason }),
};

type WindowQuery = { window?: WindowName; anchor?: Anchor };

export const admin = {
  overview: (q: WindowQuery) => get<Overview>("/v1/admin/overview", q),
  timeseries: (q: WindowQuery & { bucket?: "hour" | "day" }) => get<Timeseries>("/v1/admin/timeseries", q),
  rails: () => get<RailOut[]>("/v1/admin/rails"),
  rules: () => get<{ sampled_decisions: number; rules: RuleOut[] }>("/v1/admin/rules"),
  events: (q: Paging & { topic?: string; pending?: boolean }) => get<EventsReport>("/v1/admin/events", q),
  system: () => get<SystemInfo>("/v1/admin/system"),
  clients: (q: Paging & { role?: string; active?: boolean }) => get<Page<ApiClient>>("/v1/admin/clients", q),
  createClient: (name: string, role: Role, prefs: Partial<Preferences> = {}) =>
    post<ApiClientCreated>("/v1/admin/clients", { name, role, locale: prefs.locale || null, currency: prefs.currency || null }),
  revokeClient: (id: string) => post<ApiClient>(`/v1/admin/clients/${encodeURIComponent(id)}/revoke`),
  audit: (q: Paging & { action?: string; actor_id?: string }) => get<Page<AuditEntry>>("/v1/admin/audit", q),
};

export const ops = {
  ready: () => fetch("/readyz").then((r) => r.json() as Promise<{ ready: boolean; checks: Record<string, boolean>; model_version: string | null }>),
};
