"""Currencies (USD/NGN) and languages (en, fr, yo, ha, ig, pcm) across the API."""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from payguard import i18n
from payguard.agent.runner import HeuristicProvider
from payguard.api.app import build_container, create_app
from payguard.api.security import create_client
from payguard.config import Settings
from payguard.currency import CurrencyError, FxTable
from payguard.db.models import Transaction
from payguard.db.session import _backfill_currency
from payguard.vision.verify import parse_currency
from tests.conftest import make_txn
from tests.helpers import REPO, build_tiny_model, fraud_like

RATE = 1550.0  # configs/currency.yaml
T0 = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path):
    s = Settings(database_url=f"sqlite:///{(tmp_path / 'cur.db').as_posix()}", artifacts_dir=tmp_path / "art",
                 data_dir=tmp_path / "data", rules_path=REPO / "configs" / "rules.yaml",
                 rails_config_dir=REPO / "configs" / "rails", sanctions_dir=tmp_path / "no-ofac",
                 currency_path=REPO / "configs" / "currency.yaml", rate_limit_rps=1000, rate_limit_burst=1000,
                 agent_provider="heuristic", agent_auto_investigate=False, frontend_dist=tmp_path / "dist")
    build_tiny_model(s.models_dir)
    c = build_container(s, provider=HeuristicProvider(), start_workers=False)
    keys = {role: create_client(c.session_factory, f"{role}-1", role)[1] for role in ("merchant", "analyst", "admin")}
    yo_key = create_client(c.session_factory, "ade", "analyst", locale="yo", currency="NGN", fx=c.fx)[1]
    with TestClient(create_app(c)) as client:
        h = {r: {"Authorization": f"Bearer {k}"} for r, k in keys.items()}
        h["yo_analyst"] = {"Authorization": f"Bearer {yo_key}"}
        yield client, c, h


def bank(i, **kw):
    return {"rail": "bank_transfer", "transaction_id": f"bt{i}", "event_time": (T0 + timedelta(minutes=i)).isoformat(),
            "account_id": f"acct-{i}", "account_age_days": 400, "beneficiary_account": f"benef-{i}", **kw}


# ---- currency ---------------------------------------------------------------------------------------------
def test_naira_card_payment_is_scored_on_its_dollar_value(env):
    client, _, h = env
    usd = client.post("/v1/transactions/score", headers=h["merchant"],
                      json=make_txn(1, amount=30.0, card="1111").model_dump(mode="json")).json()
    ngn = client.post("/v1/transactions/score", headers=h["merchant"],
                      json=make_txn(2, amount=30.0 * RATE, card="2222", currency="NGN").model_dump(mode="json")).json()
    # Same dollar value, fresh entity each: same risk. Before the fix ₦46,500 was scored as $46,500.
    assert ngn["decision"] == usd["decision"]
    assert ngn["fraud_probability"] == pytest.approx(usd["fraud_probability"], abs=1e-6)
    assert ngn["expected_loss"] == pytest.approx(usd["expected_loss"], rel=1e-6)
    assert (ngn["currency"], ngn["amount"], ngn["amount_usd"], ngn["fx_rate"]) == ("NGN", 30.0 * RATE, 30.0, RATE)
    assert ngn["expected_loss_local"] == pytest.approx(ngn["expected_loss"] * RATE, rel=1e-4)
    assert usd["currency"] == "USD" and usd["fx_rate"] == 1.0


def test_rail_amounts_are_converted_stored_in_usd_and_kept_as_submitted(env):
    client, c, h = env
    r = client.post("/v1/payments/score", headers=h["merchant"], json=bank(1, amount=465_000, currency="ngn")).json()
    assert (r["currency"], r["amount_usd"], r["fx_rate"]) == ("NGN", 300.0, RATE)
    with c.session_factory() as s:
        tx = s.get(Transaction, "bt1")
        assert (tx.amount, tx.currency, tx.amount_local) == (300.0, "NGN", 465_000)
        assert tx.payload["amount"] == 465_000 and tx.payload.get("amount_usd") is None  # payload as submitted
    # The caller's own conversion wins over our reference rate.
    r = client.post("/v1/payments/score", headers=h["merchant"],
                    json=bank(2, amount=465_000, currency="NGN", amount_usd=290.0)).json()
    assert r["amount_usd"] == 290.0 and r["fx_rate"] is None
    # Aggregates and listings are in USD, never naira added to dollars.
    rails = client.get("/v1/admin/overview", headers=h["admin"], params={"window": "all"}).json()["by_rail"]
    assert next(x for x in rails if x["rail"] == "bank_transfer")["amount"] == pytest.approx(590.0)
    item = client.get("/v1/transactions", headers=h["analyst"], params={"q": "bt1"}).json()["items"][0]
    assert (item["amount"], item["currency"], item["amount_local"]) == (300.0, "NGN", 465_000)
    detail = client.get("/v1/transactions/bt1", headers=h["analyst"]).json()
    assert detail["money"] == {"currency": "NGN", "amount": 465_000, "amount_usd": 300.0}
    # Idempotent replay reports the same money.
    again = client.post("/v1/payments/score", headers=h["merchant"], json=bank(1, amount=465_000, currency="NGN")).json()
    assert again["idempotent_replay"] and again["amount_usd"] == 300.0 and again["fx_rate"] == pytest.approx(RATE)


def test_unconvertible_amounts_are_rejected_not_guessed(env):
    client, _, h = env
    r = client.post("/v1/payments/score", headers=h["merchant"], json=bank(3, amount=10, currency="XYZ"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_currency"
    r = client.post("/v1/payments/score", headers=h["merchant"], json=bank(4, amount=10, currency=""))
    assert r.status_code == 422
    crypto = {"rail": "crypto", "transaction_id": "cx1", "event_time": T0.isoformat(), "amount": 0.5, "currency": "BTC",
              "account_id": "t1", "direction": "withdrawal", "asset": "BTC", "chain": "bitcoin",
              "counterparty_address": "1ExternalAddrAAAAAAAAAAAAAAAAAAA"}
    r = client.post("/v1/payments/score", headers=h["merchant"], json=crypto)  # 0.5 BTC is not $0.50
    assert r.status_code == 422 and r.json()["error"]["code"] == "amount_usd_required"
    assert client.post("/v1/payments/score", headers=h["merchant"], json={**crypto, "amount_usd": 30_000}).status_code == 200
    card = make_txn(5).model_dump(mode="json") | {"currency": "BTC"}
    assert client.post("/v1/transactions/score", headers=h["merchant"], json=card).json()["error"]["code"] == "unsupported_currency"


def test_fx_table_overrides_and_validation(tmp_path):
    fx = FxTable.load(REPO / "configs" / "currency.yaml", {"NGN": 1600})
    assert fx.to_usd(1600, "ngn") == 1.0 and fx.display == ["USD", "NGN"]
    with pytest.raises(CurrencyError):
        fx.to_usd(1, "GHS")
    with pytest.raises(ValueError):
        FxTable.load(None, {"NGN": 0})


def test_backfill_rebases_old_rows_to_usd(env):
    _, c, h = env
    engine = c.session_factory.kw["bind"]
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO api_clients (id, name, key_hash, role, active, created_at) "
                          "VALUES ('old', 'o', 'x', 'merchant', 1, :t)"), {"t": T0})
        for tid, payload, amount in (("old-ngn", {"amount": 15500.0, "currency": "NGN"}, 15500.0),
                                     ("old-btc", {"amount": 0.2, "currency": "BTC", "amount_usd": 12000.0}, 0.2),
                                     ("old-usd", {"amount": 40.0}, 40.0),
                                     ("old-mislabelled", {"amount": 0.1, "currency": "USD", "amount_usd": 6000.0}, 0.1)):
            conn.execute(text("INSERT INTO transactions (id, client_id, rail, payload, payload_hash, event_time, amount, "
                              "created_at) VALUES (:id, 'old', 'bank_transfer', :p, 'h', :t, :a, :t)"),
                         {"id": tid, "p": json.dumps(payload), "t": T0, "a": amount})
        _backfill_currency(conn, "sqlite", c.fx)
        rows = dict((r[0], r[1:]) for r in conn.execute(text(
            "SELECT id, amount, currency, amount_local FROM transactions WHERE id LIKE 'old-%'")))
    assert rows["old-ngn"] == (10.0, "NGN", 15500.0)
    assert rows["old-btc"] == (12000.0, "BTC", 0.2)
    assert rows["old-usd"] == (40.0, "USD", 40.0)
    assert rows["old-mislabelled"] == (6000.0, "USD", 0.1)  # the caller's amount_usd is what was scored


# ---- input hardening ----------------------------------------------------------------------------------------
def test_ids_times_and_signals_are_bounded(env):
    client, _, h = env
    post = lambda body: client.post("/v1/payments/score", headers=h["merchant"], json=body)  # noqa: E731
    assert post(bank(6, amount=10, transaction_id="a/b")).status_code == 422  # could never be read back
    assert post(bank(7, amount=10, transaction_id="   ")).status_code == 422
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert post(bank(8, amount=10, event_time=future)).status_code == 422
    card = make_txn(9).model_dump(mode="json")
    card["signals"] = {f"V{i}": i for i in range(1001)}
    assert client.post("/v1/transactions/score", headers=h["merchant"], json=card).status_code == 422


def test_labels_for_unknown_transactions_are_reported_not_stored(env):
    client, _, h = env
    client.post("/v1/payments/score", headers=h["merchant"], json=bank(10, amount=10))
    r = client.post("/v1/labels", headers=h["analyst"], json=[{"transaction_id": "bt10", "is_fraud": False},
                                                             {"transaction_id": "ghost", "is_fraud": True}]).json()
    assert r == {"accepted": 1, "unknown": ["ghost"], "unknown_count": 1}
    assert client.get("/v1/labels", headers=h["analyst"]).json()["total"] == 1


def test_receipt_currency_is_read_from_the_amount():
    assert [parse_currency(t) for t in ("NGN 12,000.00", "₦12,000", "$40.00", "12,000.00")] == ["NGN", "NGN", "USD", None]


# ---- languages ------------------------------------------------------------------------------------------------
def test_catalogs_are_complete_and_keep_placeholders():
    ids = set(i18n.msgids())
    ph = lambda s: set(re.findall(r"\{(\w+)\}", s))  # noqa: E731
    for loc in i18n.LOCALES:
        if loc == "en":
            continue
        cat = i18n.catalog(loc)
        assert set(cat) == ids, loc
        assert all(ph(k) == ph(v) and v.strip() for k, v in cat.items()), loc


def test_translate_handles_templates_nested_terms_and_unknown_text():
    from payguard.explain import describe

    assert i18n.translate("case already resolved as fraud", "fr") == "dossier déjà clôturé comme fraude"
    assert i18n.translate(describe("customer_cnt_1h", 3.0), "yo").startswith("Ìṣe Oníbàárà")
    assert i18n.translate("free text from an LLM", "ha") == "free text from an LLM"
    assert i18n.negotiate("de-DE, fr;q=0.8, yo;q=0.9") == "yo"
    assert i18n.normalize("fr-CA") == "fr" and i18n.normalize("xx") is None


def test_error_messages_follow_the_request_language_codes_do_not(env):
    client, _, h = env
    r = client.get("/v1/cases/case_nope", headers={**h["analyst"], "Accept-Language": "fr-FR,fr;q=0.9"})
    assert r.status_code == 404 and r.json()["error"] == {**r.json()["error"], "code": "not_found",
                                                          "message": "dossier inconnu(e)"}
    assert r.headers["content-language"] == "fr"
    r = client.get("/v1/cases/case_nope", headers=h["analyst"], params={"lang": "pcm"})
    assert r.json()["error"]["message"] == "we no know dis case"
    r = client.get("/v1/cases/case_nope", headers=h["analyst"])
    assert r.json()["error"]["message"] == "unknown case" and r.headers["content-language"] == "en"
    r = client.get("/v1/cases", headers={"Accept-Language": "ha"})  # before auth: header still applies
    assert r.json()["error"]["message"] == "babu alamar shiga (bearer token)"


def test_profile_preferences_follow_the_key(env):
    client, _, h = env
    me = client.get("/v1/auth/me", headers={**h["yo_analyst"], "Accept-Language": "en"}).json()
    assert me["preferences"] == {"locale": "yo", "currency": "NGN"} and me["locale"] == "yo"  # profile beats header
    r = client.get("/v1/cases/case_nope", headers=h["yo_analyst"])
    assert r.json()["error"]["message"] == "a kò mọ̀ ẹjọ́ yìí"

    r = client.patch("/v1/auth/me/preferences", headers=h["analyst"], json={"locale": "ig-NG", "currency": "ngn"})
    assert r.status_code == 200 and r.json()["preferences"] == {"locale": "ig", "currency": "NGN"}
    assert client.get("/v1/auth/me", headers=h["analyst"]).json()["locale"] == "ig"
    r = client.patch("/v1/auth/me/preferences", headers=h["analyst"], json={"currency": None})
    assert r.json()["preferences"] == {"locale": "ig", "currency": None}  # omitted fields unchanged
    assert client.patch("/v1/auth/me/preferences", headers=h["analyst"], json={"locale": "de"}).status_code == 422
    assert client.patch("/v1/auth/me/preferences", headers=h["analyst"], json={"currency": "BTC"}).status_code == 422
    audit = client.get("/v1/admin/audit", headers=h["admin"], params={"action": "client.preferences"}).json()
    assert audit["total"] == 2  # the two accepted changes

    r = client.post("/v1/admin/clients", headers=h["admin"], json={"name": "amina", "role": "merchant", "locale": "ha"})
    assert r.status_code == 201 and r.json()["preferences"] == {"locale": "ha", "currency": None}
    meta = client.get("/v1/meta").json()  # public: the sign-in page needs it
    assert [x["code"] for x in meta["locales"]] == ["en", "fr", "yo", "ha", "ig", "pcm"]
    assert meta["currencies"]["display"] == ["USD", "NGN"] and meta["currencies"]["rates"]["NGN"] == RATE


def test_reasons_and_explanations_are_translated_on_read(env):
    client, _, h = env
    flagged = None
    for i in range(20):
        r = client.post("/v1/transactions/score", headers={**h["merchant"], "Accept-Language": "fr"},
                        json=fraud_like(i, amount=3000.0).model_dump(mode="json")).json()
        if r.get("case_id"):
            flagged = r
            break
    assert flagged, "expected a flagged transaction"
    assert {x["code"] for x in flagged["reasons"]} >= {"model_risk"}  # codes never change
    assert "Risque de fraude élevé" in [x["detail"] for x in flagged["reasons"]]
    case = client.get(f"/v1/cases/{flagged['case_id']}", headers=h["yo_analyst"]).json()
    assert "Ewu jìbìtì ga" in [x["detail"] for x in case["model"]["reasons"]]
    en = client.get(f"/v1/cases/{flagged['case_id']}", headers=h["analyst"]).json()
    assert [e["feature"] for e in case["explanation"]] == [e["feature"] for e in en["explanation"]]
    rules = client.get("/v1/admin/rules", headers={**h["admin"], "Accept-Language": "fr"}).json()["rules"]
    assert any(r["description"].startswith("Premier virement") for r in rules)
