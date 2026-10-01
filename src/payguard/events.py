"""Event bus + outbox relay.

Topics:
  decision.made   every scored transaction  -> monitoring / analytics sinks
  case.created    review/decline outcomes   -> investigation worker (agent)
  label.recorded  analyst / chargeback labels -> retraining dataset sink

Redis Streams was chosen over Kafka for this scale: consumer groups give at-least-once delivery,
per-group offsets, and pending-entry reclaim for crashed consumers, with one piece of infra that is
already required for the online feature store. The EventBus interface is the seam where a Kafka
adapter would go when retention/replay at high throughput is needed (see docs/DECISIONS.md).
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from payguard.db.models import OutboxEvent
from payguard.db.session import write_guard

log = logging.getLogger(__name__)
Handler = Callable[[str, dict], None]


class EventBus(Protocol):
    def publish(self, topic: str, key: str, payload: dict) -> None: ...

    def poll(self, topic: str, group: str, consumer: str, handler: Handler, count: int = 50, block_ms: int = 1000) -> int:
        """Deliver up to `count` events to handler; ack each only after the handler returns."""


class InMemoryBus:
    def __init__(self):
        self._log: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        self._offsets: dict[tuple[str, str], int] = defaultdict(int)
        self._cv = threading.Condition()

    def publish(self, topic, key, payload):
        with self._cv:
            self._log[topic].append((key, payload))
            self._cv.notify_all()

    def poll(self, topic, group, consumer, handler, count=50, block_ms=1000):
        with self._cv:
            off = self._offsets[(topic, group)]
            if off >= len(self._log[topic]):
                self._cv.wait(timeout=block_ms / 1000)
            batch = self._log[topic][off:off + count]
        done = 0
        for key, payload in batch:
            handler(key, payload)  # raise => offset not advanced => redelivered
            with self._cv:
                self._offsets[(topic, group)] += 1
            done += 1
        return done

    def size(self, topic: str) -> int:
        return len(self._log[topic])


class RedisStreamsBus:
    def __init__(self, client, maxlen: int = 1_000_000, reclaim_idle_ms: int = 60_000):
        self.r = client
        self.maxlen = maxlen
        self.reclaim_idle_ms = reclaim_idle_ms
        self._groups: set[tuple[str, str]] = set()

    @staticmethod
    def _stream(topic: str) -> str:
        return f"pg:ev:{topic}"

    def _ensure_group(self, topic, group):
        import redis

        if (topic, group) in self._groups:
            return
        try:
            self.r.xgroup_create(self._stream(topic), group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        self._groups.add((topic, group))

    def publish(self, topic, key, payload):
        self.r.xadd(self._stream(topic), {"key": key, "payload": json.dumps(payload)}, maxlen=self.maxlen, approximate=True)

    def poll(self, topic, group, consumer, handler, count=50, block_ms=1000):
        self._ensure_group(topic, group)
        stream = self._stream(topic)
        # First reclaim messages a crashed consumer left un-acked, then read new ones.
        _, claimed, *_ = self.r.xautoclaim(stream, group, consumer, min_idle_time=self.reclaim_idle_ms, count=count)
        entries = list(claimed)
        if not entries:
            resp = self.r.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)
            entries = resp[0][1] if resp else []
        done = 0
        for msg_id, fields in entries:
            f = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                 for k, v in fields.items()}
            handler(f["key"], json.loads(f["payload"]))
            self.r.xack(stream, group, msg_id)
            done += 1
        return done


def enqueue(session, topic: str, key: str, payload: dict) -> None:
    """Add an event to the outbox inside the caller's DB transaction."""
    session.add(OutboxEvent(topic=topic, key=key, payload=payload))


def relay_outbox(session_factory: sessionmaker, bus: EventBus, batch: int = 500) -> int:
    """Publish unpublished outbox rows in id order. A crash after publish but before commit re-sends
    the batch: delivery is at-least-once and every consumer is idempotent."""
    with session_factory() as s:
        pending = s.scalar(select(OutboxEvent.id).where(OutboxEvent.published_at.is_(None)).limit(1))
    if pending is None:  # cheap read; don't take the write path when there is nothing to do
        return 0
    with write_guard(session_factory), session_factory() as s:
        rows = s.scalars(select(OutboxEvent).where(OutboxEvent.published_at.is_(None))
                         .order_by(OutboxEvent.id).limit(batch)).all()
        now = datetime.now(timezone.utc)
        for row in rows:
            bus.publish(row.topic, row.key, row.payload)
            row.published_at = now
        s.commit()
        return len(rows)


def make_bus(redis_url: str | None) -> EventBus:
    if not redis_url:
        return InMemoryBus()
    import redis

    return RedisStreamsBus(redis.Redis.from_url(redis_url))
