"""Every payment rail is scored and reviewable through the same pipeline; rail-specific typologies fire."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from payguard.agent.runner import HeuristicProvider
from payguard.api.app import build_container, create_app
from payguard.api.security import create_client
from payguard.config import Settings
from payguard.crypto.screening import SanctionsScreener, normalize
from payguard.schemas import PaymentIn
from tests.helpers import REPO, build_tiny_model

T0 = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
SANCTIONED_BTC = "1SanctionedTestAddressXXXXXXXXXXXX"
SANCTIONED_EVM = "0x8589427373D6D84E98730D7795D8f6f8731FDA16"
pay = TypeAdapter(PaymentIn).validate_python


def bank(i, sender="alice", beneficiary="bob", amount=100.0, minutes=0.0, **kw):
    return pay({"rail": "bank_transfer", "transaction_id": f"bt{i}", "event_time": T0 + timedelta(minutes=minutes),
                "amount": amount, "currency": "NGN", "amount_usd": amount, "account_id": sender,
                "account_age_days": 400, "beneficiary_account": beneficiary, "channel": "app", "scheme": "NIP", **kw})


def momo(i, **kw):
    base = {"rail": "mobile_money", "transaction_id": f"mm{i}", "event_time": T0, "amount": 200.0, "account_id": "w1",
            "account_age_days": 300, "kind": "p2p", "counterparty_wallet": "w2"}
    return pay({**base, **kw})


def crypto(i, direction="withdrawal", address="1ExternalAddrAAAAAAAAAAAAAAAAAAA", amount_usd=500.0, minutes=0.0, **kw):
    return pay({"rail": "crypto", "transaction_id": f"cx{i}", "event_time": T0 + timedelta(minutes=minutes),
                "amount": amount_usd / 60000, "currency": "BTC", "amount_usd": amount_usd, "account_id": "acct-1",
                "account_age_days": 200, "direction": direction, "asset": "BTC", "chain": "bitcoin",
                "counterparty_address": address, **kw})


@pytest.fixture
def env(tmp_path):
    sdir = tmp_path / "ofac"
    sdir.mkdir()
    (sdir / "sanctioned_addresses_XBT.txt").write_text(SANCTIONED_BTC + "\n")
    (sdir / "sanctioned_addresses_ETH.txt").write_text(SANCTIONED_EVM + "\n")
    s = Settings(database_url=f"sqlite:///{(tmp_path / 'r.db').as_posix()}", artifacts_dir=tmp_path / "art",
                 rules_path=REPO / "configs" / "rules.yaml", rails_config_dir=REPO / "configs" / "rails",
                 sanctions_dir=sdir, agent_provider="heuristic")
    build_tiny_model(s.models_dir)
    c = build_container(s, provider=HeuristicProvider(), start_workers=False)
    cid = create_client(c.session_factory, "m", "merchant")[0]
    return c, cid, s


def test_sanctions_normalisation():
    scr = SanctionsScreener({"ETH": {SANCTIONED_EVM}, "XBT": {SANCTIONED_BTC}})
    assert scr.screen(SANCTIONED_EVM.lower()).hit and scr.screen(SANCTIONED_EVM.upper().replace("0X", "0x")).hit
    assert not scr.screen(SANCTIONED_BTC.lower()).hit  # base58 is case-sensitive: no false match
    assert normalize("BC1QXYZ") == "bc1qxyz"


def test_bank_transfer_mule_fan_in_is_held_for_review(env):
    c, cid, _ = env
    baseline = c.scoring.score(bank(0, sender="s0", beneficiary="mule"), cid)
    for i in range(1, 7):  # six different victims pay the same account within hours
        r = c.scoring.score(bank(i, sender=f"victim{i}", beneficiary="mule", amount=1500.0, minutes=30 * i), cid)
    assert baseline.decision == "approve"
    assert r.decision == "review" and "hold_payment" in r.required_actions and r.rail == "bank_transfer"
    assert r.case_id is not None


def test_new_payee_large_amount_scores_higher_than_repeat_payee(env):
    c, cid, _ = env
    for i in range(3):
        c.scoring.score(bank(10 + i, sender="carol", beneficiary="landlord", amount=800.0, minutes=i * 60 * 24 * 7), cid)
    repeat = c.scoring.score(bank(20, sender="carol", beneficiary="landlord", amount=800.0, minutes=60 * 24 * 30), cid)
    new = c.scoring.score(bank(21, sender="carol", beneficiary="stranger", amount=8000.0, minutes=60 * 24 * 30 + 5), cid)
    assert new.fraud_probability > repeat.fraud_probability


def test_mobile_money_sim_swap_cash_out_is_reviewed(env):
    c, cid, _ = env
    r = c.scoring.score(momo(1, kind="cash_out", amount=150.0, sim_swap_days=0.5, agent_id="agent-7"), cid)
    assert r.decision == "review" and "sim_swap_cash_out" in r.rules_triggered
    ok = c.scoring.score(momo(2, account_id="w9", sim_swap_days=400), cid)
    assert ok.decision == "approve"


def test_crypto_withdrawal_to_sanctioned_address_is_blocked(env):
    c, cid, _ = env
    r = c.scoring.score(crypto(1, address=SANCTIONED_BTC), cid)
    assert r.decision == "decline"
    assert {"block_withdrawal", "file_sanctions_report"} <= set(r.required_actions)
    assert any(x.code == "sanctions:ofac_sdn" for x in r.reasons)


def test_crypto_deposit_from_sanctioned_address_is_frozen_not_declined(env):
    c, cid, _ = env
    r = c.scoring.score(crypto(2, direction="deposit", address=SANCTIONED_EVM.lower(), chain="ethereum",
                               asset="ETH", tx_hash="0xabc"), cid)
    assert r.decision == "review"  # funds already arrived on-chain; they cannot be refused
    assert {"freeze_funds", "file_sanctions_report"} <= set(r.required_actions)


def test_crypto_pass_through_withdrawal_scores_higher(env):
    c, cid, _ = env
    normal = c.scoring.score(crypto(3, address="1LongTimeColdWalletBBBBBBBBBBBBBB", amount_usd=400.0), cid)
    c.scoring.score(crypto(4, direction="deposit", address="1SourceCCCCCCCCCCCCCCCCCCCCCCC", amount_usd=9000.0,
                           minutes=60), cid)
    passthrough = c.scoring.score(crypto(5, address="1FreshDDDDDDDDDDDDDDDDDDDDDDDDD", amount_usd=8800.0,
                                         minutes=70), cid)
    assert passthrough.fraud_probability > normal.fraud_probability
    assert passthrough.decision == "review" and "rapid_pass_through" in passthrough.rules_triggered
    assert "hold_withdrawal" in passthrough.required_actions


def test_travel_rule_missing_for_vasp_transfer_above_threshold(env):
    c, cid, _ = env
    r = c.scoring.score(crypto(6, amount_usd=5000.0, counterparty_vasp="OtherExchange"), cid)
    assert "travel_rule_incomplete" in r.rules_triggered and r.decision == "review"
    tr = {"originator_name": "Ada Obi", "originator_account": "acct-1", "beneficiary_name": "Ada Obi",
          "beneficiary_account": "ext-99"}
    ok = c.scoring.score(crypto(7, amount_usd=5000.0, counterparty_vasp="OtherExchange", travel_rule=tr,
                                address="1Another1EEEEEEEEEEEEEEEEEEEEEE", minutes=500), cid)
    assert "travel_rule_incomplete" not in ok.rules_triggered


def test_all_rails_share_one_queue_and_agent_reads_rail_signals(env):
    c, cid, s = env
    key = create_client(c.session_factory, "a", "analyst")[1]
    mkey = create_client(c.session_factory, "m2", "merchant")[1]
    with TestClient(create_app(c)) as client:
        h, hm = {"Authorization": f"Bearer {key}"}, {"Authorization": f"Bearer {mkey}"}
        r = client.post("/v1/payments/score", headers=hm, json=crypto(8, address=SANCTIONED_BTC).model_dump(mode="json"))
        assert r.status_code == 200 and r.json()["rail"] == "crypto"
        r2 = client.post("/v1/payments/score", headers=hm,
                         json=momo(9, kind="cash_out", sim_swap_days=0.1, amount=500.0).model_dump(mode="json"))
        assert r2.json()["decision"] == "review"
        queue = client.get("/v1/cases", headers=h).json()
        assert {q["transaction_id"] for q in queue} >= {"cx8", "mm9"}
        case = next(q for q in queue if q["transaction_id"] == "cx8")
        detail = client.get(f"/v1/cases/{case['case_id']}", headers=h).json()
        assert detail["explanation"]  # scorecard contributions stored at scoring time
        inv = client.post(f"/v1/cases/{case['case_id']}/investigate", headers=h).json()
        from payguard.agent.service import run_investigation

        done = run_investigation(c.session_factory, c.provider, inv["investigation_id"], c.explainer)
        assert done.recommendation == "fraud"
        assert "get_payment_risk_signals" in [t["tool"] for t in done.trace]
        assert done.report["grounding"]["citation_valid_rate"] == 1.0


def test_idempotency_and_disabled_rail(env, tmp_path):
    c, cid, s = env
    a = c.scoring.score(crypto(10), cid)
    b = c.scoring.score(crypto(10), cid)
    assert b.idempotent_replay and b.decision == a.decision and b.rail == "crypto"
    card_only = build_container(s.model_copy(update={"rails_enabled": ["card"],
                                                     "database_url": f"sqlite:///{(tmp_path / 'c.db').as_posix()}"}),
                                provider=HeuristicProvider(), start_workers=False)
    key = create_client(card_only.session_factory, "m", "merchant")[1]
    with TestClient(create_app(card_only)) as client:
        r = client.post("/v1/payments/score", json=crypto(11).model_dump(mode="json"),
                        headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "rail_not_enabled"
