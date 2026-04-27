"""
Use Claude (via claude CLI or Anthropic API) to generate improved prompt variants.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from evaluator import EvalScore

OPTIMIZER_MODEL = "claude-opus-4-7"

_SYSTEM = """\
You are an expert prompt engineer optimizing prompts for an AI debate evidence card cutting system.

The system has two prompts fed to smaller/local models:
1. UNDERLINE PROMPT  — instructs the model to select 20-35% of each card's body for underlining
                       (key claims, causal mechanisms, empirical evidence, impact statements)
2. HIGHLIGHT PROMPT  — instructs the model to select 15-25% of underlined text for highlighting
                       (absolute minimum text needed to convey the CAUSE→MECHANISM→IMPACT skeleton)

Critical constraints that must be preserved in every prompt you write:
- The model must return ONLY valid JSON — no extra text, no markdown fences
- Selected strings must be EXACT substrings of the source text (copy-paste accurate)
- Underline target: 20-35% of card body by character count
- Highlight target: 15-25% of underlined text by character count

The MOST IMPORTANT quality metric is logical coherence (logic_mean, scored by Claude):
- The underlined passages must form a coherent CAUSE→MECHANISM→IMPACT chain
- Critical reasoning steps must NOT be skipped — even if they don't seem "important"
- Reading only the underlines should let a debater reconstruct the full argument
- The highlights should form a telegraphic skeleton that captures that chain

You will receive the current prompts, their performance metrics (including logic_mean),
and concrete examples of good and bad outputs. Generate improved variants that address
the weakest metrics, prioritizing logic coherence above ratio/validity metrics.

Return ONLY a JSON object with this exact structure — no preamble, no explanation outside it:
{
  "variants": [
    {
      "underline": "...(complete underline prompt)...",
      "highlight": "...(complete highlight prompt)...",
      "rationale": "One sentence: what changed and why"
    }
  ]
}"""


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


def _fmt_examples(examples: list[dict], label: str) -> str:
    if not examples:
        return ""
    lines = [f"\n{label}:"]
    for ex in examples[:3]:
        lines.append(f"  Card: {ex['tag'][:70]}")
        if ex.get("error"):
            lines.append(f"  ERROR: {ex['error'][:120]}")
        elif ex.get("ul_underlined"):
            lines.append(
                f"  UL ratio={ex['ul_ratio']:.2f}, exact={ex['ul_exact']}, "
                f"count={len(ex['ul_underlined'])}"
            )
            for u in ex["ul_underlined"][:2]:
                lines.append(f"    › {u[:100]}")
            if ex.get("hl_highlighted"):
                lines.append(
                    f"  HL ratio={ex['hl_ratio']:.2f}, exact={ex['hl_exact']}, "
                    f"count={len(ex['hl_highlighted'])}"
                )
                for h in ex["hl_highlighted"][:2]:
                    lines.append(f"    · {h[:80]}")
        lines.append("")
    return "\n".join(lines)


def _call_claude_cli(system: str, user_msg: str) -> str:
    """Call the claude CLI non-interactively and return the text response."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError(
            "claude CLI not found in PATH. "
            "Install Claude Code or set ANTHROPIC_API_KEY to use the API instead."
        )
    result = subprocess.run(
        [
            claude_bin, "-p",
            "--system-prompt", system,
            "--model", OPTIMIZER_MODEL,
            "--tools", "",         # no tools needed
            "--output-format", "text",
            user_msg,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited {result.returncode}:\n{result.stderr[:400]}"
        )
    return result.stdout


def _call_anthropic_api(api_key: str, system: str, user_msg: str) -> str:
    """Call the Anthropic API directly and return the text response."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=OPTIMIZER_MODEL,
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    return resp.content[0].text


class PromptOptimizer:
    def __init__(self, api_key: str = ""):
        self._api_key = api_key or _get_api_key()
        # Prefer API key if available; fall back to claude CLI
        if not self._api_key and not shutil.which("claude"):
            raise ValueError(
                "No Anthropic API key found and claude CLI not in PATH.\n"
                "Either:\n"
                "  • Set ANTHROPIC_API_KEY in your environment / backend/.env, or\n"
                "  • Install Claude Code (https://claude.ai/code) and log in."
            )

    def _call(self, system: str, user_msg: str) -> str:
        if self._api_key:
            return _call_anthropic_api(self._api_key, system, user_msg)
        return _call_claude_cli(system, user_msg)

    def generate_variants(
        self,
        current_prompts: dict,
        score: EvalScore,
        good_ex: list[dict],
        bad_ex: list[dict],
        history: list[dict],
        n_variants: int = 3,
    ) -> list[dict]:
        """Ask Claude to generate n_variants improved prompt pairs."""
        hist_lines = ""
        if history:
            rows = history[-6:]
            hist_lines = "\nPREVIOUS ITERATION SCORES (oldest → newest):\n"
            for h in rows:
                marker = "★" if h.get("is_best") else " "
                hist_lines += f"  {marker} composite={h['composite']:.4f}  (iter {h['iteration']})\n"

        logic_line = (
            f"  Logic coherence {score.logic_mean:6.1%}   (target >70%)  ← PRIORITY\n"
            if score.logic_mean is not None
            else "  Logic coherence  n/a    (no API key — enable for logic eval)\n"
        )
        user_msg = f"""CURRENT UNDERLINE PROMPT:
---
{current_prompts['underline']}
---

CURRENT HIGHLIGHT PROMPT:
---
{current_prompts['highlight']}
---

PERFORMANCE METRICS ({score.n_cards} cards):
  UL JSON valid   {score.ul_valid_rate:6.1%}   (target >95%)
  UL exact match  {score.ul_exact_rate:6.1%}   (target >95%)
  UL ratio mean   {score.ul_ratio_mean:6.1%}   (target 20-35%)  score={score.ul_ratio_score:.3f}
  HL JSON valid   {score.hl_valid_rate:6.1%}   (target >95%)
  HL exact match  {score.hl_exact_rate:6.1%}   (target >95%)
  HL ratio mean   {score.hl_ratio_mean:6.1%}   (target 15-25%)  score={score.hl_ratio_score:.3f}
{logic_line}  COMPOSITE       {score.composite:6.4f}   (max 1.0000)
{hist_lines}
{_fmt_examples(good_ex, "GOOD OUTPUTS (these worked — preserve what makes them work)")}
{_fmt_examples(bad_ex, "BAD OUTPUTS (these failed — fix the root cause)")}

Generate {n_variants} improved variants. Prioritize the lowest-scoring metrics.
Each variant must contain the complete, self-contained prompt text (not diffs).
Return ONLY the JSON object."""

        text = self._call(_SYSTEM, user_msg)
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise ValueError(f"Claude returned no JSON.\nRaw response:\n{text[:400]}")
        data = json.loads(m.group(0))
        variants = data.get("variants", [])
        if not variants:
            raise ValueError("Claude returned an empty variants list.")
        return variants
