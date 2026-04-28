"""
Lightweight in-process metrics: per-model token rate tracking and recent-user tracking.
Thread/async safe via simple list appends (GIL-protected in CPython).
"""

import time
from collections import deque, defaultdict
from datetime import datetime, timezone

# Rolling window length for tokens/s calculation
_WINDOW_SECS = 60.0

# How long (seconds) before a token is considered "inactive"
_USER_IDLE_SECS = 300

_app_start: datetime = datetime.now(timezone.utc)

# deque of (timestamp, model_id, token_count) tuples
_token_events: deque = deque()

# token_hash -> last_seen timestamp
_user_last_seen: dict[str, float] = {}


def record_tokens(model_id: str, count: int) -> None:
    _token_events.append((time.monotonic(), model_id, count))


def record_user(token: str) -> None:
    _user_last_seen[hash(token)] = time.monotonic()


def get_uptime_secs() -> float:
    return (datetime.now(timezone.utc) - _app_start).total_seconds()


def get_current_user_count() -> int:
    now = time.monotonic()
    return sum(1 for t in _user_last_seen.values() if now - t < _USER_IDLE_SECS)


def get_tokens_per_sec() -> dict[str, float]:
    """Return per-model tokens/s averaged over the last 60 seconds."""
    now = time.monotonic()
    cutoff = now - _WINDOW_SECS

    # Evict old events
    while _token_events and _token_events[0][0] < cutoff:
        _token_events.popleft()

    totals: dict[str, int] = defaultdict(int)
    for ts, model_id, count in _token_events:
        totals[model_id] += count

    # Actual window is min(elapsed, WINDOW_SECS)
    elapsed = min(get_uptime_secs(), _WINDOW_SECS)
    if elapsed <= 0:
        return {}

    return {mid: total / elapsed for mid, total in totals.items()}
