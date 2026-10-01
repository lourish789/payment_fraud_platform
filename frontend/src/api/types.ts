// Mirrors the backend response models in src/payguard/api/dto.py (and ScoreResponse in schemas.py).
// Keep the two in step: a field renamed there must be renamed here.

export type Role = "merchant" | "analyst" | "admin";
export type Rail = "card" | "bank_transfer" | "mobile_money" | "crypto";
export type Decision = "approve" | "review" | "decline";
export const RAILS: Rail[] = ["card", "bank_transfer", "mobile_money", "crypto"];

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Me {
  client_id: string;
  name: string;
  role: Role;
  permissions: string[];
}

export interface ReasonCode {
  code: string;
  detail: string;
  weight: number;
}

export interface ScoreResponse {
  transaction_id: string;
  decision: Decision;
  fraud_probability: number;
  expected_loss: number;
  reasons: ReasonCode[];
  model_version: string;
  rules_triggered: string[];
  case_id: string | null;
  latency_ms: number;
  idempotent_replay: boolean;
  degraded: boolean;
  rail: Rail;
  required_actions: string[];
}

export interface DecisionOut {
  decision: Decision;
  fraud_probability: number;
  expected_loss: number;
  model_version: string;
  reasons: ReasonCode[];
  rules: string[];
  actions: string[];
  shadow: Record<string, unknown> | null;
  latency_ms: number;
  created_at: string;
}

export interface TransactionSummary {
  transaction_id: string;
  rail: Rail;
  amount: number;
  currency: string | null;
  event_time: string;
  decision: Decision | null;
  fraud_probability: number | null;
  model_version: string | null;
  case_id: string | null;
  label: boolean | null;
  created_at: string;
}

export interface TransactionDetail {
  transaction_id: string;
  rail: Rail;
  transaction: Record<string, unknown>;
  decision: DecisionOut | null;
  label: { is_fraud: boolean; source: string; created_at: string | null } | null;
  case_id: string | null;
}

export interface AgentSummary {
  status: string;
  recommendation: string | null;
  confidence: number | null;
}

export interface CaseSummary {
  case_id: string;
  transaction_id: string;
  rail: Rail;
  status: "open" | "resolved";
  decision: Decision;
  priority: number;
  amount: number;
  resolution: "fraud" | "legit" | null;
  created_at: string;
  agent: AgentSummary | null;
}

export interface AgentReport {
  recommendation: "fraud" | "legit" | "escalate";
  confidence: number;
  summary: string;
  next_action: string;
  evidence: { claim: string; tool_call_id: string }[];
  grounding?: { evidence_items?: number; citation_valid_rate?: number; grounded_rate?: number };
}

export interface TraceStep {
  id: string;
  tool: string;
  input: Record<string, unknown>;
  output: unknown;
}

export interface Investigation {
  investigation_id: string;
  case_id: string;
  status: "queued" | "running" | "done" | "failed";
  provider: string | null;
  model: string | null;
  recommendation: string | null;
  confidence: number | null;
  report: AgentReport | null;
  error: string | null;
  tokens: { input: number; output: number };
  created_at: string;
  finished_at: string | null;
  trace?: TraceStep[] | null;
}

export interface ReceiptSummary {
  receipt_id: string;
  case_id: string | null;
  claimed_reference: string | null;
  verdict: string;
  created_at: string;
}

export interface ReceiptDetail extends ReceiptSummary {
  sha256: string;
  result: ReceiptResult;
}

export interface ReceiptResult {
  verdict: string;
  extracted: Record<string, unknown>;
  checks: Record<string, unknown>;
  claimed_reference: string | null;
  note?: string;
  receipt_id?: string;
}

export interface Explanation {
  feature: string;
  detail: string;
  weight: number;
}

export interface CaseDetail {
  case_id: string;
  status: "open" | "resolved";
  decision: Decision;
  priority: number;
  resolution: "fraud" | "legit" | null;
  resolution_note: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  created_at: string;
  transaction_id: string;
  rail: Rail;
  transaction: Record<string, unknown>;
  model: DecisionOut | null;
  explanation: Explanation[] | null;
  investigations: Investigation[];
  receipts: ReceiptSummary[];
}

export interface LabelRow {
  transaction_id: string;
  is_fraud: boolean;
  source: string;
  created_at: string | null;
}

// ---- monitoring & models ----------------------------------------------------------------------------
export interface DriftReport {
  status: "ok" | "warn" | "alert" | "insufficient_data";
  n: number;
  model_version?: string;
  window?: { from: string; to: string };
  score_psi?: number;
  flag_rate?: number;
  flagged_labelled?: number;
  live_precision_flagged?: number | null;
  features_psi?: Record<string, number>;
  drifted_features?: string[];
  thresholds?: { warn: number; alert: number };
}

export interface RegistryHistory {
  alias: "champion" | "challenger";
  from: string | null;
  to: string | null;
  reason: string;
  at: string;
}

export interface Registry {
  champion: string | null;
  challenger: string | null;
  history: RegistryHistory[];
  versions: string[];
}

export type ModelMetadata = Record<string, unknown> & {
  version: string;
  created_at?: string;
  train_window?: string;
  valid_window?: string;
  test_window?: string;
  n_features?: number;
  best_iteration?: number;
  report?: Record<string, unknown>;
};

// ---- admin ----------------------------------------------------------------------------------------------
export interface Window {
  from: string | null;
  to: string | null;
  anchored_to: "now" | "latest_event" | "explicit";
}

export interface RailBreakdown {
  rail: Rail;
  total: number;
  amount: number;
  approve: number;
  review: number;
  decline: number;
  flag_rate: number | null;
}

export interface Overview {
  window: Window;
  transactions: {
    total: number;
    amount: number;
    flagged: number;
    flagged_amount: number;
    flag_rate: number | null;
    mean_fraud_probability: number | null;
  };
  by_decision: Record<Decision, number>;
  by_rail: RailBreakdown[];
  latency_ms: { p50: number | null; p95: number | null; p99: number | null; sampled: number | null };
  cases: {
    open: number;
    resolved: number;
    resolved_fraud: number;
    resolved_legit: number;
    open_exposure: number;
    oldest_open_age_s: number | null;
    median_time_to_resolve_s: number | null;
  };
  labels: {
    total: number;
    fraud: number;
    by_source: Record<string, number>;
    precision_flagged: number | null;
    recall: number | null;
    fraud_rate: number | null;
  };
  agent: {
    provider: string;
    by_status: Record<string, number>;
    by_recommendation: Record<string, number>;
    tokens: { input: number; output: number };
    analyst_agreement: number | null;
    compared: number;
  };
  receipts: Record<string, number>;
  events: { total: number; pending: number; oldest_pending_age_s: number | null };
  models: { champion: string | null; challenger: string | null; card_test_metrics: Record<string, number> };
  health: Record<string, boolean>;
}

export interface TimeseriesPoint {
  t: string;
  total: number;
  approve: number;
  review: number;
  decline: number;
  amount: number;
  flagged_amount: number;
}

export interface Timeseries {
  window: Window;
  bucket: "hour" | "day";
  series: TimeseriesPoint[];
  by_rail: Record<string, { t: string; total: number; flagged: number }[]>;
}

export interface Condition {
  feature: string;
  op: string;
  value: unknown;
}

export interface RuleOut {
  rail: Rail;
  id: string;
  description: string;
  action: Decision;
  mode: "enforce" | "shadow";
  conditions: Condition[];
  hits: number;
}

export interface RailOut {
  rail: Rail;
  enabled: boolean;
  engine: "model" | "scorecard";
  version: string | null;
  rules: number;
  details: Record<string, unknown> & {
    weights?: (Condition & { points: number; reason: string })[];
    thresholds?: { review: number; decline: number };
    intercept?: number;
  };
  stats: {
    total: number;
    approve: number;
    review: number;
    decline: number;
    mean_latency_ms: number | null;
    flag_rate: number | null;
  };
}

export interface OutboxEvent {
  id: number;
  topic: string;
  key: string;
  payload: Record<string, unknown>;
  created_at: string;
  published_at: string | null;
}

export interface EventsReport {
  topics: { topic: string; total: number; pending: number; oldest_pending_age_s: number | null; last_event_at: string | null }[];
  recent: Page<OutboxEvent>;
}

export interface ApiClient {
  client_id: string;
  name: string;
  role: Role;
  active: boolean;
  key_prefix: string | null;
  created_at: string;
  revoked_at: string | null;
}

export interface ApiClientCreated extends ApiClient {
  api_key: string;
}

export interface AuditEntry {
  id: number;
  actor_id: string;
  actor_name: string;
  action: string;
  resource: string;
  details: Record<string, unknown> | null;
  created_at: string;
}

export interface SystemInfo {
  version: string;
  started_at: string;
  uptime_s: number;
  components: Record<string, unknown> & { health: Record<string, boolean>; rails: string[] };
  settings: Record<string, unknown>;
}
