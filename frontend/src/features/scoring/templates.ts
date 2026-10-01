import type { Rail } from "@/api/types";

// Example payloads per rail (same shapes as the smoke test). The console fills in a fresh
// transaction_id and the current time each time a template is loaded.
export function template(rail: Rail): Record<string, unknown> {
  const base = { transaction_id: `console-${rail}-${Date.now().toString(36)}`, event_time: new Date().toISOString() };
  switch (rail) {
    case "card":
      return {
        rail, ...base, amount: 249.99, currency: "USD", product_code: "W",
        card: { bin: "9500", issuer: "111", country_code: "150", category_code: "226", network: "visa", type: "debit" },
        billing: { region: "325", country: "87" }, payer_email_domain: "gmail.com",
        device: { type: "mobile", info: "iOS Device", os: "iOS 11.2", browser: "mobile safari 11.0", screen: "2208x1242" },
        signals: { C1: 1, C13: 1, D1: 0, M4: "M0" },
      };
    case "bank_transfer":
      return {
        rail, ...base, amount: 4800, currency: "USD", account_id: "acct-1029", account_age_days: 400,
        beneficiary_account: "mule-001", beneficiary_bank: "058", beneficiary_name: "J. Doe", channel: "app", scheme: "NIP",
      };
    case "mobile_money":
      return {
        rail, ...base, amount: 250, currency: "USD", account_id: "wallet-77", account_age_days: 210,
        kind: "cash_out", counterparty_wallet: "agent-wallet-9", agent_id: "agent-9", sim_swap_days: 0.3,
      };
    case "crypto":
      return {
        rail, ...base, amount: 0.2, currency: "BTC", amount_usd: 12000, account_id: "trader-1", account_age_days: 3,
        direction: "withdrawal", asset: "BTC", chain: "bitcoin",
        counterparty_address: "bc1qconsoleexampleaddressxxxxxxxxxxxxxxx", counterparty_vasp: "OtherExchange",
      };
  }
}
