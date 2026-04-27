"""
Evaluate a prompt pair against a list of parsed cards using the configured models.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from model_router import router  # noqa: E402  (must come after sys.path insert)


# ── Targets ───────────────────────────────────────────────────────────────────
UL_RATIO_LO, UL_RATIO_HI = 0.20, 0.35
HL_RATIO_LO, HL_RATIO_HI = 0.15, 0.25

# ── Logic evaluation ──────────────────────────────────────────────────────────
LOGIC_MODEL = "claude-haiku-4-5-20251001"

_LOGIC_SYSTEM = """\
You are evaluating debate evidence card cuts for logical coherence.
Score how well the selected passages capture the argument's CAUSE→MECHANISM→IMPACT chain.
Return ONLY a JSON object: {"score": <integer 0-10>, "reason": "<one sentence>"}"""


def _get_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if key:
        return key
    env_path = Path(__file__).parent.parent / "backend" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return ""


class LogicEvaluator:
    """Scores logical coherence of a card cut using Claude Haiku via API or CLI."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key or _get_api_key()
        self._has_cli = bool(shutil.which("claude"))
        if not self._api_key and not self._has_cli:
            raise ValueError(
                "No Anthropic API key found and claude CLI not in PATH."
            )

    @classmethod
    def create_if_available(cls, api_key: str = "") -> Optional["LogicEvaluator"]:
        try:
            return cls(api_key)
        except ValueError:
            return None

    async def call(self, user_msg: str) -> str:
        if self._api_key:
            return await self._call_api(user_msg)
        return await asyncio.to_thread(self._call_cli, user_msg)

    async def _call_api(self, user_msg: str) -> str:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=self._api_key)
        resp = await client.messages.create(
            model=LOGIC_MODEL,
            max_tokens=128,
            system=_LOGIC_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        return resp.content[0].text

    def _call_cli(self, user_msg: str) -> str:
        claude_bin = shutil.which("claude")
        result = subprocess.run(
            [
                claude_bin, "-p",
                "--system-prompt", _LOGIC_SYSTEM,
                "--model", LOGIC_MODEL,
                "--output-format", "text",
                user_msg,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited {result.returncode}: {result.stderr[:200]}"
            )
        return result.stdout


def _ratio_score(value: float, lo: float, hi: float) -> float:
    """1.0 if value in [lo, hi], linearly decays to 0 outside."""
    if lo <= value <= hi:
        return 1.0
    if value < lo:
        return max(0.0, value / lo)
    return max(0.0, 1.0 - (value - hi) / hi)


# ── Per-card result ────────────────────────────────────────────────────────────
@dataclass
class CardResult:
    tag: str
    body_len: int
    ul_valid: bool = False
    ul_underlined: list = field(default_factory=list)
    ul_exact: bool = False        # all underlines are exact substrings of body
    ul_ratio: float = 0.0         # underlined chars / body chars
    hl_valid: bool = False
    hl_highlighted: list = field(default_factory=list)
    hl_exact: bool = False        # all highlights are exact substrings of underlines
    hl_ratio: float = 0.0         # highlighted chars / underlined chars
    logic_score: Optional[float] = None  # 0-1 coherence score from Claude, None if skipped
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "body_len": self.body_len,
            "ul_valid": self.ul_valid,
            "ul_underlined": self.ul_underlined,
            "ul_exact": self.ul_exact,
            "ul_ratio": self.ul_ratio,
            "hl_valid": self.hl_valid,
            "hl_highlighted": self.hl_highlighted,
            "hl_exact": self.hl_exact,
            "hl_ratio": self.hl_ratio,
            "logic_score": self.logic_score,
            "error": self.error,
        }


# ── Aggregate score ────────────────────────────────────────────────────────────
@dataclass
class EvalScore:
    n_cards: int
    ul_valid_rate: float
    ul_exact_rate: float
    ul_ratio_mean: float
    ul_ratio_score: float
    hl_valid_rate: float
    hl_exact_rate: float
    hl_ratio_mean: float
    hl_ratio_score: float
    composite: float
    logic_mean: Optional[float] = None  # None if logic eval was skipped
    results: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "n_cards": self.n_cards,
            "ul_valid_rate": self.ul_valid_rate,
            "ul_exact_rate": self.ul_exact_rate,
            "ul_ratio_mean": self.ul_ratio_mean,
            "ul_ratio_score": self.ul_ratio_score,
            "hl_valid_rate": self.hl_valid_rate,
            "hl_exact_rate": self.hl_exact_rate,
            "hl_ratio_mean": self.hl_ratio_mean,
            "hl_ratio_score": self.hl_ratio_score,
            "composite": self.composite,
            "logic_mean": self.logic_mean,
        }

    @classmethod
    def zero(cls) -> "EvalScore":
        return cls(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, logic_mean=None)


# ── Logic evaluation ──────────────────────────────────────────────────────────
async def _evaluate_logic(
    card_body: str,
    underlined: list[str],
    highlighted: list[str],
    evaluator: LogicEvaluator,
) -> float:
    """Ask Claude to score logical coherence of a cut. Returns 0.0–1.0."""
    ul_text = "\n".join(f"{i+1}. {u}" for i, u in enumerate(underlined))
    hl_text = ", ".join(f'"{h}"' for h in highlighted) if highlighted else "(none)"

    user_msg = (
        f"CARD BODY:\n{card_body[:2000]}\n\n"
        f"UNDERLINED PASSAGES:\n{ul_text}\n\n"
        f"HIGHLIGHTED PHRASES: {hl_text}\n\n"
        "Score this cut 0-10 on logical coherence:\n"
        "- Does the underlined text form a coherent CAUSE→MECHANISM→IMPACT chain?\n"
        "- Are critical reasoning steps missing from the underlines?\n"
        "- Do the highlights convey the core argument as a telegraphic skeleton?\n\n"
        "10 = Perfect chain, key argument fully preserved\n"
        "7-9 = Good, one minor gap\n"
        "4-6 = Key logical steps skipped or incoherent\n"
        "1-3 = Major gaps, misses core argument\n"
        "0 = Incoherent or empty"
    )
    try:
        text = await evaluator.call(user_msg)
        m = re.search(r"\{[\s\S]*?\}", text)
        if m:
            data = json.loads(m.group(0))
            raw = float(data.get("score", 5))
            return max(0.0, min(10.0, raw)) / 10.0
    except Exception:
        pass
    return 0.5  # neutral on failure


# ── Core evaluation ────────────────────────────────────────────────────────────
async def evaluate_prompts(
    underline_prompt: str,
    highlight_prompt: str,
    cards: list[dict],
    topic: str = "",
    progress_cb: Optional[Callable[[int, int], None]] = None,
    logic_client: Optional[LogicEvaluator] = None,
) -> EvalScore:
    """
    Run both prompts on every card in `cards` using the model router.
    Returns an EvalScore with per-card results attached.
    """
    results: list[CardResult] = []

    for i, card in enumerate(cards):
        if progress_cb:
            progress_cb(i, len(cards))

        res = CardResult(tag=card["tag"][:80], body_len=len(card["body"]))

        try:
            # ── Underline ──────────────────────────────────────────────────
            user_msg = (
                f"TOPIC: {topic}\n\n"
                f"CARD TAG: {card['tag']}\n"
                f"CITATION: {card['cite']}\n"
                f"BODY:\n{card['body'][:3000]}"
            )
            text, _model, _tokens = await router.call(
                underline_prompt, user_msg, max_tokens=2048
            )
            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                data = json.loads(m.group(0))
                res.ul_valid = True
                underlined: list[str] = data.get("underlined", [])
                res.ul_underlined = underlined

                body = card["body"]
                res.ul_exact = bool(underlined) and all(u in body for u in underlined)
                ul_chars = sum(len(u) for u in underlined)
                res.ul_ratio = ul_chars / max(len(body), 1)

                # ── Highlight ──────────────────────────────────────────────
                if underlined:
                    numbered = "\n".join(
                        f"{j + 1}. {p}" for j, p in enumerate(underlined)
                    )
                    hl_msg = f"CARD TAG: {card['tag']}\n\nUNDERLINED PASSAGES:\n{numbered}"
                    hl_text, _m2, _t2 = await router.call(
                        highlight_prompt, hl_msg, max_tokens=1024
                    )
                    hm = re.search(r"\{[\s\S]*\}", hl_text)
                    if hm:
                        hdata = json.loads(hm.group(0))
                        res.hl_valid = True
                        highlighted: list[str] = hdata.get("highlighted", [])
                        res.hl_highlighted = highlighted
                        ul_combined = " ".join(underlined)
                        res.hl_exact = bool(highlighted) and all(
                            h in ul_combined for h in highlighted
                        )
                        hl_chars = sum(len(h) for h in highlighted)
                        res.hl_ratio = hl_chars / max(ul_chars, 1)

                        # ── Logic ──────────────────────────────────────────
                        if logic_client and underlined:
                            res.logic_score = await _evaluate_logic(
                                card["body"], underlined, highlighted, logic_client
                            )

        except Exception as exc:
            res.error = str(exc)[:200]

        results.append(res)

    if progress_cb:
        progress_cb(len(cards), len(cards))

    return _aggregate(results)


def _aggregate(results: list[CardResult]) -> EvalScore:
    n = len(results)
    if n == 0:
        return EvalScore.zero()

    ul_valid = [r for r in results if r.ul_valid]
    hl_valid = [r for r in results if r.hl_valid]

    ul_valid_rate = len(ul_valid) / n
    ul_exact_rate = (
        sum(1 for r in ul_valid if r.ul_exact) / len(ul_valid) if ul_valid else 0.0
    )
    ul_ratio_mean = (
        sum(r.ul_ratio for r in ul_valid) / len(ul_valid) if ul_valid else 0.0
    )
    ul_ratio_score = _ratio_score(ul_ratio_mean, UL_RATIO_LO, UL_RATIO_HI)

    hl_valid_rate = len(hl_valid) / n
    hl_exact_rate = (
        sum(1 for r in hl_valid if r.hl_exact) / len(hl_valid) if hl_valid else 0.0
    )
    hl_ratio_mean = (
        sum(r.hl_ratio for r in hl_valid) / len(hl_valid) if hl_valid else 0.0
    )
    hl_ratio_score = _ratio_score(hl_ratio_mean, HL_RATIO_LO, HL_RATIO_HI)

    logic_scored = [r for r in results if r.logic_score is not None]
    if logic_scored:
        _logic_val: float = sum(r.logic_score for r in logic_scored) / len(logic_scored)  # type: ignore[misc]
        logic_mean: Optional[float] = _logic_val
        # Logic carries 25% of composite; remaining mechanics scaled to 75%
        composite = (
            0.20 * ul_valid_rate
            + 0.15 * ul_exact_rate
            + 0.10 * ul_ratio_score
            + 0.12 * hl_valid_rate
            + 0.10 * hl_exact_rate
            + 0.08 * hl_ratio_score
            + 0.25 * _logic_val
        )
    else:
        logic_mean = None
        composite = (
            0.25 * ul_valid_rate
            + 0.20 * ul_exact_rate
            + 0.15 * ul_ratio_score
            + 0.15 * hl_valid_rate
            + 0.15 * hl_exact_rate
            + 0.10 * hl_ratio_score
        )

    return EvalScore(
        n_cards=n,
        ul_valid_rate=ul_valid_rate,
        ul_exact_rate=ul_exact_rate,
        ul_ratio_mean=ul_ratio_mean,
        ul_ratio_score=ul_ratio_score,
        hl_valid_rate=hl_valid_rate,
        hl_exact_rate=hl_exact_rate,
        hl_ratio_mean=hl_ratio_mean,
        hl_ratio_score=hl_ratio_score,
        composite=composite,
        logic_mean=logic_mean,
        results=results,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────
def good_examples(score: EvalScore, n: int = 3) -> list[dict]:
    """Return up to n card results that scored well (logic score prioritized if available)."""
    good = [
        r for r in score.results
        if r.ul_valid and r.ul_exact and r.hl_valid
        and UL_RATIO_LO <= r.ul_ratio <= UL_RATIO_HI
        and (r.logic_score is None or r.logic_score >= 0.7)
    ]
    good.sort(key=lambda r: r.logic_score or 0.0, reverse=True)
    return [r.to_dict() for r in good[:n]]


def bad_examples(score: EvalScore, n: int = 3) -> list[dict]:
    """Return up to n card results that failed in some way (low logic score prioritized)."""
    bad = [
        r for r in score.results
        if not r.ul_valid or not r.ul_exact or r.error
        or r.ul_ratio < UL_RATIO_LO * 0.5
        or r.ul_ratio > UL_RATIO_HI * 1.5
        or (r.logic_score is not None and r.logic_score < 0.5)
    ]
    bad.sort(key=lambda r: r.logic_score or 1.0)
    return [r.to_dict() for r in bad[:n]]
