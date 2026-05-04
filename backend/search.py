"""LangSearch API wrapper with tier-aware rate limiting.

Environment variables:
  LANGSEARCH_API_KEY  — enables search (required)
  LANGSEARCH_TIER     — "free" (default), "tier1", "tier2", or "tier3"
"""

import asyncio
import logging
import os
import time
from collections import deque

import httpx

_SEARCH_URL = "https://api.langsearch.com/v1/web-search"
logger = logging.getLogger(__name__)

TIER_LIMITS: dict[str, dict] = {
    "free":  {"qps": 1,   "qpm": 60,    "qpd": 1_000},
    "tier1": {"qps": 5,   "qpm": 200,   "qpd": 2_000},
    "tier2": {"qps": 10,  "qpm": 500,   "qpd": 10_000},
    "tier3": {"qps": 30,  "qpm": 2_000, "qpd": 100_000},
}


class _SearchRateLimiter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # Rolling timestamp windows
        self._sec_ts: deque[float] = deque()   # last 1 s
        self._min_ts: deque[float] = deque()   # last 60 s
        self._day_ts: deque[float] = deque()   # last 24 h
        # External block set from a 429 response
        self._blocked_until: float = 0.0
        self._blocked_reason: str = ""
        # Last observed rate-limit event (never cleared, shown on status page)
        self._last_rl_event: dict | None = None

    # ── internal helpers ──────────────────────────────────────────────────────

    def _tier(self) -> str:
        raw = os.getenv("LANGSEARCH_TIER", "free").lower().replace("-", "").replace("_", "")
        return raw if raw in TIER_LIMITS else "free"

    def _lim(self) -> dict:
        return TIER_LIMITS[self._tier()]

    def _evict(self, now: float) -> None:
        while self._sec_ts and self._sec_ts[0] < now - 1.0:
            self._sec_ts.popleft()
        while self._min_ts and self._min_ts[0] < now - 60.0:
            self._min_ts.popleft()
        while self._day_ts and self._day_ts[0] < now - 86_400.0:
            self._day_ts.popleft()

    # ── public API ────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Snapshot of current usage — safe to call from any context."""
        now = time.monotonic()
        self._evict(now)
        lim = self._lim()
        blocked_secs = max(0.0, self._blocked_until - now) if self._blocked_until else 0.0
        return {
            "enabled": bool(os.getenv("LANGSEARCH_API_KEY", "")),
            "tier": self._tier(),
            "limits": lim,
            "usage": {
                "qps": len(self._sec_ts),
                "qpm": len(self._min_ts),
                "qpd": len(self._day_ts),
            },
            "blocked": blocked_secs > 0,
            "blocked_reason": self._blocked_reason if blocked_secs > 0 else "",
            "blocked_expires_in_secs": round(blocked_secs, 1) if blocked_secs > 0 else None,
            "last_rate_limit_event": self._last_rl_event,
        }

    async def acquire(self) -> None:
        """Throttle until a request slot is available, then claim it.

        Serialises all callers via an asyncio.Lock so requests are sequential.
        Also drains any 429-penalty before checking windows.
        """
        async with self._lock:
            now = time.monotonic()
            if self._blocked_until > now:
                wait = self._blocked_until - now
                logger.warning(
                    "LangSearch blocked (%.1fs remaining: %s)", wait, self._blocked_reason
                )
                await asyncio.sleep(wait)

            lim = self._lim()
            while True:
                now = time.monotonic()
                self._evict(now)

                if len(self._sec_ts) >= lim["qps"]:
                    sleep = max(0.01, self._sec_ts[0] + 1.0 - now + 0.02)
                    logger.debug("LangSearch QPS throttle — sleeping %.2fs", sleep)
                    await asyncio.sleep(sleep)
                    continue
                if len(self._min_ts) >= lim["qpm"]:
                    sleep = max(0.1, self._min_ts[0] + 60.0 - now + 0.1)
                    logger.info("LangSearch QPM throttle — sleeping %.1fs", sleep)
                    await asyncio.sleep(sleep)
                    continue
                if len(self._day_ts) >= lim["qpd"]:
                    sleep = max(1.0, self._day_ts[0] + 86_400.0 - now + 1.0)
                    logger.warning(
                        "LangSearch QPD throttle — sleeping %.0fs (%.1f hrs)",
                        sleep, sleep / 3600,
                    )
                    await asyncio.sleep(sleep)
                    continue
                break

            now = time.monotonic()
            self._sec_ts.append(now)
            self._min_ts.append(now)
            self._day_ts.append(now)

    def record_429(self, resp: httpx.Response) -> float:
        """Parse a 429 response, set the block, and return seconds to wait."""
        now = time.monotonic()

        # Try response headers first
        for header in ("Retry-After", "retry-after"):
            val = resp.headers.get(header)
            if val:
                try:
                    wait = float(val)
                    self._blocked_until = now + wait
                    self._blocked_reason = f"429 (Retry-After: {wait:.0f}s)"
                    self._last_rl_event = {
                        "at_monotonic": now,
                        "reason": self._blocked_reason,
                        "waited_secs": wait,
                    }
                    return wait
                except ValueError:
                    pass

        for header in ("X-RateLimit-Reset", "x-ratelimit-reset"):
            val = resp.headers.get(header)
            if val:
                try:
                    n = float(val)
                    if n > 1_000_000_000:
                        n = max(0.0, n - time.time())  # Unix ts → seconds from now
                    if n > 0:
                        self._blocked_until = now + n
                        self._blocked_reason = f"429 (X-RateLimit-Reset: {n:.0f}s)"
                        self._last_rl_event = {
                            "at_monotonic": now,
                            "reason": self._blocked_reason,
                            "waited_secs": n,
                        }
                        return n
                except ValueError:
                    pass

        # Infer from which window is most saturated
        lim = self._lim()
        if len(self._sec_ts) >= lim["qps"]:
            wait, reason = 2.0, "QPS limit hit"
        elif len(self._min_ts) >= lim["qpm"] * 0.8:
            wait, reason = 62.0, "QPM limit hit"
        else:
            wait, reason = 86_402.0, "QPD limit hit"

        self._blocked_until = now + wait
        self._blocked_reason = reason
        self._last_rl_event = {
            "at_monotonic": now,
            "reason": reason,
            "waited_secs": wait,
        }
        return wait


_limiter = _SearchRateLimiter()


def get_search_stats() -> dict:
    """Return current rate-limit stats for the status page and API."""
    return _limiter.stats()


def search_enabled() -> bool:
    return bool(os.getenv("LANGSEARCH_API_KEY", ""))


async def web_search(query: str, count: int = 50) -> list[dict]:
    """Return up to `count` results as [{title, url, snippet}].

    Automatically throttles to stay within the configured tier limits.
    Retries once if a 429 is received (after waiting the penalty period).
    """
    key = os.getenv("LANGSEARCH_API_KEY", "")
    if not key:
        return []

    for attempt in range(2):
        await _limiter.acquire()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    _SEARCH_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json={"query": query, "count": count, "summary": False},
                )

                if resp.status_code == 429:
                    wait = _limiter.record_429(resp)
                    logger.warning(
                        "LangSearch 429 for %r — %s, waiting %.1fs before retry",
                        query[:60], _limiter._blocked_reason, wait,
                    )
                    if attempt == 0:
                        await asyncio.sleep(wait)
                        continue
                    return []

                if not resp.is_success:
                    logger.warning(
                        "LangSearch %r → HTTP %d: %s",
                        query[:60], resp.status_code, resp.text[:200],
                    )
                    return []

                data = resp.json()
                results = [
                    {
                        "title": r.get("name", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("snippet", ""),
                    }
                    for r in data.get("data", {}).get("webPages", {}).get("value", [])
                ]
                logger.info("LangSearch %r → %d results", query[:60], len(results))
                return results

        except Exception as exc:
            logger.warning("LangSearch error for %r: %s", query[:60], exc)
            return []

    return []
