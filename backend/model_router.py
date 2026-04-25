"""
Multi-provider model router with hot-reload and preference-ordered fallback.

models.json schema per entry:
  id            string   — unique identifier used throughout the app
  name          string   — display name shown in the UI
  provider      string   — "openai_compat" | "anthropic"
  base_url      string   — root URL for openai_compat (e.g. http://host/v1)
  api_key       string   — bearer token / API key (kept only in models.json)
  model         string   — model name sent in the request body
  enabled       bool     — if false the model is never tried
  preference    int      — lower number = tried first; ties resolved by order in file
  timeout_secs  float    — seconds before giving up and trying the next model
  max_tokens    int      — default token budget (callers may override per-call)
  extra_headers object   — additional HTTP headers merged into every request
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

MODELS_FILE = Path(__file__).parent / "models.json"
_POLL_INTERVAL = 2.0  # seconds between mtime checks


@dataclass
class ModelConfig:
    id: str
    name: str
    provider: str
    model: str
    api_key: str
    base_url: str = ""
    enabled: bool = True
    preference: int = 99
    timeout_secs: float = 60.0
    max_tokens: int = 2048
    extra_headers: dict = field(default_factory=dict)


def _parse_models(path: Path) -> list[ModelConfig]:
    with open(path) as f:
        data = json.load(f)
    configs = []
    for m in data.get("models", []):
        configs.append(
            ModelConfig(
                id=m["id"],
                name=m.get("name", m["id"]),
                provider=m["provider"],
                model=m["model"],
                api_key=m.get("api_key", ""),
                base_url=m.get("base_url", "").rstrip("/"),
                enabled=m.get("enabled", True),
                preference=m.get("preference", 99),
                timeout_secs=float(m.get("timeout_secs", 60)),
                max_tokens=int(m.get("max_tokens", 2048)),
                extra_headers=m.get("extra_headers", {}),
            )
        )
    return configs


class ModelRouter:
    def __init__(self) -> None:
        self._models: list[ModelConfig] = []
        self._mtime: float = 0.0
        self._reload()

    def _reload(self) -> None:
        try:
            configs = _parse_models(MODELS_FILE)
            self._mtime = MODELS_FILE.stat().st_mtime
            self._models = configs
            enabled = [m for m in configs if m.enabled]
            logger.info(
                "models.json loaded: %d total, %d enabled", len(configs), len(enabled)
            )
        except FileNotFoundError:
            logger.warning("models.json not found at %s — no models available", MODELS_FILE)
        except Exception as exc:
            logger.error("Failed to load models.json: %s", exc)

    async def _watch_loop(self) -> None:
        while True:
            await asyncio.sleep(_POLL_INTERVAL)
            try:
                mtime = MODELS_FILE.stat().st_mtime
                if mtime != self._mtime:
                    self._reload()
            except FileNotFoundError:
                pass
            except Exception as exc:
                logger.error("Error polling models.json: %s", exc)

    def start_watching(self) -> None:
        asyncio.create_task(self._watch_loop())

    def active_models(self) -> list[ModelConfig]:
        return sorted(
            (m for m in self._models if m.enabled),
            key=lambda m: m.preference,
        )

    def all_models(self) -> list[ModelConfig]:
        return sorted(self._models, key=lambda m: m.preference)

    # ── Provider implementations ──────────────────────────────────────────────

    async def _call_openai_compat(
        self,
        cfg: ModelConfig,
        system: str,
        user_msg: str,
        max_tokens: int,
    ) -> str:
        url = f"{cfg.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
            **cfg.extra_headers,
        }
        payload = {
            "model": cfg.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        }
        async with httpx.AsyncClient(timeout=cfg.timeout_secs) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def _call_anthropic(
        self,
        cfg: ModelConfig,
        system: str,
        user_msg: str,
        max_tokens: int,
    ) -> str:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
            **cfg.extra_headers,
        }
        payload = {
            "model": cfg.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_msg}],
        }
        async with httpx.AsyncClient(timeout=cfg.timeout_secs) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"]

    async def _dispatch(
        self,
        cfg: ModelConfig,
        system: str,
        user_msg: str,
        max_tokens: int,
    ) -> str:
        if cfg.provider == "openai_compat":
            return await self._call_openai_compat(cfg, system, user_msg, max_tokens)
        if cfg.provider == "anthropic":
            return await self._call_anthropic(cfg, system, user_msg, max_tokens)
        raise ValueError(f"Unknown provider '{cfg.provider}' for model '{cfg.id}'")

    # ── Public call interface ─────────────────────────────────────────────────

    async def call(
        self,
        system: str,
        user_msg: str,
        max_tokens: Optional[int] = None,
    ) -> tuple[str, str]:
        """
        Try active models in preference order.

        Returns (response_text, model_id_used).
        Falls back to the next model on timeout or any HTTP/parse error.
        Raises RuntimeError if every model fails.
        """
        models = self.active_models()
        if not models:
            raise RuntimeError("No models are enabled in models.json")

        last_error: str = "no models tried"
        for cfg in models:
            effective_tokens = max_tokens if max_tokens is not None else cfg.max_tokens
            try:
                logger.debug("Trying model %s (preference=%d)", cfg.id, cfg.preference)
                text = await asyncio.wait_for(
                    self._dispatch(cfg, system, user_msg, effective_tokens),
                    timeout=cfg.timeout_secs,
                )
                return text, cfg.id
            except asyncio.TimeoutError:
                last_error = f"{cfg.id}: timed out after {cfg.timeout_secs}s"
                logger.warning(last_error)
            except httpx.TimeoutException:
                last_error = f"{cfg.id}: HTTP timeout"
                logger.warning(last_error)
            except httpx.HTTPStatusError as exc:
                last_error = f"{cfg.id}: HTTP {exc.response.status_code}"
                logger.warning("%s — response: %s", last_error, exc.response.text[:200])
            except Exception as exc:
                last_error = f"{cfg.id}: {exc}"
                logger.warning("Model %s error: %s", cfg.id, exc)

        raise RuntimeError(f"All models failed. Last error: {last_error}")


# Module-level singleton — imported by ai.py and main.py
router = ModelRouter()
