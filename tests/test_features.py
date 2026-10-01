import math
import random

import fakeredis
import pytest

from payguard.features import state as st
from payguard.features.pipeline import FeaturePipeline
from payguard.features.store import InMemoryFeatureStore, RedisFeatureStore


def _close(a: dict, b: dict) -> bool:
    for k in a:
        x, y = a[k], b[k]
        if isinstance(x, dict):
            if not _close(x, y):
                return False
        elif isinstance(x, list):
            if sorted(x) != sorted(y):
                return False
        elif isinstance(x, float) and not math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-9):
            return False
        elif not isinstance(x, float) and x != y:
            return False
    return True


def test_fold_is_order_independent():
    events = [(1000.0 + 37 * i, 10.0 + i) for i in range(50)]
    in_order = st.new_state()
    for ts, amt in events:
        in_order = st.fold_event(in_order, ts, amt, {})
    shuffled = events[:]
    random.Random(0).shuffle(shuffled)
    out_of_order = st.new_state()
    for ts, amt in shuffled:
        out_of_order = st.fold_event(out_of_order, ts, amt, {})
    assert _close(in_order, out_of_order)


def test_decayed_count_halves_after_one_half_life():
    s = st.fold_event(st.new_state(), 0.0, 10.0, {})
    f = st.read_features(s, 3600.0, 10.0)
    assert math.isclose(f["cnt_1h"], 0.5, rel_tol=1e-9)
    assert math.isclose(f["secs_since_last"], 3600.0)


def test_features_exclude_current_event(txn_factory):
    pipe = FeaturePipeline(InMemoryFeatureStore())
    first, _ = pipe.build(txn_factory(1))
    assert first["customer_is_new"] == 1.0 and first["customer_cnt_1h"] == 0.0
    second, _ = pipe.build(txn_factory(2, minutes=1))
    assert second["customer_is_new"] == 0.0
    assert math.isclose(second["customer_cnt_1h"], 2 ** (-1 / 60))


@pytest.mark.parametrize("backend", ["memory", "redis"])
def test_retry_is_idempotent(backend, txn_factory):
    store = InMemoryFeatureStore() if backend == "memory" else RedisFeatureStore(fakeredis.FakeRedis())
    pipe = FeaturePipeline(store)
    a, replay_a = pipe.build(txn_factory(1))
    b, replay_b = pipe.build(txn_factory(1))  # same transaction id retried
    c, _ = pipe.build(txn_factory(2, minutes=1))
    assert not replay_a and replay_b
    assert a == b or all((x == y) or (x != x and y != y) for x, y in zip(a.values(), b.values()))
    assert math.isclose(c["customer_cnt_1h"], 2 ** (-1 / 60))  # the retry did not double count


def test_redis_and_memory_backends_agree(txn_factory):
    mem, red = FeaturePipeline(InMemoryFeatureStore()), FeaturePipeline(RedisFeatureStore(fakeredis.FakeRedis()))
    rng = random.Random(1)
    for i in range(200):
        t = txn_factory(i, minutes=i * rng.uniform(0.1, 30), amount=rng.uniform(1, 500),
                        card=rng.choice(["1", "2", "3"]), device=rng.choice(["a", "b"]))
        fm, _ = mem.build(t)
        fr, _ = red.build(t)
        for k in fm:
            x, y = fm[k], fr[k]
            if isinstance(x, float) and math.isnan(x):
                assert isinstance(y, float) and math.isnan(y), k
            else:
                assert x == y, k


def test_device_sharing_counts_distinct_customers(txn_factory):
    pipe = FeaturePipeline(InMemoryFeatureStore())
    for i, card in enumerate(["1", "2", "3", "4"]):
        f, _ = pipe.build(txn_factory(i, minutes=i, card=card, device="farm-phone"))
    assert f["device_n_distinct_customer"] == 3.0  # three other customers seen on this device before


def test_features_are_stationary_age_capped_and_distinct_windowed():
    day = 86400.0
    s = st.new_state()
    s = st.fold_event(s, 0.0, 10.0, {"device": "old-phone"})
    for i in range(1, 5):
        s = st.fold_event(s, 40 * day + i, 10.0, {"device": f"new-{i}"})
    f = st.read_features(s, 45 * day, 10.0)
    assert f["age_days"] == st.AGE_CAP_DAYS  # not 45: capped, so it cannot grow with the calendar
    assert st.distinct_recent(s, "device", 45 * day) == 4.0  # the device from 45 days ago has aged out


def test_distinct_cap_keeps_most_recent_regardless_of_order():
    events = [(float(i), f"k{i}") for i in range(80)]
    a, b = st.new_state(), st.new_state()
    for ts, k in events:
        a = st.fold_event(a, ts, 1.0, {"device": k})
    for ts, k in reversed(events):
        b = st.fold_event(b, ts, 1.0, {"device": k})
    assert a["d"]["device"] == b["d"]["device"] and len(a["d"]["device"]) == st.DISTINCT_CAP
    assert min(a["d"]["device"].values()) == 80 - st.DISTINCT_CAP


def test_browser_family_is_stable_across_source_format_change():
    from payguard.features.pipeline import _browser_family

    # April and May of the source encode the same browsers differently
    assert _browser_family("mobile safari generic") == _browser_family("mobile safari 11.0") == "mobile safari"
    assert _browser_family("chrome generic for android") == _browser_family("chrome 65.0 for android") == "chrome"
