"""
Multi-provider model router with hot-reload, preference-ordered fallback,
and per-model concurrency limiting.

models.json schema per entry:
  id              string   — unique identifier used throughout the app
  name            string   — display name shown in the UI
  provider        string   — "openai_compat" | "anthropic"
  base_url        string   — root URL for openai_compat (e.g. http://host/v1)
  api_key         string   — bearer token / API key (kept only in models.json)
  model           string   — model name sent in the request body
  enabled         bool     — if false the model is never tried
  preference      int      — lower number = tried first; ties resolved by order in file
  timeout_secs    float    — seconds before giving up and trying the next model
  max_tokens      int      — default token budget (callers may override per-call)
  max_concurrent  int      — max simultaneous calls to this model (default 1)
  extra_headers   object   — additional HTTP headers merged into every request
"""

import asyncio
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

MODELS_FILE = Path(__file__).parent / "models.json"
_POLL_INTERVAL = 2.0  # seconds between mtime checks
_CLI_HAIKU_ID = "claude_cli_haiku"
_CLI_HAIKU_MODEL = "claude-haiku-4-5-20251001"


def _make_cli_haiku() -> Optional["ModelConfig"]:
    """Return a ModelConfig for Haiku via the claude CLI, or None if the CLI is not in PATH."""
    if not shutil.which("claude"):
        return None
    return ModelConfig(
        id=_CLI_HAIKU_ID,
        name="Claude Haiku (claude CLI)",
        provider="claude_cli",
        model=_CLI_HAIKU_MODEL,
        api_key="",
        enabled=True,
        preference=50,
        timeout_secs=60.0,
        max_tokens=2048,
        max_concurrent=1,
    )


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
    max_concurrent: int = 1
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
                max_concurrent=int(m.get("max_concurrent", 1)),
                extra_headers=m.get("extra_headers", {}),
            )
        )
    return configs


class ModelRouter:
    def __init__(self) -> None:
        self._models: list[ModelConfig] = []
        self._mtime: float = 0.0
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._semaphore_maxes: dict[str, int] = {}
        self._reload()

    def _update_semaphores(self, configs: list[ModelConfig]) -> None:
        new_sems: dict[str, asyncio.Semaphore] = {}
        new_maxes: dict[str, int] = {}
        for cfg in configs:
            new_maxes[cfg.id] = cfg.max_concurrent
            if (
                cfg.id in self._semaphores
                and self._semaphore_maxes.get(cfg.id) == cfg.max_concurrent
            ):
                new_sems[cfg.id] = self._semaphores[cfg.id]  # reuse — preserves in-flight count
            else:
                new_sems[cfg.id] = asyncio.Semaphore(cfg.max_concurrent)
                if cfg.id in self._semaphores:
                    logger.info(
                        "Semaphore for %s recreated (max_concurrent changed to %d)",
                        cfg.id, cfg.max_concurrent,
                    )
        self._semaphores = new_sems
        self._semaphore_maxes = new_maxes

    def _reload(self) -> None:
        try:
            file_configs = _parse_models(MODELS_FILE)
            self._mtime = MODELS_FILE.stat().st_mtime
        except FileNotFoundError:
            file_configs = []
            logger.warning("models.json not found at %s", MODELS_FILE)
        except Exception as exc:
            logger.error("Failed to load models.json: %s", exc)
            return

        configs = list(file_configs)
        cli_model = _make_cli_haiku()
        if cli_model and not any(m.id == cli_model.id for m in configs):
            configs.append(cli_model)

        self._models = configs
        self._update_semaphores(configs)
        enabled = [m for m in configs if m.enabled]
        logger.info("models reloaded: %d total, %d enabled", len(configs), len(enabled))

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
    ) -> tuple[str, dict]:
        url = f"{cfg.base_url}/chat/completions"
        if cfg.api_key:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg.api_key}",
                **cfg.extra_headers,
            }
        else:
            headers = {
                "Content-Type": "application/json",
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
            usage = data.get("usage", {})
            tokens = {
                "input": usage.get("prompt_tokens", 0),
                "output": usage.get("completion_tokens", 0),
            }
            return data["choices"][0]["message"]["content"], tokens

    async def _call_anthropic(
        self,
        cfg: ModelConfig,
        system: str,
        user_msg: str,
        max_tokens: int,
    ) -> tuple[str, dict]:
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
            usage = data.get("usage", {})
            tokens = {
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
            }
            return data["content"][0]["text"], tokens

    async def _call_claude_cli(
        self,
        cfg: ModelConfig,
        system: str,
        user_msg: str,
        max_tokens: int,
    ) -> tuple[str, dict]:
        claude_bin = shutil.which("claude")
        if not claude_bin:
            raise RuntimeError("claude CLI not found in PATH")

        def _run() -> subprocess.CompletedProcess:
            return subprocess.run(
                [
                    claude_bin, "-p",
                    "--system-prompt", system,
                    "--model", cfg.model,
                    "--output-format", "text",
                    user_msg,
                ],
                capture_output=True,
                text=True,
                timeout=cfg.timeout_secs,
            )

        result = await asyncio.to_thread(_run)
        if result.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited {result.returncode}: {result.stderr[:200]}"
            )
        return result.stdout, {"input": 0, "output": 0}

    async def _dispatch(
        self,
        cfg: ModelConfig,
        system: str,
        user_msg: str,
        max_tokens: int,
    ) -> tuple[str, dict]:
        if cfg.provider == "openai_compat":
            return await self._call_openai_compat(cfg, system, user_msg, max_tokens)
        if cfg.provider == "anthropic":
            return await self._call_anthropic(cfg, system, user_msg, max_tokens)
        if cfg.provider == "claude_cli":
            return await self._call_claude_cli(cfg, system, user_msg, max_tokens)
        raise ValueError(f"Unknown provider '{cfg.provider}' for model '{cfg.id}'")

    # ── Public call interface ─────────────────────────────────────────────────

    async def call(
        self,
        system: str,
        user_msg: str,
        max_tokens: Optional[int] = None,
    ) -> tuple[str, str, dict]:
        """
        Try active models in preference order, respecting per-model concurrency limits.

        - If a model is at capacity (all slots occupied) it is skipped in favour of
          the next model in preference order.
        - If every model is at capacity the call waits until a slot opens up, then
          retries from the top of the preference list.
        - If a model returns an error it is skipped for the lifetime of this call.

        Returns (response_text, model_id_used, tokens_dict).
        Raises RuntimeError if every model errors out.
        """
        models = self.active_models()
        if not models:
            raise RuntimeError("No models are enabled in models.json")

        errored: set[str] = set()
        last_error = "no models tried"

        while True:
            available = [m for m in models if m.id not in errored]
            if not available:
                raise RuntimeError(f"All models failed. Last error: {last_error}")

            any_at_capacity = False

            for cfg in available:
                sem = self._semaphores.get(cfg.id)
                if sem is None:
                    errored.add(cfg.id)
                    continue

                if sem.locked():
                    any_at_capacity = True
                    continue  # model busy — fall through to next

                async with sem:
                    effective = max_tokens if max_tokens is not None else cfg.max_tokens
                    logger.debug(
                        "Calling model %s (preference=%d)", cfg.id, cfg.preference
                    )
                    try:
                        text, tokens = await asyncio.wait_for(
                            self._dispatch(cfg, system, user_msg, effective),
                            timeout=cfg.timeout_secs,
                        )
                        return text, cfg.id, tokens
                    except asyncio.TimeoutError:
                        last_error = f"{cfg.id}: timed out after {cfg.timeout_secs}s"
                        logger.warning(last_error)
                        errored.add(cfg.id)
                    except httpx.TimeoutException:
                        last_error = f"{cfg.id}: HTTP timeout"
                        logger.warning(last_error)
                        errored.add(cfg.id)
                    except httpx.HTTPStatusError as exc:
                        last_error = f"{cfg.id}: HTTP {exc.response.status_code}"
                        logger.warning("%s — response: %s", last_error, exc.response.text[:200])
                        errored.add(cfg.id)
                    except Exception as exc:
                        last_error = f"{cfg.id}: {exc}"
                        logger.warning("Model %s error: %s", cfg.id, exc)
                        errored.add(cfg.id)

            # Re-check after marking errors this pass
            available = [m for m in models if m.id not in errored]
            if not available:
                raise RuntimeError(f"All models failed. Last error: {last_error}")

            if not any_at_capacity:
                # All remaining models had semaphores but we still didn't succeed —
                # shouldn't happen, but raise rather than spin.
                raise RuntimeError(f"All models failed. Last error: {last_error}")

            # At least one model is occupied — wait then retry from top of list
            logger.debug("All models at capacity, waiting for a slot…")
            await asyncio.sleep(0.5)


# Module-level singleton — imported by ai.py and main.py
router = ModelRouter()
