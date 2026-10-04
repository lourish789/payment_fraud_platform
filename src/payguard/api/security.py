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


def new_key() -> str:
    return "pg_" + secrets.token_urlsafe(32)


def validate_preferences(locale: str | None, currency: str | None, fx=None) -> tuple[str | None, str | None]:
    """Normalise profile preferences; ValueError (with an English message id) if unsupported."""
    from payguard.i18n import LOCALES, normalize

    loc = None
    if locale:
        loc = normalize(locale)
        if loc is None:
            raise ValueError(f"unsupported locale {locale} (supported: {', '.join(LOCALES)})")
    cur = currency.strip().upper() if currency else None
    if cur and fx is not None and cur not in fx.display:
        raise ValueError(f"currency {cur} cannot be used for display (supported: {', '.join(fx.display)})")
    return loc, cur


def create_client(session_factory: sessionmaker, name: str, role: str, locale: str | None = None,
                  currency: str | None = None, fx=None) -> tuple[str, str]:
    if role not in ROLES:
        raise ValueError(f"role must be one of {sorted(ROLES)}")
    locale, currency = validate_preferences(locale, currency, fx)
    key = new_key()
    with session_factory() as s, s.begin():
        c = ApiClient(name=name, key_hash=hash_key(key), role=role, key_prefix=key[:10], locale=locale,
                      display_currency=currency)
        s.add(c)
        s.flush()
        return c.id, key


def ensure_admin(session_factory: sessionmaker, key: str, name: str = "bootstrap-admin") -> bool:
    """Create an admin client with a caller-chosen key unless one with that key exists. True if created."""
    if len(key) < 24:
        raise ValueError("bootstrap admin key must be at least 24 characters")
    with session_factory() as s, s.begin():
        if s.scalar(select(ApiClient.id).where(ApiClient.key_hash == hash_key(key))):
            return False
        s.add(ApiClient(name=name, key_hash=hash_key(key), role="admin", key_prefix=key[:10]))
        return True


@dataclass(frozen=True)
class Principal:
    client_id: str
    name: str
    role: str
    locale: str | None = None  # profile preferences (None = not chosen)
    currency: str | None = None

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
            p = Principal(c.id, c.name, c.role, c.locale, c.display_currency) if c else None
        with self._lock:
            self._cache[h] = (now, p)
        return p

    def invalidate(self) -> None:
        """Drop cached lookups so a revoked key stops working (and changed preferences apply) on this replica
        immediately; other replicas within the cache TTL."""
        with self._lock:
            self._cache.clear()


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
