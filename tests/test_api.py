import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from payguard.agent.runner import HeuristicProvider
from payguard.api.app import build_container, create_app
from payguard.api.security import create_client
from payguard.config import Settings
from payguard.db.models import Case, DecisionRecord, Investigation, Label, OutboxEvent, Transaction
from tests.conftest import make_txn
from tests.helpers import REPO, build_tiny_model, fraud_like


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'pg.db').as_posix()}", artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data", rules_path=REPO / "configs" / "rules.yaml", rate_limit_rps=1000,
        rate_limit_burst=1000, agent_provider="heuristic")
    build_tiny_model(settings.models_dir)
    container = build_container(settings, provider=HeuristicProvider(), start_workers=True)
    keys = {role: create_client(container.session_factory, role, role)[1] for role in ("merchant", "analyst", "admin")}
    with TestClient(create_app(container)) as client:
        yield client, container, {r: {"Authorization": f"Bearer {k}"} for r, k in keys.items()}, settings


def body(t):
    return t.model_dump(mode="json")


def test_auth_and_roles(env):
    client, _, h, _ = env
    assert client.post("/v1/transactions/score", json=body(make_txn(1))).status_code == 401
    assert client.post("/v1/transactions/score", json=body(make_txn(1)),
                       headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/v1/transactions/score", json=body(make_txn(1)), headers=h["analyst"]).status_code == 403
    assert client.get("/v1/cases", headers=h["merchant"]).status_code == 403
    assert client.get("/v1/models", headers=h["analyst"]).status_code == 403
    assert client.get("/v1/models", headers=h["admin"]).status_code == 200


def test_validation_rejects_bad_payload(env):
    client, _, h, _ = env
    bad = body(make_txn(1))
    bad["amount"] = -5
    r = client.post("/v1/transactions/score", json=bad, headers=h["merchant"])
    assert r.status_code == 422


def test_score_is_idempotent_and_conflicts_on_changed_payload(env):
    client, c, h, _ = env
    t = make_txn(1)
    r1 = client.post("/v1/transactions/score", json=body(t), headers=h["merchant"]).json()
    r2 = client.post("/v1/transactions/score", json=body(t), headers=h["merchant"]).json()
    assert r1["decision"] == r2["decision"] and r1["fraud_probability"] == r2["fraud_probability"]
    assert not r1["idempotent_replay"] and r2["idempotent_replay"]
    changed = body(t) | {"amount": 999.0}
    assert client.post("/v1/transactions/score", json=changed, headers=h["merchant"]).status_code == 409
    with c.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(DecisionRecord)) == 1


def test_concurrent_duplicate_requests_create_one_decision(env):
    client, c, h, _ = env
    t = fraud_like(1)
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda _: client.post("/v1/transactions/score", json=body(t), headers=h["merchant"]),
                              range(8)))
    assert all(r.status_code == 200 for r in results)
    assert len({r.json()["fraud_probability"] for r in results}) == 1
    with c.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Transaction)) == 1
        assert s.scalar(select(func.count()).select_from(Case)) <= 1


def test_rate_limit(tmp_path):
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'rl.db').as_posix()}", artifacts_dir=tmp_path / "a",
                        rules_path=REPO / "configs" / "rules.yaml", rate_limit_rps=0.001, rate_limit_burst=3)
    build_tiny_model(settings.models_dir)
    c = build_container(settings, provider=HeuristicProvider(), start_workers=False)
    key = create_client(c.session_factory, "m", "merchant")[1]
    with TestClient(create_app(c)) as client:
        codes = [client.post("/v1/transactions/score", json=body(make_txn(i)),
                             headers={"Authorization": f"Bearer {key}"}).status_code for i in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]


def _wait(pred, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def test_flag_creates_case_outbox_and_agent_investigation(env):
    client, c, h, _ = env
    for i in range(30):  # warm history for this customer / device
        client.post("/v1/transactions/score", json=body(fraud_like(i, amount=50.0)), headers=h["merchant"])
    flagged = None
    for i in range(30, 80):
        r = client.post("/v1/transactions/score", json=body(fraud_like(i, amount=3000.0)), headers=h["merchant"]).json()
        if r["decision"] != "approve":
            flagged = r
            break
    assert flagged is not None, "model never flagged a fraud-pattern transaction"
    assert flagged["case_id"] and any(r["code"] == "model_risk" for r in flagged["reasons"])
    assert not any(r["code"].startswith(("customer_", "device_", "sig_")) for r in flagged["reasons"])  # no internals
    case_id = flagged["case_id"]

    # outbox is relayed, the case.created consumer starts an investigation, the agent files a report
    assert _wait(lambda: _inv_done(c, case_id)), "investigation did not complete"
    detail = client.get(f"/v1/cases/{case_id}", headers=h["analyst"]).json()
    assert detail["explanation"] and all(e["weight"] > 0 for e in detail["explanation"])  # TreeSHAP, async
    inv = detail["investigations"][-1]
    assert inv["status"] == "done" and inv["recommendation"] in ("fraud", "legit", "escalate")
    assert inv["report"]["grounding"]["citation_valid_rate"] == 1.0
    with c.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None))) == 0

    queue = client.get("/v1/cases", headers=h["analyst"]).json()["items"]
    assert queue[0]["priority"] >= queue[-1]["priority"]

    # analyst resolution records the label; a conflicting second resolution is rejected
    assert client.post(f"/v1/cases/{case_id}/resolve", json={"resolution": "fraud"}, headers=h["analyst"]).status_code == 200
    assert client.post(f"/v1/cases/{case_id}/resolve", json={"resolution": "legit"}, headers=h["analyst"]).status_code == 409
    with c.session_factory() as s:
        assert s.get(Label, flagged["transaction_id"]).is_fraud is True


def _inv_done(c, case_id) -> bool:
    with c.session_factory() as s:
        inv = s.scalar(select(Investigation).where(Investigation.case_id == case_id))
        return inv is not None and inv.status in ("done", "failed")


def test_challenger_scores_in_shadow_and_promotion_hot_swaps(env):
    client, c, h, settings = env
    v2 = build_tiny_model(settings.models_dir, version="lgbm-test-2", seed=1)
    r = client.post("/v1/models/promote", json={"alias": "challenger", "version": v2, "reason": "shadow test"},
                    headers=h["admin"])
    assert r.status_code == 200
    client.post("/v1/transactions/score", json=body(make_txn(5)), headers=h["merchant"])
    detail = client.get("/v1/transactions/t5", headers=h["analyst"]).json()
    assert detail["decision"]["shadow"]["version"] == v2
    assert detail["decision"]["model_version"] == "lgbm-test-1"  # champion still decides
    client.post("/v1/models/promote", json={"alias": "champion", "version": v2, "reason": "promote"}, headers=h["admin"])
    assert client.post("/v1/transactions/score", json=body(make_txn(6)), headers=h["merchant"]).json()["model_version"] == v2


def test_readiness_metrics_and_drift(env):
    client, _, h, _ = env
    assert client.get("/readyz").json()["ready"] is True
    client.post("/v1/transactions/score", json=body(make_txn(1)), headers=h["merchant"])
    m = client.get("/metrics").text
    assert "payguard_decisions_total" in m and "payguard_score_stage_seconds" in m
    assert client.get("/v1/monitoring/drift", headers=h["analyst"]).json()["status"] == "insufficient_data"


class BrokenStore:
    def transact(self, *a, **k):
        raise ConnectionError("redis down")

    def ping(self):
        return False


def test_feature_store_outage_degrades_instead_of_failing(tmp_path):
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'd.db').as_posix()}", artifacts_dir=tmp_path / "a",
                        rules_path=REPO / "configs" / "rules.yaml")
    build_tiny_model(settings.models_dir)
    c = build_container(settings, store=BrokenStore(), provider=HeuristicProvider(), start_workers=False)
    key = create_client(c.session_factory, "m", "merchant")[1]
    with TestClient(create_app(c)) as client:
        r = client.post("/v1/transactions/score", json=body(make_txn(1)), headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200 and r.json()["degraded"] is True
        assert client.get("/readyz").status_code == 503  # load balancer / alerting sees it
