"""End-to-end smoke test of every HTTP endpoint against a running server, using real sample transactions.

    python scripts/smoke_test.py --merchant-key ... --analyst-key ... --admin-key ... [--url ...] [--n 600]

Sample data: the first --n transactions of the May 2018 test month (real IEEE-CIS rows, converted to the API
schema), saved to data/samples/smoke_transactions.jsonl with their true labels. Receipts are rendered from
the transactions the server actually scored, so ledger reconciliation runs against real records.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from payguard.data.adapter import iter_transactions  # noqa: E402
from payguard.simulate import test_month  # noqa: E402
from payguard.vision.receipts import forge_field, jpeg, random_fields, render  # noqa: E402

results: list[dict] = []


def check(name: str, ok: bool, detail: str = "", ms: float | None = None) -> bool:
    results.append({"check": name, "pass": bool(ok), "detail": detail, "ms": None if ms is None else round(ms, 1)})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    return ok


def timed(fn, *a, **k):
    t = time.perf_counter()
    r = fn(*a, **k)
    return r, (time.perf_counter() - t) * 1000


def sample_transactions(n: int) -> list[tuple[dict, int]]:
    df = test_month(ROOT / "data" / "processed", n)
    rows = [(t.model_dump(mode="json"), y) for t, y in iter_transactions(df)]
    out = ROOT / "data" / "samples" / "smoke_transactions.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for p, y in rows:
            f.write(json.dumps({"payload": p, "is_fraud": y}) + "\n")
    return rows


def receipt_png(tx: dict, kind: str) -> bytes:
    rng = random.Random(kind)
    when = datetime.fromisoformat(tx["event_time"].replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    fields = random_fields(rng, tx["transaction_id"], tx["amount"], when, currency=tx.get("currency", "USD"))
    if kind == "fabricated":
        fields.reference = "TRF9X7Q2LM4KZ8"
    r = render(fields)
    img = jpeg(r.image, 90)
    if kind == "amount_edit":
        r.image = img
        img = jpeg(forge_field(r, "Amount", f"{fields.currency} {tx['amount'] * 10:,.2f}", rng), 90)
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def real_crypto_samples() -> dict:
    """A real OFAC-sanctioned BTC address, and real Elliptic transactions (one illicit, one licit) from the
    test period with their input addresses."""
    ofac = (ROOT / "data" / "raw" / "crypto" / "ofac" / "sanctioned_addresses_XBT.txt").read_text().split()
    import pandas as pd

    from payguard.crypto.data import time_of_step

    risk = pd.read_parquet(sorted((ROOT / "artifacts" / "models" / "crypto").glob("crypto-lgbm-*/tx_risk.parquet"))[-1])
    ins = pd.read_csv(ROOT / "data" / "raw" / "crypto" / "AddrTx_edgelist.csv")
    ev = ins.merge(risk[(risk.step.between(40, 42)) & (risk.label >= 0)], on="txId")
    illicit = ev[ev.label == 1].sort_values("risk", ascending=False).iloc[0]
    licit = ev[ev.label == 0].sort_values("risk").iloc[0]
    return {"sanctioned": ofac[0], "illicit": illicit, "licit": licit, "when": time_of_step}


def rail_checks(c: httpx.Client, H: dict) -> None:
    print("== every payment rail via POST /v1/payments/score")
    t0 = "2026-10-01T09:00:00Z"
    base = {"currency": "USD", "account_age_days": 400}
    for i in range(6):  # six different senders pay one beneficiary within hours: mule fan-in
        bt = {"rail": "bank_transfer", "transaction_id": f"smoke-bt-{i}", "event_time": f"2026-10-01T0{i}:00:00Z",
              "amount": 1500.0, "account_id": f"victim-{i}", "beneficiary_account": "mule-001", "scheme": "NIP", **base}
        r = c.post("/v1/payments/score", json=bt, headers=H["merchant"])
    body = r.json()
    check("bank_transfer: mule fan-in -> review + hold_payment", r.status_code == 200 and body["decision"] == "review"
          and "hold_payment" in body["required_actions"], f"p={body.get('fraud_probability')}")
    mm = {"rail": "mobile_money", "transaction_id": "smoke-mm-1", "event_time": t0, "amount": 250.0, "account_id": "wallet-1",
          "kind": "cash_out", "counterparty_wallet": "agent-wallet", "agent_id": "agent-9", "sim_swap_days": 0.3, **base}
    r = c.post("/v1/payments/score", json=mm, headers=H["merchant"])
    check("mobile_money: cash-out right after SIM swap -> review", r.status_code == 200 and r.json()["decision"] == "review",
          ", ".join(r.json()["rules_triggered"]))

    s = real_crypto_samples()
    cx = {"rail": "crypto", "currency": "BTC", "asset": "BTC", "chain": "bitcoin", "account_id": "trader-1", **base}
    r = c.post("/v1/payments/score", headers=H["merchant"], json={**cx, "transaction_id": "smoke-cx-1", "event_time": t0,
               "amount": 0.2, "amount_usd": 12000, "direction": "withdrawal", "counterparty_address": s["sanctioned"]})
    b = r.json()
    check("crypto: withdrawal to real OFAC-sanctioned address -> decline + block",
          b["decision"] == "decline" and "block_withdrawal" in b["required_actions"], f"address {s['sanctioned']}")
    r = c.post("/v1/payments/score", headers=H["merchant"], json={**cx, "transaction_id": "smoke-cx-2", "event_time": t0,
               "amount": 0.2, "amount_usd": 12000, "direction": "deposit", "counterparty_address": s["sanctioned"]})
    b = r.json()
    check("crypto: deposit from sanctioned address -> frozen, not declined",
          b["decision"] == "review" and {"freeze_funds", "file_sanctions_report"} <= set(b["required_actions"]))
    for kind in ("illicit", "licit"):
        row = s[kind]
        when = s["when"](int(row.step)).isoformat().replace("+00:00", "Z")
        r = c.post("/v1/payments/score", headers=H["merchant"], json={
            **cx, "account_id": f"trader-{kind}", "transaction_id": f"smoke-cx-{kind}", "event_time": when,
            "amount": 0.05, "amount_usd": 900, "direction": "deposit", "counterparty_address": row.input_address,
            "tx_hash": str(int(row.txId))})
        b = r.json()
        cp = c.get(f"/v1/transactions/smoke-cx-{kind}", headers=H["analyst"]).json()["decision"]
        expected_flag = kind == "illicit"
        check(f"crypto: deposit carried by a real Elliptic {kind} tx -> {'flagged' if expected_flag else 'approved'}",
              (b["decision"] != "approve") == expected_flag,
              f"decision {b['decision']}, p={b['fraud_probability']}, model {cp['model_version']}")
    r = c.post("/v1/payments/score", headers=H["merchant"], json={**cx, "transaction_id": "smoke-cx-3", "event_time": t0,
               "amount": 0.1, "amount_usd": 6000, "direction": "withdrawal", "counterparty_vasp": "OtherExchange",
               "counterparty_address": "bc1qsmoketestaddressxxxxxxxxxxxxxxxxxx"})
    check("crypto: VASP transfer above threshold without Travel Rule data -> review",
          "travel_rule_incomplete" in r.json()["rules_triggered"])
    r = c.post("/v1/payments/score", headers=H["merchant"], json={"rail": "cheque", "transaction_id": "x",
                                                                  "event_time": t0, "amount": 1})
    check("unknown rail -> 422", r.status_code == 422)
    queue = c.get("/v1/cases", headers=H["analyst"], params={"limit": 200}).json()
    rails = {c.get(f"/v1/transactions/{q['transaction_id']}", headers=H["analyst"]).json()["transaction"].get("rail", "card")
             for q in queue}
    check("all four rails land in the one analyst queue", {"card", "bank_transfer", "mobile_money", "crypto"} <= rails,
          ", ".join(sorted(rails)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8020")
    ap.add_argument("--merchant-key", required=True)
    ap.add_argument("--analyst-key", required=True)
    ap.add_argument("--admin-key", required=True)
    ap.add_argument("--n", type=int, default=600)
    a = ap.parse_args()
    H = {r: {"Authorization": f"Bearer {k}"} for r, k in
         (("merchant", a.merchant_key), ("analyst", a.analyst_key), ("admin", a.admin_key))}
    c = httpx.Client(base_url=a.url, timeout=60)

    print("== ops")
    r = c.get("/healthz")
    check("GET /healthz -> 200", r.status_code == 200)
    r = c.get("/readyz")
    check("GET /readyz -> ready", r.status_code == 200 and r.json()["ready"], json.dumps(r.json()["checks"]))
    champion = r.json()["model_version"]

    print("== auth and validation")
    samples = sample_transactions(a.n)
    first = samples[0][0]
    check("score without token -> 401", c.post("/v1/transactions/score", json=first).status_code == 401)
    check("score with bad token -> 401", c.post("/v1/transactions/score", json=first,
                                                 headers={"Authorization": "Bearer nope"}).status_code == 401)
    check("analyst cannot score -> 403", c.post("/v1/transactions/score", json=first, headers=H["analyst"]).status_code == 403)
    check("merchant cannot read cases -> 403", c.get("/v1/cases", headers=H["merchant"]).status_code == 403)
    check("analyst cannot read models -> 403", c.get("/v1/models", headers=H["analyst"]).status_code == 403)
    bad = dict(first, amount=-5)
    check("negative amount -> 422", c.post("/v1/transactions/score", json=bad, headers=H["merchant"]).status_code == 422)

    print(f"== scoring {len(samples)} real sample transactions")
    lat, decisions, codes, scored = [], {}, {}, {}
    for p, y in samples:
        r, ms = timed(c.post, "/v1/transactions/score", json=p, headers=H["merchant"])
        lat.append(ms)
        codes[r.status_code] = codes.get(r.status_code, 0) + 1
        if r.status_code == 200:
            body = r.json()
            decisions[body["decision"]] = decisions.get(body["decision"], 0) + 1
            scored[p["transaction_id"]] = (body, y, p)
    lat.sort()
    p50, p95 = lat[len(lat) // 2], lat[int(len(lat) * 0.95)]
    check(f"POST /v1/transactions/score x{len(samples)} -> all 200", codes == {200: len(samples)},
          f"decisions {decisions}, p50 {p50:.1f} ms, p95 {p95:.1f} ms")
    fraud = [v for v in scored.values() if v[1] == 1]
    caught = [v for v in fraud if v[0]["decision"] != "approve"]
    flagged = [v for v in scored.values() if v[0]["decision"] != "approve"]
    outcome = {"fraud_in_sample": len(fraud), "fraud_flagged": len(caught),
               "flagged_total": len(flagged), "flagged_that_were_fraud": sum(1 for v in flagged if v[1] == 1)}
    check("model flags real fraud in the sample", len(caught) > 0, json.dumps(outcome))
    leaked = [v for v in scored.values() for rc in v[0]["reasons"] if rc["code"].startswith(("customer_", "sig_", "device_"))]
    check("merchant responses carry no model internals", not leaked)

    r = c.post("/v1/transactions/score", json=first, headers=H["merchant"])
    check("retry same transaction -> idempotent replay, same decision",
          r.status_code == 200 and r.json()["idempotent_replay"] and r.json()["decision"] == scored[first["transaction_id"]][0]["decision"])
    r = c.post("/v1/transactions/score", json=dict(first, amount=first["amount"] + 1), headers=H["merchant"])
    check("same id, changed payload -> 409", r.status_code == 409)

    tid = first["transaction_id"]
    r = c.get(f"/v1/transactions/{tid}", headers=H["analyst"])
    check("GET /v1/transactions/{id} -> payload + decision", r.status_code == 200 and r.json()["decision"] is not None)
    check("GET unknown transaction -> 404", c.get("/v1/transactions/does-not-exist", headers=H["analyst"]).status_code == 404)

    print("== cases and explanations")
    r = c.get("/v1/cases", headers=H["analyst"], params={"limit": 200})
    queue = r.json()
    pri = [q["priority"] for q in queue]
    check("GET /v1/cases -> queue ordered by expected loss", r.status_code == 200 and queue and pri == sorted(pri, reverse=True),
          f"{len(queue)} open cases")
    case = queue[0]
    r = c.get(f"/v1/cases/{case['case_id']}", headers=H["analyst"])
    expl = r.json().get("explanation") or []
    check("GET /v1/cases/{id} -> TreeSHAP explanation", r.status_code == 200 and len(expl) > 0,
          "; ".join(e["detail"] for e in expl[:3]))
    check("GET unknown case -> 404", c.get("/v1/cases/case_nope", headers=H["analyst"]).status_code == 404)

    print("== receipt verification (computer vision)")
    tx = scored[case["transaction_id"]][2]
    for kind, expected in (("genuine", "verified"), ("amount_edit", "mismatch"), ("fabricated", "not_found")):
        r, ms = timed(c.post, "/v1/receipts/verify", headers=H["merchant"],
                      files={"file": (f"{kind}.png", receipt_png(tx, kind), "image/png")},
                      data={"case_id": case["case_id"], **({"claimed_reference": tx["transaction_id"]}
                                                            if kind != "fabricated" else {})})
        body = r.json()
        check(f"POST /v1/receipts/verify ({kind}) -> {expected}", r.status_code == 200 and body["verdict"] == expected,
              f"verdict {body.get('verdict')}, extracted amount {body.get('extracted', {}).get('amount')}", ms)
    r = c.post("/v1/receipts/verify", headers=H["merchant"], files={"file": ("x.txt", b"hello", "text/plain")})
    check("non-image upload -> 415", r.status_code == 415)

    print("== investigation agent")
    r = c.post(f"/v1/cases/{case['case_id']}/investigate", headers=H["analyst"])
    inv_id = r.json().get("investigation_id")
    check("POST /v1/cases/{id}/investigate -> 202", r.status_code == 202 and inv_id is not None)
    inv, deadline = None, time.time() + 120
    while time.time() < deadline:
        inv = c.get(f"/v1/investigations/{inv_id}", headers=H["analyst"], params={"include_trace": "true"}).json()
        if inv["status"] in ("done", "failed"):
            break
        time.sleep(1)
    tools_used = [t["tool"] for t in (inv.get("trace") or [])]
    check("GET /v1/investigations/{id} -> grounded report",
          inv["status"] == "done" and inv["report"]["grounding"]["citation_valid_rate"] == 1.0,
          f"{inv['provider']} recommends {inv['recommendation']} ({inv['confidence']}); tools: {', '.join(tools_used)}")
    check("agent read the attached receipt verdicts", "get_receipt_verification" in tools_used)
    r = c.get("/v1/cases", headers=H["analyst"], params={"limit": 200})
    auto = [q for q in r.json() if q["agent"] and q["agent"]["status"] == "done"]
    check("cases auto-investigated via outbox -> bus -> worker", len(auto) > 0, f"{len(auto)} of {len(r.json())} open cases")

    print("== feedback loop")
    truth = scored[case["transaction_id"]][1]
    res = "fraud" if truth else "legit"
    r = c.post(f"/v1/cases/{case['case_id']}/resolve", json={"resolution": res, "note": "smoke test"}, headers=H["analyst"])
    check(f"POST /v1/cases/{{id}}/resolve ({res}) -> 200", r.status_code == 200)
    r = c.post(f"/v1/cases/{case['case_id']}/resolve", json={"resolution": res}, headers=H["analyst"])
    check("same resolution again -> 200 (idempotent)", r.status_code == 200)
    other = "legit" if res == "fraud" else "fraud"
    r = c.post(f"/v1/cases/{case['case_id']}/resolve", json={"resolution": other}, headers=H["analyst"])
    check("conflicting resolution -> 409", r.status_code == 409)
    batch = [{"transaction_id": p["transaction_id"], "is_fraud": bool(y), "source": "chargeback"} for p, y in samples[1:51]]
    r = c.post("/v1/labels", json=batch, headers=H["analyst"])
    check("POST /v1/labels (50 chargebacks) -> accepted", r.status_code == 200 and r.json()["accepted"] == 50)
    r = c.get(f"/v1/transactions/{samples[1][0]['transaction_id']}", headers=H["analyst"])
    check("label visible on transaction", r.json()["label"] is not None)

    rail_checks(c, H)

    print("== monitoring and model registry")
    r = c.get("/v1/monitoring/drift", headers=H["analyst"])
    d = r.json()
    check("GET /v1/monitoring/drift -> report", r.status_code == 200 and d.get("status") in ("ok", "warn", "alert"),
          f"status {d.get('status')}, score PSI {d.get('score_psi')}, n {d.get('n')}, drifted {d.get('drifted_features')}")
    r = c.get("/v1/models", headers=H["admin"])
    check("GET /v1/models -> registry", r.status_code == 200 and r.json()["champion"] == champion, f"champion {champion}")
    r = c.post("/v1/models/promote", headers=H["admin"],
               json={"alias": "challenger", "version": champion, "reason": "smoke: shadow scoring"})
    check("POST /v1/models/promote challenger -> 200", r.status_code == 200)
    extra = test_month(ROOT / "data" / "processed", 1, offset=a.n)
    p = next(iter_transactions(extra))[0].model_dump(mode="json")
    c.post("/v1/transactions/score", json=p, headers=H["merchant"])
    shadow = c.get(f"/v1/transactions/{p['transaction_id']}", headers=H["analyst"]).json()["decision"]["shadow"]
    check("challenger scored in shadow", shadow is not None and shadow["version"] == champion, json.dumps(shadow))
    r = c.post("/v1/models/promote", headers=H["admin"], json={"alias": "challenger", "version": None, "reason": "smoke: done"})
    check("unset challenger -> 200", r.status_code == 200 and r.json()["challenger"] is None)
    r = c.post("/v1/models/promote", headers=H["admin"], json={"alias": "champion", "version": None, "reason": "bad"})
    check("unset champion -> 400", r.status_code == 400)
    r = c.post("/v1/models/promote", headers=H["admin"], json={"alias": "champion", "version": "lgbm-nope", "reason": "bad"})
    check("promote unknown version -> 404", r.status_code == 404)

    r = c.get("/metrics")
    m = r.text
    check("GET /metrics -> Prometheus counters", r.status_code == 200 and "payguard_decisions_total" in m
          and "payguard_receipts_total" in m and "payguard_agent_runs_total" in m)

    passed = sum(x["pass"] for x in results)
    report = {"url": a.url, "passed": passed, "total": len(results), "sample_outcome": outcome,
              "score_latency_ms": {"p50": round(p50, 1), "p95": round(p95, 1)}, "checks": results}
    out = ROOT / "artifacts" / "reports" / "smoke_test.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\n{passed}/{len(results)} checks passed -> {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
