"""Online feature stores.

Both implementations expose one primitive, `transact`, an atomic read-modify-write over a set of
entity states, guarded by a per-transaction dedupe marker:

  * first time a transaction id is seen: read states -> fn(states) -> (features, new_states);
    write new states and the dedupe marker (holding the features) atomically;
  * a retry of the same transaction id returns the *stored* features and writes nothing, so client
    retries can neither double-count velocity nor see different features than the first attempt.

RedisFeatureStore uses WATCH/MULTI optimistic concurrency: two concurrent transactions for the same
card conflict, one retries, and neither update is lost.
"""

from __future__ import annotations

import gzip
import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol

TransactFn = Callable[[dict[str, dict | None]], tuple[dict, dict[str, dict]]]

STATE_TTL_S = 180 * 86400
DEDUPE_TTL_S = 7 * 86400


class FeatureStore(Protocol):
    def transact(self, txn_id: str | None, keys: list[str], fn: TransactFn) -> tuple[dict, bool]:
        """Returns (features, replayed). txn_id=None disables dedupe (offline backfill)."""

    def get_many(self, keys: list[str]) -> dict[str, dict | None]: ...

    def put_many(self, states: dict[str, dict]) -> None: ...

    def ping(self) -> bool: ...


class InMemoryFeatureStore:
    def __init__(self, dedupe_capacity: int = 500_000):
        self._states: dict[str, dict] = {}
        self._seen: OrderedDict[str, dict] = OrderedDict()
        self._cap = dedupe_capacity
        self._lock = threading.Lock()

    def transact(self, txn_id, keys, fn):
        with self._lock:
            if txn_id is not None and txn_id in self._seen:
                return self._seen[txn_id], True
            features, new_states = fn({k: self._states.get(k) for k in keys})
            self._states.update(new_states)
            if txn_id is not None:
                self._seen[txn_id] = features
                if len(self._seen) > self._cap:
                    self._seen.popitem(last=False)
            return features, False

    def get_many(self, keys):
        with self._lock:
            return {k: self._states.get(k) for k in keys}

    def put_many(self, states):
        with self._lock:
            self._states.update(states)

    def items(self) -> Iterable[tuple[str, dict]]:
        return list(self._states.items())

    def __len__(self) -> int:
        return len(self._states)

    def ping(self) -> bool:
        return True


class RedisFeatureStore:
    PREFIX = "pg:fs:"
    SEEN = "pg:seen:"

    def __init__(self, client, max_retries: int = 20):
        self.r = client
        self.max_retries = max_retries
        self.conflicts = 0  # exported as a metric

    def transact(self, txn_id, keys, fn):
        import redis

        rkeys = [self.PREFIX + k for k in keys]
        seen_key = self.SEEN + txn_id if txn_id is not None else None
        watch = rkeys + ([seen_key] if seen_key else [])
        for _ in range(self.max_retries):
            with self.r.pipeline() as pipe:
                try:
                    pipe.watch(*watch)
                    if seen_key:
                        prior = pipe.get(seen_key)
                        if prior is not None:
                            pipe.unwatch()
                            return json.loads(prior), True
                    raw = pipe.mget(rkeys)
                    states = {k: (json.loads(v) if v is not None else None) for k, v in zip(keys, raw)}
                    features, new_states = fn(states)
                    pipe.multi()
                    for k, st in new_states.items():
                        pipe.set(self.PREFIX + k, json.dumps(st, separators=(",", ":")), ex=STATE_TTL_S)
                    if seen_key:
                        pipe.set(seen_key, json.dumps(features, allow_nan=True), ex=DEDUPE_TTL_S)
                    pipe.execute()
                    return features, False
                except redis.WatchError:
                    self.conflicts += 1
                    continue
        raise RuntimeError(f"feature store contention: gave up after {self.max_retries} retries")

    def get_many(self, keys):
        raw = self.r.mget([self.PREFIX + k for k in keys])
        return {k: (json.loads(v) if v is not None else None) for k, v in zip(keys, raw)}

    def put_many(self, states, batch: int = 5000):
        items = list(states.items())
        for i in range(0, len(items), batch):
            with self.r.pipeline(transaction=False) as pipe:
                for k, st in items[i:i + batch]:
                    pipe.set(self.PREFIX + k, json.dumps(st, separators=(",", ":")), ex=STATE_TTL_S)
                pipe.execute()

    def ping(self) -> bool:
        try:
            return bool(self.r.ping())
        except Exception:
            return False


def save_snapshot(store: InMemoryFeatureStore, path: Path, as_of: str) -> None:
    """Materialise offline state for loading into an online store (the 'backfill -> online' step)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"as_of": as_of, "entities": len(store)}) + "\n")
        for k, st in store.items():
            f.write(json.dumps([k, st], separators=(",", ":")) + "\n")


def load_snapshot(store: FeatureStore, path: Path, batch: int = 20_000) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        header = json.loads(f.readline())
        buf: dict[str, dict] = {}
        for line in f:
            k, st = json.loads(line)
            buf[k] = st
            if len(buf) >= batch:
                store.put_many(buf)
                buf = {}
        if buf:
            store.put_many(buf)
    return header


def make_store(redis_url: str | None) -> FeatureStore:
    if not redis_url:
        return InMemoryFeatureStore()
    import redis

    return RedisFeatureStore(redis.Redis.from_url(redis_url))
