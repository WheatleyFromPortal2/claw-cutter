"""LangSearch API wrapper. Set LANGSEARCH_API_KEY in .env to enable real web search."""

import logging
import os

import httpx

_SEARCH_URL = "https://api.langsearch.com/v1/web-search"
logger = logging.getLogger(__name__)


def _api_key() -> str:
    return os.getenv("LANGSEARCH_API_KEY", "")


async def web_search(query: str, count: int = 5) -> list[dict]:
    """Return up to `count` results as [{title, url, snippet}]. Empty list if no key."""
    key = _api_key()
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                _SEARCH_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={"query": query, "count": count, "summary": False},
            )
            if not resp.is_success:
                logger.warning("LangSearch %s → HTTP %d: %s", query[:60], resp.status_code, resp.text[:200])
                return []
            data = resp.json()
            results = []
            for r in data.get("data", {}).get("webPages", {}).get("value", []):
                results.append({
                    "title": r.get("name", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("snippet", ""),
                })
            logger.info("LangSearch %r → %d results", query[:60], len(results))
            return results
    except Exception as exc:
        logger.warning("LangSearch error for %r: %s", query[:60], exc)
        return []


def search_enabled() -> bool:
    return bool(_api_key())
