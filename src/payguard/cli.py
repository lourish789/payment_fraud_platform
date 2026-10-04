"""payguard <command>. Run `payguard -h` for the list."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from payguard.config import get_settings


def _json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _save(name: str, obj) -> None:
    out = get_settings().artifacts_dir / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(obj, indent=2, default=str))
    _json(obj)


def _container(workers: bool = False):
    from payguard.api.app import build_container

    return build_container(_settings_with_snapshot(), start_workers=workers)


def _settings_with_snapshot():
    s = get_settings()
    snap = s.processed_dir / "online_snapshot.jsonl.gz"
    if s.online_snapshot is None and snap.exists():
        s = s.model_copy(update={"online_snapshot": snap})
    return s


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="payguard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest", help="download + verify + normalise IEEE-CIS")
    sub.add_parser("backfill", help="offline feature backfill (same code as serving) + online snapshot")
    sub.add_parser("materialize", help="load the offline snapshot into the configured online store (Redis)")
    t = sub.add_parser("train", help="train, calibrate, evaluate and register a model")
    t.add_argument("--no-ablations", action="store_true")
    pr = sub.add_parser("promote", help="point an alias at a model version")
    pr.add_argument("alias", choices=["champion", "challenger"])
    pr.add_argument("version")
    pr.add_argument("--reason", required=True)
    cc = sub.add_parser("create-client", help="create an API client and print its key (shown once)")
    cc.add_argument("--name", required=True)
    cc.add_argument("--role", required=True, choices=["merchant", "analyst", "admin"])
    cc.add_argument("--locale", help="profile language: en, fr, yo, ha, ig or pcm (default: not set)")
    cc.add_argument("--currency", help="profile display currency, e.g. USD or NGN (default: not set)")
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--workers", type=int, default=1)
    sub.add_parser("worker", help="run outbox relay + investigation consumer as a standalone process")
    rp = sub.add_parser("replay", help="score the test month in-process through the real service")
    rp.add_argument("--n", type=int, default=None)
    rp.add_argument("--offset", type=int, default=0)
    pa = sub.add_parser("parity", help="training/serving skew audit")
    pa.add_argument("--sample", type=int, default=None)
    lt = sub.add_parser("loadtest")
    lt.add_argument("--url", default="http://127.0.0.1:8000")
    lt.add_argument("--key", required=True)
    lt.add_argument("--n", type=int, default=2000)
    lt.add_argument("--concurrency", type=int, default=16)
    lt.add_argument("--offset", type=int, default=0)
    lt.add_argument("--label", default="default", help="topology being measured; results saved per label")
    ae = sub.add_parser("agent-eval")
    ae.add_argument("--n", type=int, default=100)
    ae.add_argument("--provider", default="auto", choices=["auto", "claude", "heuristic"])
    sub.add_parser("vision-eval")
    sub.add_parser("crypto-ingest", help="download Elliptic++ and the OFAC SDN address list")
    sub.add_parser("crypto-train", help="train + evaluate the on-chain transaction risk model")
    sub.add_parser("crypto-intel", help="build the point-in-time address intelligence store + counterparty combiner")
    sub.add_parser("drift-demo")
    a = ap.parse_args(argv)
    s = get_settings()

    if a.cmd == "ingest":
        from payguard.data.ingest import ingest

        print(ingest())
    elif a.cmd == "crypto-ingest":
        from payguard.crypto.data import ingest as crypto_ingest

        _json(crypto_ingest(s.raw_dir / "crypto", s.processed_dir))
    elif a.cmd == "crypto-train":
        from payguard.crypto.model import train as crypto_train

        print(crypto_train(s.processed_dir, s.raw_dir / "crypto", s.models_dir))
    elif a.cmd == "crypto-intel":
        from payguard.crypto.intel import AddressIntel, build, fit_combiner

        d = s.crypto_dir / (s.crypto_dir / "champion.txt").read_text().strip()
        _json(build(d, s.raw_dir / "crypto", d / "address_intel.db"))
        _save("crypto_combiner", fit_combiner(AddressIntel.open(d / "address_intel.db"), s.raw_dir / "crypto", d,
                                              d / "combiner.json"))
    elif a.cmd == "backfill":
        from payguard.features.backfill import backfill

        print(backfill())
    elif a.cmd == "materialize":
        from payguard.features.store import load_snapshot, make_store

        if not s.redis_url:
            print("PAYGUARD_REDIS_URL is not set; the in-memory store loads the snapshot at startup instead")
            return 1
        _json(load_snapshot(make_store(s.redis_url), s.processed_dir / "online_snapshot.jsonl.gz"))
    elif a.cmd == "train":
        from payguard.models.train import train

        print(train(ablations=not a.no_ablations))
    elif a.cmd == "promote":
        from payguard.models.registry import Registry

        Registry(s.models_dir).set_alias(a.alias, a.version, a.reason)
        _json(Registry(s.models_dir).read())
    elif a.cmd == "create-client":
        from payguard.api.security import create_client
        from payguard.currency import FxTable
        from payguard.db.session import init_db, make_engine, make_session_factory

        fx = FxTable.load(s.currency_path, s.fx_rates)
        engine = make_engine(s.database_url)
        init_db(engine, fx)
        cid, key = create_client(make_session_factory(engine), a.name, a.role, a.locale, a.currency, fx)
        _json({"client_id": cid, "role": a.role, "api_key": key, "locale": a.locale, "currency": a.currency})
    elif a.cmd == "serve":
        import os

        import uvicorn

        from payguard.api.app import create_app

        snap = _settings_with_snapshot().online_snapshot
        if snap:
            os.environ.setdefault("PAYGUARD_ONLINE_SNAPSHOT", str(snap))
        get_settings.cache_clear()
        uvicorn.run(create_app(), host=a.host, port=a.port, log_level="info", workers=1)
    elif a.cmd == "worker":
        import signal
        import threading

        c = _container(workers=False)
        from payguard.workers import WorkerPool

        pool = WorkerPool(c.session_factory, c.bus, c.provider, c.explainer, s.agent_auto_investigate)
        pool.start()
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        stop.wait()
        pool.stop()
    elif a.cmd == "replay":
        from payguard.simulate import replay

        _save("replay", replay(_container(), s.processed_dir, n=a.n, offset=a.offset))
    elif a.cmd == "parity":
        from payguard.simulate import parity_audit

        _save("parity", parity_audit(_container().session_factory, s.processed_dir, sample=a.sample))
    elif a.cmd == "loadtest":
        from payguard.simulate import loadtest

        res = asyncio.run(loadtest(a.url, a.key, s.processed_dir, a.n, a.concurrency, a.offset))
        _save(f"loadtest_{a.label}_c{a.concurrency}", {"label": a.label, **res})
    elif a.cmd == "agent-eval":
        from payguard.agent.evaluate import evaluate_agent
        from payguard.agent.runner import make_provider

        c = _container()
        provider = make_provider(a.provider, s.agent_model, s.agent_max_steps, s.agent_max_output_tokens)
        rep = evaluate_agent(c.session_factory, provider, n=a.n, out=s.artifacts_dir / "reports", explainer=c.explainer)
        rep.pop("cases")
        _json(rep)
    elif a.cmd == "vision-eval":
        from payguard.vision.evaluate import evaluate

        _json(evaluate(s.artifacts_dir / "vision")["summary"])
    elif a.cmd == "drift-demo":
        from payguard.simulate import drift_demo

        _json(drift_demo(_settings_with_snapshot(), s.artifacts_dir / "reports"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
