# QA report: 2026-10-04

A full pass over the API and the console, run against a copy of the 89k-transaction replay database. It
used three methods:
- the 48-check endpoint smoke test, as it stood;
- about 40 exploratory probes (currency, validation edge cases, concurrency, admin safety);
- a headless-Edge browser run of every console page in every language.

The work also added currency handling ([ADR-19](DECISIONS.md)) and languages ([ADR-20](DECISIONS.md)).

## Findings

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | Critical | `currency` was ignored: ₦46,500 on a card (≈ $30) was scored as $46,500 and **declined** by the hard amount rule, with an expected loss of $5,066 | Every payment is converted to USD before scoring (`payguard/currency.py`) |
| 2 | High | A crypto payment without `amount_usd` scored 0.5 BTC as **$0.50**, below the Travel Rule threshold and the large-transfer points. Omitting one field was an evasion path | `422 amount_usd_required` for crypto without `amount_usd` |
| 3 | High | A bank transfer of ₦465,000 (≈ $300) carried an expected loss of $15,017, so it jumped to the top of the analyst queue (ordered by expected loss) | Same conversion; the queue is now in USD |
| 4 | High | The dashboard summed naira and dollars into one "volume", and `min_amount` filters mixed currencies | `transactions.amount` is USD; `currency` and `amount_local` keep what was paid; migration re-bases old rows |
| 5 | Medium | Any `currency` string was accepted (`"XYZ"`, `""`) | ISO-style validation; unknown codes → `422 unsupported_currency` |
| 6 | Medium | A receipt showing the right digits in the wrong currency ("USD 12,000" for a ₦12,000 transfer) would have verified | The verifier reads the currency next to the amount; a different currency → `mismatch` |
| 7 | Medium | `transaction_id` containing `/` was accepted and scored, but the transaction and its case could never be read back (`GET /v1/transactions/{id}` → 404). Whitespace-only ids were accepted too | Ids must match `^[A-Za-z0-9][A-Za-z0-9._:-]*$` |
| 8 | Medium | `signals` was unbounded: a 50,000-key payload was accepted and moved the fraud probability from 0.04 to 0.47 | At most 1,000 signals; names ≤ 64 chars; string values ≤ 256 |
| 9 | Medium | `event_time` in 2099 was accepted. A future event folds every entity's decayed velocity state forward in time | Reject more than 24 h in the future |
| 10 | Low | Labels for transactions that don't exist were stored. They can never join a decision, and they inflate label counts | Unknown ids are skipped and returned in `unknown` |
| 11 | Low | The case queue showed a crypto amount of 0.2 BTC as "$0.20" | `<Amount>` shows the payment's own currency, plus its value in the display currency |
| 12 | Low | The smoke test crashed (`KeyError`) on any database that already held data. It also failed its shadow check, because the transaction it used had been scored before | It uses its own scored case, per-run ids, and a fresh id for the shadow check |
| 13 | Low | Smoke-test crypto payloads were sent as `currency: "USD"`, because a dict merge overwrote `"BTC"` | Merge order fixed |
| 14 | Low | Found during this work (introduced by it, fixed before release): the console re-fetched `/v1/auth/me` in a loop after sign-in, because registering the profile-save listener re-created the auth callbacks | Listener kept in a ref; the setters are stable |

These all held up under probing:
- auth and role gates;
- pagination bounds;
- error envelope on 404, 405 and 422;
- a 16-way concurrent duplicate submission (one decision, 15 idempotent replays);
- self-revocation protection;
- path traversal on `/v1/models/..`;
- corrupt and oversized uploads.

Two minor items are noted and not changed:
- Unknown JSON fields are ignored silently (`"amonut": 99` → 200). Rejecting them would break lenient
  clients.
- An amount of `1e-9` is accepted.

## Verification

| Check | Result |
|---|---|
| `pytest` | 63 passed (13 new: currency, migration, validation, labels, catalogs, languages, profiles) |
| `npm test` / `npm run typecheck` | 14 passed (catalog completeness for 435 strings × 5 languages, currency formatting) / clean |
| `scripts/smoke_test.py` on a fresh copy of the replay DB | 54/54, and 54/54 again on re-run (previously crashed) |
| Exploratory probes after the fixes | Every finding above now returns the expected 422 or the converted amount |
| Browser run (headless Edge) | 23/23 checks: <ul><li>all 15 admin pages in all 6 languages</li><li>the language follows the key's profile into a second browser</li><li>API reasons in Yorùbá on a case</li><li>dashboard in naira</li><li>an NGN card payment scored as $249.99</li><li>a French error message</li><li>merchant (Hausa) and analyst (Igbo) consoles</li><li>no horizontal scroll at 390 px</li><li>no console errors or 5xx</li></ul> |
| Migration on the 89k-row replay DB | Ran in seconds at startup. USD card rows unchanged; non-USD rail rows re-based to USD |

## Open items

- A native-speaker review of the Yorùbá, Hausa, Igbo and Pidgin translations.
- FX rates are static (`configs/currency.yaml`, NGN 1550/USD as of 2026-10-01). Wire a daily treasury feed,
  and record the rate's timestamp on each decision.
- The five console catalogs add about 40 KB gzipped to the main bundle. Lazy-load them per language if that
  matters.
