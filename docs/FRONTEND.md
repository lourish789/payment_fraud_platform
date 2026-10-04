# Web console and admin dashboard

The console is a React + TypeScript single-page app in `frontend/`. The FastAPI process serves its built
files, so one deployable unit runs the API and the console. It has three faces, chosen by the role of the
API key that signs in:

| Role | Lands on | Sees |
|---|---|---|
| admin | `/admin` | everything below, plus the admin dashboard, rails, rules, models, event pipeline, API clients, audit log, system |
| analyst | `/cases` | case queue, case detail (explanation, agent report, resolve, receipts), transactions, investigations, receipts, model monitoring |
| merchant | `/score` | scoring console, receipt verification |

## Architecture

```
browser ──── same origin ────► FastAPI
  │                              ├── /v1/...            JSON API (role-gated, one error envelope)
  │                              ├── /assets/...        hashed JS/CSS chunks (immutable)
  │                              └── /* (anything else) index.html  ← client-side routes, strict CSP
  │
  └─ React app
       main.tsx            QueryClient (cache, retries: 5xx only), AuthProvider, RouterProvider
       app/router.tsx      routes, one lazy-loaded chunk per page, permission guard per route
       app/navigation.ts   sidebar = this list filtered by the key's permissions
       api/client.ts       the only place that calls fetch: auth header, error envelope -> ApiError, 401 -> sign out
       api/endpoints.ts    one function per backend endpoint, grouped by resource
       api/types.ts        mirrors src/payguard/api/dto.py
       auth/               AuthContext (key in sessionStorage, /v1/auth/me), LoginPage, RequirePermission
       components/         ui.tsx (Card, Stat, Badge, Pagination, Async, ...), charts.tsx (Recharts), layout/AppShell
       features/<page>/    one folder per feature: dashboard, cases, transactions, investigations,
                           receipts, monitoring, scoring, models, admin
       lib/                format.ts (pure, unit-tested), useUrlState.ts (filters + paging in the URL)
       styles/global.css   design tokens (light and dark), layout primitives
```

### Conventions

- **Server state is TanStack Query.** Pages never keep fetched data in component state. Query keys include
  every filter, so changing a filter is a new cache entry and going back is instant. Lists use
  `keepPreviousData` so tables don't flash empty while the next page loads.
- **Filters live in the URL** (`useUrlState`), so every view of a list can be linked and survives a
  reload. Changing a filter resets the page offset.
- **Polling, not websockets.** The dashboard refreshes every 30-60 s and the queue every 15 s. A running
  investigation is polled every 1.5 s until it finishes; polling follows the investigation id the POST
  returned, so it doesn't depend on a case refetch having landed. At this scale polling is simpler to
  operate than a websocket tier, and nothing on these screens needs sub-second freshness.
- **Errors are handled in one place.** The API always returns
  `{"error": {"code", "message", "request_id"}}`. `ApiError` carries all three, and `ErrorState` shows the
  request id, so a support ticket can be matched to the server log line.
- **Retries only for 5xx.** A 4xx (bad filter, missing permission, not found) won't fix itself.
- **Code splitting.** Each page is its own chunk (2-9 KB gzipped). The chart library (113 KB gzipped) loads
  only with a page that draws charts, so merchants never download it.

### Languages and currencies

- **Languages:** English, Français, Yorùbá, Hausa, Igbo and Naijá (Pidgin). Pick one in the top bar or on
  the sign-in page.
- **Where the choice comes from:** the key's profile first (`PATCH /v1/auth/me/preferences`), so it follows
  the user to any browser. Then this browser's last choice, then the browser's language, then English.
  Changing it in the console saves it to the profile.
- **How strings are translated.** `src/i18n/` is gettext style: `t("Case queue")` looks up the English text
  in `locales/<lang>.ts` and falls back to English. API codes (decisions, statuses, verdicts, required
  actions, error codes) are labelled in `i18n/labels.ts` and translated where they're rendered. The code
  itself never changes.
- **Text the server writes** (error messages, decision reasons, explanations, the deterministic agent's
  report) is translated by the API. Every request sends `Accept-Language`, and switching language
  refetches the open queries.
- **Completeness is tested.** `src/i18n/catalog.test.ts` scans the source for every `t()`/`msg()` string
  literal (435 of them). It fails if a language is missing one, changes a `{placeholder}` or carries an
  unused entry.
- **Display currency (USD or NGN):**
  - `usd()` is for figures the API computes in USD (expected loss, volume, review cost). It converts them
    with the rates from `GET /v1/meta`.
  - `money(amount, currency)` shows a payment as it was paid. `<Amount>` adds "≈ ₦…" when the payment's
    currency differs from the display currency.
  - Crypto amounts show in the asset ("0.2 BTC ≈ $12,000.00").
- **Scoring console:** a USD/NGN switch rewrites the example payload, and the result shows the amount as
  paid, the USD it was scored as, and the rate.
- Numbers and dates follow the language (`Intl` with en-NG, fr-FR, yo-NG, ha-NG, ig-NG).

### The admin dashboard

`GET /v1/admin/overview` and `/v1/admin/timeseries` feed one page:

- **Traffic.** Transactions, volume, flag rate, mean fraud probability and scoring latency p50/p95/p99.
- **Decisions.** Decisions over time, volume against flagged amount, flag rate, and the decision mix.
- **Rails.** Per-rail volume, review/decline counts and flag rate.
- **Case queue.** Open cases, expected loss waiting in the queue, oldest open case, resolutions and median
  time to resolve.
- **Ground truth.** Labels by source, fraud rate, and **live precision/recall of flags against labels**.
- **Agent.** Runs by status and recommendation, tokens, and **agreement with analyst resolutions**.
- **Event pipeline.** Outbox backlog and age of the oldest pending event.
- **Models.** Champion, challenger and offline test metrics.
- **Health.** Status of the model, feature store, database and workers.

Windows are on business time (`event_time`) and end at the wall clock by default. "Ending at latest event"
is an explicit option for inspecting a replayed historical month in isolation. It used to be the default,
but the e2e run showed why it shouldn't be: once live payments are scored, "latest event" means today and
the replayed month falls out of every window.

The aggregates are GROUP BY queries over a covering index (`ix_transactions_dashboard`,
`ix_decisions_dashboard`). Percentiles and rule-hit counts use bounded samples of the most recent rows.
On the 89k-transaction replay database the overview went from 7.4 s (one scan per metric, over wide rows
with JSON payloads) to 0.6 s.

## Security

- **Authentication.** The console uses the same API keys as every other client. `/v1/auth/me` validates a
  key and returns its permissions; the key is kept in `sessionStorage` (cleared when the tab closes, not
  shared across tabs) and sent only to the console's own origin.
- **CSP.** The page sets `default-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'`.
  No third-party script can run, and nothing can be sent off-origin, which is what makes a bearer key in
  browser storage acceptable here.
- **Authorization stays on the server.** The UI hides pages a role can't use, but every API call is checked
  by role again. Hiding a page is a usability feature, not a control.
- **Revocation is immediate.** Revoking a key clears the auth cache; the next request with that key gets
  401, and the console signs the user out.
- **Path traversal.** The SPA fallback serves only files inside `frontend/dist` (tested), and unknown
  `/v1/...` paths return a JSON 404, never `index.html`.

**What a bank deployment would change:** OIDC single sign-on in front of the console, with short-lived
HttpOnly session cookies (plus CSRF protection) instead of keys in browser storage, and per-user identities
in the audit log instead of per-key. API keys would stay for machine clients.

## Testing

- `npm test`: unit tests for formatting and URL building (Vitest).
- `npm run typecheck`: strict TypeScript, no unused code.
- `tests/test_admin.py`: the admin API, the error envelope, pagination and serving the console.
- Browser end-to-end (run on 2026-10-01 against the 89k-transaction replay database, headless Edge via
  Playwright): **34/34 checks**, covering:
  - Signing in as each role, and that each role's navigation shows only its pages.
  - Every admin page, including issuing and revoking a key.
  - Opening a case, running the agent and reading its cited report, then resolving the case.
  - Scoring a payment on all four rails from the console.
  - That a revoked key is rejected.
  - That the phone layout has no horizontal scroll.
  - No console errors and no 5xx responses.

### What the browser run found

1. **The dashboard was too slow.** The first version took 7.4 s per overview. Fixed with grouped queries
   and covering indexes (above).
2. **An analyst's "Investigate" click waited more than 60 s on a cold start.** The outbox relay was draining
   a 93k-event backlog from the replay, and Python's `threading.Lock` (the SQLite writer lock) makes no
   ordering promise, so the request thread kept losing the lock to the background loop. Fixed with a FIFO
   ticket lock (`FairLock`), and with a separate interactive lane so analyst-requested runs never queue
   behind auto-investigations.
3. **In the split-process deployment, analyst-requested investigations never ran.** The API wrote the row
   but only in-process workers picked it up. The request now goes through the outbox as
   `investigation.requested`.
4. **The same investigation could run twice.** This happened when an automatic run and an analyst's run
   covered the same case. The claim is now atomic (`queued` → `running` only).
5. **"Anchored to the latest event" hid the replay** once live payments existed (see the dashboard section).

## Running it

```bash
cd frontend && npm ci
npm run dev        # http://localhost:5173, proxies /v1 to the API on :8000 (PAYGUARD_API to override)
npm run build      # -> frontend/dist, served by `payguard serve` at http://127.0.0.1:8000
```

Settings: `PAYGUARD_FRONTEND_DIST` is where the API looks for the build. `PAYGUARD_CORS_ORIGINS` is only
needed if the console is hosted on a different origin, which is not recommended (see Security).
