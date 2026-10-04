// English labels for the codes the API returns (decisions, statuses, verdicts, required actions, ...). The
// codes themselves never change with the language; their labels are translated where rendered.
import { humanize } from "@/lib/format";
import { msg } from "./index";

export const RAIL_LABEL: Record<string, string> = {
  card: msg("Card"),
  bank_transfer: msg("Bank transfer"),
  mobile_money: msg("Mobile money"),
  crypto: msg("Crypto"),
};

export const CODE_LABEL: Record<string, string> = {
  // decisions and case states
  approve: msg("Approve"),
  review: msg("Review"),
  decline: msg("Decline"),
  open: msg("Open"),
  resolved: msg("Resolved"),
  fraud: msg("Fraud"),
  legit: msg("Legit"),
  escalate: msg("Escalate"),
  // investigations
  queued: msg("Queued"),
  running: msg("Running"),
  done: msg("Done"),
  failed: msg("Failed"),
  confirm_decline: msg("Confirm decline"),
  release: msg("Release"),
  request_customer_verification: msg("Request customer verification"),
  // receipts
  verified: msg("Verified"),
  mismatch: msg("Mismatch"),
  not_found: msg("Not found"),
  suspected_tampering: msg("Suspected tampering"),
  unreadable: msg("Unreadable"),
  found: msg("Found"),
  unavailable: msg("Unavailable"),
  ocr_boxes: msg("OCR text boxes"),
  forensics: msg("Image forensics"),
  ledger: msg("Ledger"),
  amount_matches: msg("Amount matches"),
  date_matches: msg("Date matches"),
  currency_matches: msg("Currency matches"),
  forensic_anomaly: msg("Pixel anomaly"),
  claimed_reference_matches: msg("Claimed reference matches"),
  // required actions
  hold_payment: msg("Hold payment"),
  freeze_funds: msg("Freeze funds"),
  file_sanctions_report: msg("File sanctions report"),
  hold_credit: msg("Hold credit"),
  block_withdrawal: msg("Block withdrawal"),
  hold_withdrawal: msg("Hold withdrawal"),
  // roles, rules, rails, clients, models, events
  merchant: msg("Merchant"),
  analyst: msg("Analyst"),
  admin: msg("Admin"),
  enforce: msg("Enforce"),
  shadow: msg("Shadow"),
  enabled: msg("Enabled"),
  disabled: msg("Disabled"),
  model: msg("Model"),
  scorecard: msg("Scorecard"),
  active: msg("Active"),
  revoked: msg("Revoked"),
  champion: msg("Champion"),
  challenger: msg("Challenger"),
  pending: msg("Pending"),
  // drift
  ok: msg("OK"),
  warn: msg("Warning"),
  alert: msg("Alert"),
  insufficient_data: msg("Insufficient data"),
  // health
  feature_store: msg("Feature store"),
  database: msg("Database"),
  workers: msg("Workers"),
  // label sources
  chargeback: msg("Chargeback"),
  backfill: msg("Backfill"),
  // audit actions
  "case.resolve": msg("Case resolved"),
  "model.promote": msg("Model promoted"),
  "client.create": msg("API key issued"),
  "client.revoke": msg("API key revoked"),
  "client.preferences": msg("Preferences changed"),
  // error codes (ApiError.code)
  validation_error: msg("Invalid request"),
  unauthenticated: msg("Not signed in"),
  forbidden: msg("Not allowed"),
  rate_limited: msg("Too many requests"),
  idempotency_conflict: msg("Duplicate transaction id"),
  already_resolved: msg("Already resolved"),
  unsupported_currency: msg("Unsupported currency"),
  amount_usd_required: msg("USD amount required"),
  invalid_preference: msg("Invalid preference"),
  invalid_window: msg("Invalid time window"),
  too_large: msg("Too large"),
  unsupported_media_type: msg("Unsupported file type"),
  unprocessable_image: msg("Unreadable image"),
  self_revoke: msg("Cannot revoke your own key"),
  rail_not_enabled: msg("Payment rail not enabled"),
  internal: msg("Server error"),
};

/** English label for an API code (falls back to the humanised code), to pass through t(). */
export const codeLabel = (code: string): string => CODE_LABEL[code] ?? humanize(code);
