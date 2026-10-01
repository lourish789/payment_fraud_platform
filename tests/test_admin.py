"""Admin API, auth/me, the error envelope, paginated listings and serving the web console."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from payguard.agent.runner import HeuristicProvider
from payguard.api.app import build_container, create_app
from payguard.api.security import create_client
from payguard.config import Settings
from payguard.schemas import BankTransferIn
from tests.conftest import make_txn
from tests.helpers import REPO, build_tiny_model, fraud_like


def _settings(tmp_path, **kw) -> Settings:
    return Settings(database_url=f"sqlite:///{(tmp_path / 'adm.db').as_posix()}", artifacts_dir=tmp_path / "artifacts",
                    data_dir=tmp_path / "data", rules_path=REPO / "configs" / "rules.yaml",
                    rails_config_dir=REPO / "configs" / "rails", sanctions_dir=tmp_path / "no-ofac",
                    rate_limit_rps=1000, rate_limit_burst=1000, agent_provider="heuristic",
                    agent_auto_investigate=False, frontend_dist=tmp_path / "dist", **kw)


@pytest.fixture
def env(tmp_path):
    settings = _settings(tmp_path)
    build_tiny_model(settings.models_dir)
    c = build_container(settings, provider=HeuristicProvider(), start_workers=False)
    keys = {role: create_client(c.session_factory, f"{role}-1", role)[1] for role in ("merchant", "analyst", "admin")}
    with TestClient(create_app(c)) as client:
        yield client, {r: {"Authorization": f"Bearer {k}"} for r, k in keys.items()}


def _bank(i: int, amount: float = 100.0) -> dict:
    return BankTransferIn(rail="bank_transfer", transaction_id=f"bt{i}", account_id="acct-1", amount=amount,
                          event_time=datetime(2018, 5, 2, tzinfo=timezone.utc) + timedelta(minutes=i),
                          beneficiary_account=f"benef-{i}").model_dump(mode="json")


def _seed(client, h) -> list[dict]:
    out = [client.post("/v1/transactions/score", json=make_txn(i, minutes=i * 60).model_dump(mode="json"),
                       headers=h["merchant"]).json() for i in range(6)]
    out += [client.post("/v1/transactions/score", json=fraud_like(i, amount=3000.0).model_dump(mode="json"),
                        headers=h["merchant"]).json() for i in range(20)]
    out += [client.post("/v1/payments/score", json=_bank(i, 50_000.0), headers=h["merchant"]).json() for i in range(3)]
    return out


def test_me_returns_role_and_permissions(env):
    client, h = env
    me = client.get("/v1/auth/me", headers=h["analyst"]).json()
    assert me["role"] == "analyst" and "cases:resolve" in me["permissions"] and "admin:read" not in me["permissions"]
    assert "clients:manage" in client.get("/v1/auth/me", headers=h["admin"]).json()["permissions"]


def test_errors_use_one_envelope_with_request_id(env):
    client, h = env
    r = client.get("/v1/cases/case_nope", headers={**h["analyst"], "X-Request-Id": "rid-123"})
    assert r.status_code == 404 and r.headers["x-request-id"] == "rid-123"
    assert r.json() == {"error": {"code": "not_found", "message": "unknown case", "request_id": "rid-123"}}
    r = client.get("/v1/cases", headers=h["analyst"], params={"limit": 999})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error" and r.json()["error"]["details"]
    assert client.get("/v1/admin/overview", headers=h["analyst"]).json()["error"]["code"] == "forbidden"
    assert client.get("/v1/auth/me").json()["error"]["code"] == "unauthenticated"


def test_listings_paginate_and_filter(env):
    client, h = env
    _seed(client, h)
    page = client.get("/v1/transactions", headers=h["analyst"], params={"limit": 5}).json()
    assert page["total"] == 29 and len(page["items"]) == 5 and page["limit"] == 5
    second = client.get("/v1/transactions", headers=h["analyst"], params={"limit": 5, "offset": 5}).json()
    assert not {t["transaction_id"] for t in page["items"]} & {t["transaction_id"] for t in second["items"]}
    bank = client.get("/v1/transactions", headers=h["analyst"], params={"rail": "bank_transfer"}).json()
    assert bank["total"] == 3 and all(t["rail"] == "bank_transfer" for t in bank["items"])
    cases = client.get("/v1/cases", headers=h["analyst"], params={"status": "all"}).json()
    assert cases["total"] >= 1 and {"rail", "amount", "priority"} <= set(cases["items"][0])
    assert client.get("/v1/cases", headers=h["analyst"], params={"rail": "crypto"}).json()["total"] == 0


def test_overview_reflects_traffic_cases_labels_and_audit(env):
    client, h = env
    _seed(client, h)
    case = client.get("/v1/cases", headers=h["analyst"]).json()["items"][0]
    client.post(f"/v1/cases/{case['case_id']}/resolve", json={"resolution": "fraud", "note": "confirmed"},
                headers=h["analyst"])
    ov = client.get("/v1/admin/overview", headers=h["admin"], params={"window": "all", "anchor": "latest_event"}).json()
    assert ov["transactions"]["total"] == 29 and sum(ov["by_decision"].values()) == 29
    assert {r["rail"] for r in ov["by_rail"]} == {"card", "bank_transfer"}
    assert ov["cases"]["resolved_fraud"] == 1 and ov["labels"]["by_source"] == {"analyst": 1}
    assert ov["labels"]["precision_flagged"] == 1.0 and ov["latency_ms"]["p50"] is not None
    assert ov["window"]["anchored_to"] == "latest_event" and ov["health"]["database"]
    # a 24h window anchored to the latest event excludes the oldest card traffic; anchored to now
    # (the default) it is empty, because the seeded traffic is from 2018
    day = {"window": "24h", "anchor": "latest_event"}
    assert 0 < client.get("/v1/admin/overview", headers=h["admin"], params=day).json()["transactions"]["total"] < 29
    now = client.get("/v1/admin/overview", headers=h["admin"], params={"window": "24h"}).json()
    assert now["window"]["anchored_to"] == "now" and now["transactions"]["total"] == 0

    audit = client.get("/v1/admin/audit", headers=h["admin"]).json()["items"]
    assert audit[0]["action"] == "case.resolve" and audit[0]["actor_name"] == "analyst-1"


def test_timeseries_rails_rules_events_system(env):
    client, h = env
    _seed(client, h)
    ts = client.get("/v1/admin/timeseries", headers=h["admin"], params={"window": "7d", "bucket": "hour", "anchor": "latest_event"}).json()
    assert sum(p["total"] for p in ts["series"]) == 29 and set(ts["by_rail"]) == {"card", "bank_transfer"}
    bad = client.get("/v1/admin/timeseries", headers=h["admin"], params={"window": "all", "bucket": "hour"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_window"

    rails = {r["rail"]: r for r in client.get("/v1/admin/rails", headers=h["admin"]).json()}
    assert rails["card"]["engine"] == "model" and rails["bank_transfer"]["stats"]["total"] == 3
    assert rails["crypto"]["details"]["sanctioned_addresses"] == 0 and rails["crypto"]["details"]["weights"]

    rules = client.get("/v1/admin/rules", headers=h["admin"]).json()
    assert {r["rail"] for r in rules["rules"]} >= {"card", "bank_transfer", "mobile_money", "crypto"}
    assert rules["sampled_decisions"] == 29

    ev = client.get("/v1/admin/events", headers=h["admin"], params={"topic": "decision.made", "limit": 3}).json()
    assert ev["recent"]["total"] == 29 and len(ev["recent"]["items"]) == 3
    assert any(t["topic"] == "decision.made" for t in ev["topics"])

    sysinfo = client.get("/v1/admin/system", headers=h["admin"]).json()
    assert sysinfo["components"]["database"]["dialect"] == "sqlite" and "password" not in str(sysinfo)


def test_api_client_lifecycle(env):
    client, h = env
    r = client.post("/v1/admin/clients", headers=h["admin"], json={"name": "web-shop", "role": "merchant"})
    assert r.status_code == 201
    new = r.json()
    key = {"Authorization": f"Bearer {new['api_key']}"}
    assert client.get("/v1/auth/me", headers=key).json()["name"] == "web-shop"
    listed = client.get("/v1/admin/clients", headers=h["admin"], params={"role": "merchant"}).json()["items"]
    assert any(c["client_id"] == new["client_id"] and "api_key" not in c for c in listed)

    assert client.post(f"/v1/admin/clients/{new['client_id']}/revoke", headers=h["admin"]).json()["active"] is False
    assert client.get("/v1/auth/me", headers=key).status_code == 401  # cache invalidated: immediate effect
    me = client.get("/v1/auth/me", headers=h["admin"]).json()
    assert client.post(f"/v1/admin/clients/{me['client_id']}/revoke", headers=h["admin"]).status_code == 409
    assert client.post("/v1/admin/clients", headers=h["analyst"], json={"name": "x1", "role": "admin"}).status_code == 403
    actions = [a["action"] for a in client.get("/v1/admin/audit", headers=h["admin"],
                                               params={"action": "client"}).json()["items"]]
    assert actions == ["client.revoke", "client.create"]


def test_console_is_served_with_spa_fallback(tmp_path):
    settings = _settings(tmp_path)
    (settings.frontend_dist / "assets").mkdir(parents=True)
    (settings.frontend_dist / "index.html").write_text("<!doctype html><div id=root></div>")
    (settings.frontend_dist / "assets" / "app.js").write_text("console.log(1)")
    build_tiny_model(settings.models_dir)
    c = build_container(settings, provider=HeuristicProvider(), start_workers=False)
    with TestClient(create_app(c)) as client:
        r = client.get("/cases/case_123")
        assert r.status_code == 200 and "root" in r.text
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert client.get("/assets/app.js").text == "console.log(1)"
        r = client.get("/v1/does-not-exist")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
        assert client.get("/healthz").json() == {"status": "ok"}
        for probe in ("/..%2f..%2fpyproject.toml", "/%2e%2e/%2e%2e/pyproject.toml"):
            assert "build-system" not in client.get(probe).text  # never serves files outside dist


def test_fair_lock_serves_waiters_in_arrival_order():
    import threading
    import time

    from payguard.db.session import FairLock

    lock, order = FairLock(), []
    lock.__enter__()  # held while the waiters line up

    def worker(i):
        with lock:
            order.append(i)

    threads = []
    for i in range(6):
        t = threading.Thread(target=worker, args=(i,))
        t.start()
        threads.append(t)
        time.sleep(0.05)  # deterministic arrival order
    lock.__exit__(None, None, None)
    for t in threads:
        t.join(5)
    assert order == list(range(6))


def test_analyst_investigation_is_evented_and_runs_once(tmp_path):
    from sqlalchemy import select

    from payguard.agent.service import enqueue_investigation, run_investigation
    from payguard.db.models import Investigation, OutboxEvent

    settings = _settings(tmp_path)
    build_tiny_model(settings.models_dir)
    c = build_container(settings, provider=HeuristicProvider(), start_workers=False)
    key = {"Authorization": f"Bearer {create_client(c.session_factory, 'm', 'merchant')[1]}"}
    akey = {"Authorization": f"Bearer {create_client(c.session_factory, 'a', 'analyst')[1]}"}
    with TestClient(create_app(c)) as client:
        flagged = next(r for r in (client.post("/v1/payments/score", json=_bank(i, 90_000.0), headers=key).json()
                                   for i in range(5)) if r["case_id"])
        r = client.post(f"/v1/cases/{flagged['case_id']}/investigate", headers=akey)
        assert r.status_code == 202
        inv_id = r.json()["investigation_id"]
        # an auto-investigation for the same case reuses the queued one
        assert enqueue_investigation(c.session_factory, flagged["case_id"]) == inv_id
    with c.session_factory() as s:
        ev = s.scalars(select(OutboxEvent).where(OutboxEvent.topic == "investigation.requested")).all()
        assert [e.key for e in ev] == [inv_id]  # a worker in another process will pick it up
    run_investigation(c.session_factory, HeuristicProvider(), inv_id)
    run_investigation(c.session_factory, HeuristicProvider(), inv_id)  # delivered twice: second is a no-op
    with c.session_factory() as s:
        invs = s.scalars(select(Investigation).where(Investigation.case_id == flagged["case_id"])).all()
        assert len(invs) == 1 and invs[0].status == "done"
