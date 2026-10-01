"""API-key auth with roles, and per-client token-bucket rate limiting.

Keys are random 32-byte tokens shown once at creation; only their SHA-256 is stored (a DB leak does
not leak credentials; SHA-256 rather than bcrypt because the tokens are high-entropy and this runs on
every request). Lookups are cached for 60s so auth costs no DB round-trip on the hot path.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from payguard.db.models import ApiClient

ROLES = {"merchant": 1, "analyst": 2, "admin": 3}


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_client(session_factory: sessionmaker, name: str, role: str) -> tuple[str, str]:
    if role not in ROLES:
        raise ValueError(f"role must be one of {sorted(ROLES)}")
    key = "pg_" + secrets.token_urlsafe(32)
    with session_factory() as s, s.begin():
        c = ApiClient(name=name, key_hash=hash_key(key), role=role)
        s.add(c)
        s.flush()
        return c.id, key


@dataclass(frozen=True)
class Principal:
    client_id: str
    name: str
    role: str

    def allows(self, role: str) -> bool:
        # admin can do everything; analysts can't score, merchants can't read cases.
        return self.role == role or self.role == "admin"


class Authenticator:
    def __init__(self, session_factory: sessionmaker, ttl_s: float = 60.0):
        self.sf = session_factory
        self.ttl = ttl_s
        self._cache: dict[str, tuple[float, Principal | None]] = {}
        self._lock = threading.Lock()

    def resolve(self, key: str) -> Principal | None:
        h = hash_key(key)
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(h)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        with self.sf() as s:
            c = s.scalar(select(ApiClient).where(ApiClient.key_hash == h, ApiClient.active.is_(True)))
            p = Principal(c.id, c.name, c.role) if c else None
        with self._lock:
            self._cache[h] = (now, p)
        return p


class TokenBucket:
    """In-process token bucket. Behind a load balancer with N replicas, use RedisTokenBucket so the
    limit is global rather than N x per-replica."""

    def __init__(self, rate: float, burst: int):
        self.rate, self.burst = rate, burst
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(key, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= cost:
                self._state[key] = (tokens - cost, now)
                return True, 0.0
            self._state[key] = (tokens, now)
            return False, (cost - tokens) / self.rate


class RedisTokenBucket:
    """Same algorithm, state in Redis, updated with WATCH/MULTI so concurrent replicas can't overspend."""

    def __init__(self, client, rate: float, burst: int):
        self.r, self.rate, self.burst = client, rate, burst

    def allow(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        import redis

        k = f"pg:rl:{key}"
        for _ in range(10):
            with self.r.pipeline() as pipe:
                try:
                    pipe.watch(k)
                    now = time.time()
                    raw = pipe.hmget(k, "tokens", "ts")
                    tokens = float(raw[0]) if raw[0] is not None else float(self.burst)
                    last = float(raw[1]) if raw[1] is not None else now
                    tokens = min(self.burst, tokens + max(now - last, 0) * self.rate)
                    ok = tokens >= cost
                    pipe.multi()
                    pipe.hset(k, mapping={"tokens": tokens - cost if ok else tokens, "ts": now})
                    pipe.expire(k, 3600)
                    pipe.execute()
                    return ok, 0.0 if ok else (cost - tokens) / self.rate
                except redis.WatchError:
                    continue
        return True, 0.0  # fail open under extreme contention: availability over strictness
