"""Local, free, best-effort Redis cache for review_backend/'s two genuinely
expensive, rarely-changing sub-computations (_cash_position_stats(),
reconciliation_statement() in main.py) -- NEVER a hard dependency the way
Postgres now is. Redis holds nothing of real value (a disposable,
recomputable cache, not case/review data), so every operation here degrades
to "just compute it directly, slightly slower" on any Redis error, matching
_cash_position_stats()'s own existing try/except/[WARN]/degrade discipline.
See redis/docker-compose.yaml for the dedicated local container this talks
to -- never shared with Airflow's own internal Redis.

REVIEW_QUEUE_REDIS_URL mirrors db.py's REVIEW_QUEUE_DATABASE_URL naming and
override pattern.
"""

import json
import os
from typing import Callable, TypeVar

import redis
from fastapi.encoders import jsonable_encoder

T = TypeVar("T")

REDIS_URL = os.environ.get("REVIEW_QUEUE_REDIS_URL", "redis://localhost:6379/0")

# Created once at import time, unlike db.py's per-call psycopg connect/close
# -- redis-py's client owns an internal connection pool and reconnects
# lazily/automatically, so there's no equivalent reason to open/close per
# call (Postgres connections are comparatively heavy; Redis's aren't).
# Short explicit timeouts so a genuinely HUNG Redis (not just a plain
# "connection refused" from one that's simply not running, which fails
# instantly regardless) can never turn a fast endpoint into a slow one.
_client = redis.Redis.from_url(
    REDIS_URL,
    socket_connect_timeout=0.2,
    socket_timeout=0.2,
    decode_responses=True,
)


def cash_position_stats_key(data_dir: str, as_of_iso: str) -> str:
    """Single source of truth for this key's format -- both main.py (reads)
    and run_stream_simulator.py (invalidates) call this instead of each
    hand-formatting their own copy of the string, so the two can never
    silently drift apart (a mismatch would make invalidation stop matching
    anything, with no error and no visible symptom)."""
    return f"cash_position_stats:v1:{data_dir}:{as_of_iso}"


def reconciliation_statement_key(data_dir: str, as_of_iso: str) -> str:
    return f"reconciliation_statement:v1:{data_dir}:{as_of_iso}"


def cached_or_compute(key: str, ttl_seconds: int, compute_fn: Callable[[], T]) -> T:
    """Try a Redis GET on `key`; deserialize and return on a hit. On a miss,
    OR on ANY Redis error (down, timeout, whatever), call compute_fn()
    directly. Only if compute_fn() returns successfully is a best-effort
    Redis SET attempted (also swallowing any error) before returning.

    compute_fn() itself is NOT wrapped here -- if it raises, that propagates
    to the caller completely unchanged, cache or no cache. This matters for
    reconciliation_statement(), which translates its own exceptions into an
    HTTP 503: that behavior stays byte-for-byte identical whether Redis is
    up, down, or never started.

    Caching a `None` result (e.g. _cash_position_stats()'s own "data not
    ready yet" case) is deliberate, not a bug -- json.dumps(None) == "null",
    a real cached value, distinguishable from client.get() returning Python
    None for "key doesn't exist at all". Skipping the recompute for a
    persistently-broken CASH_POSITION_DATA_DIR until the TTL (or an
    explicit invalidation) elapses is the correct behavior, not a hidden
    failure to retry.
    """
    try:
        cached = _client.get(key)
        if cached is not None:
            print(f"[CACHE] HIT  {key}")
            return json.loads(cached)
        print(f"[CACHE] MISS {key}")
    except Exception as e:  # noqa: BLE001 -- Redis is optional, never fatal
        print(f"[WARN] Redis GET failed for {key!r}: {type(e).__name__}: {e}")

    result = compute_fn()

    try:
        # jsonable_encoder (the same conversion FastAPI already applies to
        # every response body, which is why callers never needed to worry
        # about this before caching existed) handles dates, numpy scalars,
        # etc. that plain json.dumps() chokes on -- confirmed for real:
        # reconciliation_statement()'s orphan_rows carries a raw
        # datetime.date (credit_date) straight out of a DataFrame, and
        # json.dumps() raised on it during initial testing. Encoding here
        # rather than switching to `default=str` matters: `default=str`
        # would silently turn a cache-HIT's floats/dates into strings that
        # differ in shape from a cache-MISS's native response types.
        _client.set(key, json.dumps(jsonable_encoder(result)), ex=ttl_seconds)
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] Redis SET failed for {key!r}: {type(e).__name__}: {e}")

    return result


def invalidate(key: str) -> None:
    """Best-effort DELETE, swallowing any Redis error -- used by
    run_stream_simulator.py's tick loop to proactively evict its own
    cache entries right after each atomic snapshot write, so a poll
    shortly after a tick sees fresh data instead of waiting out the TTL
    safety net."""
    try:
        _client.delete(key)
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] Redis DELETE failed for {key!r}: {type(e).__name__}: {e}")
