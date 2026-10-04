# PayGuard

Real-time payment fraud platform covering cards, bank transfers, mobile money and crypto. It scores every
payment through one API using a calibrated LightGBM model, per-rail risk logic, a rules engine and OFAC
sanctions screening. Flagged payments go to an analyst case queue. An investigation agent (Claude, or a
heuristic baseline) writes reports on cases, and a receipt checker verifies payment screenshots. A React web
console covers analysts, merchants and admins, in English, French, Yorùbá, Hausa, Igbo and Pidgin, with
amounts in USD or NGN.

## Run locally

```bash
git clone https://github.com/lourish789/payment_fraud_platform.git
cd payment_fraud_platform

python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[vision,dev]"

# Use the bundled model, or train your own: payguard ingest && payguard backfill && payguard train
cp -r deploy/bundle artifacts

payguard create-client --name me --role admin          # prints an API key (shown once)
cd frontend && npm ci && npm run build && cd ..
payguard serve                                         # console: http://127.0.0.1:8000  API docs: /docs
```

To work on the frontend with hot reload, run `cd frontend && npm run dev`. It serves on :5173 and proxies to the API.
To run the tests: `pytest` and `cd frontend && npm test`. To bring up the full stack with Postgres and Redis, run `docker compose up`.

## Deploy on Render

`render.yaml` sets up the API (Docker), the console (static site) and a Postgres database.

1. In the Render dashboard, choose **New → Blueprint** and select this repo.
2. Set `PAYGUARD_BOOTSTRAP_ADMIN_KEY` to a random string of at least 24 characters. Use it to sign in to the console.
3. Optional: set `ANTHROPIC_API_KEY` to turn on the Claude investigation agent.

If Render assigns different hostnames, update `VITE_API_URL` (console) and `PAYGUARD_CORS_ORIGINS` (API) to match.

## API

Send the key as `Authorization: Bearer <key>`. Interactive docs are at `/docs`.

| Endpoint | Role | Purpose |
|---|---|---|
| `POST /v1/payments/score` | merchant | Score a payment on any rail |
| `GET /v1/transactions[/{id}]` | analyst | Browse scored transactions |
| `GET /v1/cases[/{id}]`, `POST /v1/cases/{id}/resolve` | analyst | Case queue and resolution |
| `POST /v1/cases/{id}/investigate`, `GET /v1/investigations` | analyst | Run and read agent investigations |
| `POST /v1/receipts/verify` | merchant, analyst | Verify a payment screenshot |
| `GET/POST /v1/labels`, `GET /v1/monitoring/drift` | analyst | Fraud labels and drift report |
| `GET /v1/models`, `POST /v1/models/promote` | admin | Model registry |
| `GET /v1/admin/*` | admin | Overview, rails, rules, events, API keys, audit log |
| `GET /v1/auth/me`, `GET /v1/meta` | any | Caller identity, languages and currencies |
| `GET /healthz`, `/readyz`, `/metrics` | none | Health and Prometheus metrics |

```bash
curl -X POST localhost:8000/v1/payments/score -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -d '{
    "rail": "card", "transaction_id": "demo-1", "event_time": "2026-10-04T10:00:00Z",
    "amount": 950.0, "currency": "USD", "product_code": "C", "payer_email_domain": "gmail.com",
    "card": {"bin": "9500", "issuer": "111", "country_code": "150", "category_code": "226", "network": "visa", "type": "debit"}
  }'
```

## Docs

- [Payment rails](docs/PAYMENT_RAILS.md): per-rail risk logic
- [Evaluation](docs/EVALUATION.md): model results
- [Design decisions](docs/DECISIONS.md)
- [Web console](docs/FRONTEND.md)
- [Runbook](docs/RUNBOOK.md)
- [QA report](docs/QA_REPORT.md)
