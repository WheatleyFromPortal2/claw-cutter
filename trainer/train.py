#!/usr/bin/env python3
"""
Claw Cutter Prompt Trainer
──────────────────────────
Continuously evaluates and improves the prompts in backend/prompts.json.

  • Our models.json models evaluate candidate prompts on real cards.
  • Claude (Anthropic API) proposes improved prompt variants.
  • The best variant is saved back to prompts.json automatically.

Usage:
  python trainer/train.py [options]

Options:
  --sample        Cards per evaluation round  (default: 20)
  --variants      Variants Claude generates    (default: 3)
  --max-iters     Stop after N iterations      (default: unlimited)
  --no-save       Don't write back to prompts.json
  --api-key KEY   Anthropic API key (overrides env / .env)
"""

import argparse
import asyncio
import json
import random
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# ── resolve paths ─────────────────────────────────────────────────────────────
TRAINER_DIR = Path(__file__).parent
BACKEND_DIR = TRAINER_DIR.parent / "backend"
EXAMPLES_DIR = TRAINER_DIR / "examples"
RESULTS_DIR = TRAINER_DIR / "results"
PROMPTS_FILE = BACKEND_DIR / "prompts.json"

sys.path.insert(0, str(BACKEND_DIR))

from docx_utils import strip_cutting, extract_text_from_xml  # noqa: E402
from ai import parse_cards  # noqa: E402
from evaluator import evaluate_prompts, good_examples, bad_examples, EvalScore, LogicEvaluator  # noqa: E402
from optimizer import PromptOptimizer  # noqa: E402

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.rule import Rule
from rich import box

console = Console()

# ── graceful shutdown ─────────────────────────────────────────────────────────
_shutdown = False

def _handle_signal(sig, frame):
    global _shutdown
    _shutdown = True
    console.print("\n[yellow]Stopping after current iteration…[/yellow]")

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── card loading ──────────────────────────────────────────────────────────────
def load_all_cards() -> list[dict]:
    docx_files = sorted(EXAMPLES_DIR.glob("*.docx"))
    if not docx_files:
        return []
    all_cards: list[dict] = []
    with console.status(f"[dim]Loading {len(docx_files)} docx files…[/dim]"):
        for path in docx_files:
            try:
                data = path.read_bytes()
                xml = strip_cutting(data)
                text = extract_text_from_xml(xml)
                cards = parse_cards(text)
                for c in cards:
                    c["_source"] = path.name
                all_cards.extend(cards)
            except Exception as exc:
                console.print(f"[red]  ✗ {path.name}: {exc}[/red]")
    return all_cards


# ── display helpers ───────────────────────────────────────────────────────────
def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _bar(v: float, width: int = 8) -> str:
    filled = round(v * width)
    return "█" * filled + "░" * (width - filled)


def score_color(v: float) -> str:
    if v >= 0.90:
        return "green"
    if v >= 0.70:
        return "yellow"
    return "red"


def print_score_table(score: EvalScore, title: str = "Evaluation Results") -> None:
    t = Table(title=title, box=box.ROUNDED, show_header=True, header_style="bold dim")
    t.add_column("Metric", style="dim", width=24)
    t.add_column("Score", justify="right", width=10)
    t.add_column("Target", justify="center", width=12)
    t.add_column("Bar", width=10)

    def row(label, val, target):
        color = score_color(val)
        t.add_row(
            label,
            f"[{color}]{_pct(val)}[/{color}]",
            target,
            f"[{color}]{_bar(val)}[/{color}]",
        )

    row("UL JSON valid",    score.ul_valid_rate,  "> 95%")
    row("UL exact match",   score.ul_exact_rate,  "> 95%")
    row("UL ratio score",   score.ul_ratio_score, "20–35%")
    row("HL JSON valid",    score.hl_valid_rate,  "> 95%")
    row("HL exact match",   score.hl_exact_rate,  "> 95%")
    row("HL ratio score",   score.hl_ratio_score, "15–25%")
    if score.logic_mean is not None:
        row("Logic coherence",  score.logic_mean,    "> 70%")
    t.add_section()

    comp_color = score_color(score.composite)
    t.add_row(
        "[bold]COMPOSITE[/bold]",
        f"[bold {comp_color}]{score.composite:.4f}[/bold {comp_color}]",
        "max 1.0",
        f"[{comp_color}]{_bar(score.composite)}[/{comp_color}]",
    )
    console.print(t)
    console.print(
        f"  [dim]UL ratio mean: {_pct(score.ul_ratio_mean)}  "
        f"HL ratio mean: {_pct(score.hl_ratio_mean)}  "
        f"n={score.n_cards}[/dim]"
    )


def print_comparison_table(scores: list[tuple[str, EvalScore, str]]) -> None:
    """scores = [(label, score, rationale), ...]"""
    has_logic = any(s.logic_mean is not None for _, s, _ in scores)

    t = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold dim")
    t.add_column("Variant", width=14)
    t.add_column("UL exact", justify="right", width=10)
    t.add_column("UL ratio", justify="right", width=10)
    t.add_column("HL exact", justify="right", width=10)
    t.add_column("HL ratio", justify="right", width=10)
    if has_logic:
        t.add_column("Logic", justify="right", width=10)
    t.add_column("Composite", justify="right", width=12)
    t.add_column("Change", justify="right", width=10)
    t.add_column("Note", width=35)

    baseline_comp = scores[0][1].composite if scores else 0.0

    for label, s, note in scores:
        delta = s.composite - baseline_comp
        if label == "current":
            delta_str = "[dim]—[/dim]"
        elif delta > 0:
            delta_str = f"[green]+{delta:.4f}[/green]"
        else:
            delta_str = f"[red]{delta:.4f}[/red]"

        comp_color = score_color(s.composite)
        row_cells = [
            label,
            _pct(s.ul_exact_rate),
            _pct(s.ul_ratio_mean),
            _pct(s.hl_exact_rate),
            _pct(s.hl_ratio_mean),
        ]
        if has_logic:
            logic_str = (
                f"[{score_color(s.logic_mean)}]{_pct(s.logic_mean)}[/{score_color(s.logic_mean)}]"
                if s.logic_mean is not None
                else "[dim]—[/dim]"
            )
            row_cells.append(logic_str)
        row_cells += [
            f"[{comp_color}]{s.composite:.4f}[/{comp_color}]",
            delta_str,
            f"[dim]{note[:34]}[/dim]",
        ]
        t.add_row(*row_cells)

    console.print(t)


# ── async evaluation with progress bar ────────────────────────────────────────
async def run_evaluation(
    underline_prompt: str,
    highlight_prompt: str,
    cards: list[dict],
    label: str,
    logic_client=None,
) -> EvalScore:
    with Progress(
        SpinnerColumn(),
        TextColumn(f"[dim]{label}[/dim]"),
        BarColumn(bar_width=28),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total} cards)"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as prog:
        task = prog.add_task("eval", total=len(cards))

        def cb(done: int, total: int) -> None:
            prog.update(task, completed=done)

        score = await evaluate_prompts(
            underline_prompt, highlight_prompt, cards,
            progress_cb=cb, logic_client=logic_client,
        )
    return score


# ── main training loop ────────────────────────────────────────────────────────
async def main(args: argparse.Namespace) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = RESULTS_DIR / f"run_{run_id}.json"
    run_log: list[dict] = []

    console.print(Panel.fit(
        "[bold cyan]CLAW CUTTER PROMPT TRAINER[/bold cyan]",
        border_style="cyan",
    ))

    # ── load cards ────────────────────────────────────────────────────────────
    console.print(Rule("[dim]Loading example cards[/dim]"))
    all_cards = load_all_cards()
    if not all_cards:
        console.print(
            Panel(
                "[red]No cards found.[/red]\n\n"
                f"Add .docx files to [bold]{EXAMPLES_DIR}[/bold] and re-run.",
                border_style="red",
            )
        )
        return

    console.print(
        f"  [green]✓[/green] {len(all_cards)} cards from "
        f"{len(set(c['_source'] for c in all_cards))} files\n"
    )

    # ── init optimizer ────────────────────────────────────────────────────────
    try:
        optimizer = PromptOptimizer(api_key=args.api_key)
    except ValueError as exc:
        console.print(Panel(str(exc), title="[red]API Key Error[/red]", border_style="red"))
        return

    # ── init logic evaluator (API key or claude CLI) ──────────────────────────
    logic_client = LogicEvaluator.create_if_available(api_key=args.api_key)
    if logic_client:
        mode = "API" if logic_client._api_key else "claude CLI"
        console.print(f"  [green]✓[/green] Logic evaluator enabled (Claude Haiku via {mode})\n")
    else:
        console.print("  [dim]Logic evaluator disabled (no API key and claude CLI not found)[/dim]\n")

    # ── load current prompts ──────────────────────────────────────────────────
    with open(PROMPTS_FILE) as f:
        best_prompts = json.load(f)
    current_prompts = dict(best_prompts)

    # ── baseline evaluation ───────────────────────────────────────────────────
    console.print(Rule("[dim]Baseline evaluation[/dim]"))
    sample = random.sample(all_cards, min(args.sample, len(all_cards)))
    baseline_score = await run_evaluation(
        current_prompts["underline"], current_prompts["highlight"],
        sample, "baseline", logic_client=logic_client,
    )
    print_score_table(baseline_score, title="Baseline — current prompts.json")

    best_score = baseline_score
    history: list[dict] = [{
        "iteration": 0,
        "composite": baseline_score.composite,
        "is_best": True,
        "label": "baseline",
    }]
    no_improve_streak = 0

    # ── optimization loop ─────────────────────────────────────────────────────
    iteration = 0
    while not _shutdown:
        if args.max_iters and iteration >= args.max_iters:
            console.print("[dim]Reached max iterations.[/dim]")
            break
        if no_improve_streak >= 5:
            console.print("[dim]No improvement in 5 consecutive iterations — stopping.[/dim]")
            break

        iteration += 1
        console.print(Rule(f"[bold]Iteration {iteration}[/bold]"))

        # ── generate variants ─────────────────────────────────────────────────
        console.print(
            f"  Asking [bold cyan]Claude[/bold cyan] for "
            f"[bold]{args.variants}[/bold] improved variants…"
        )
        t0 = time.time()
        try:
            variants = optimizer.generate_variants(
                current_prompts=current_prompts,
                score=best_score,
                good_ex=good_examples(best_score),
                bad_ex=bad_examples(best_score),
                history=history,
                n_variants=args.variants,
            )
        except Exception as exc:
            console.print(f"  [red]Optimizer error: {exc}[/red]")
            await asyncio.sleep(5)
            continue

        console.print(
            f"  [green]✓[/green] {len(variants)} variants received "
            f"[dim]({time.time() - t0:.1f}s)[/dim]"
        )
        for i, v in enumerate(variants, 1):
            console.print(f"    [dim]{i}. {v.get('rationale', '')[:72]}[/dim]")
        console.print()

        # draw a fresh sample for this iteration (consistent across all variants)
        sample = random.sample(all_cards, min(args.sample, len(all_cards)))

        # re-evaluate current on same sample (fair comparison baseline)
        iter_baseline = await run_evaluation(
            current_prompts["underline"], current_prompts["highlight"],
            sample, "current (re-eval)", logic_client=logic_client,
        )

        # ── evaluate each variant ─────────────────────────────────────────────
        variant_scores: list[tuple[str, EvalScore, str]] = [
            ("current", iter_baseline, "")
        ]
        for vi, variant in enumerate(variants, 1):
            ul = variant.get("underline", current_prompts["underline"])
            hl = variant.get("highlight", current_prompts["highlight"])
            note = variant.get("rationale", "")
            vscore = await run_evaluation(ul, hl, sample, f"variant {vi}/{len(variants)}", logic_client=logic_client)
            variant_scores.append((f"variant {vi}", vscore, note))

        print_comparison_table(variant_scores)

        # ── pick best ─────────────────────────────────────────────────────────
        _, top_score, _ = max(variant_scores, key=lambda x: x[1].composite)
        top_label, top_score, top_note = max(
            variant_scores, key=lambda x: x[1].composite
        )
        top_idx = [l for l, _, _ in variant_scores].index(top_label)
        top_variant = variants[top_idx - 1] if top_idx > 0 else None  # 0 = current

        iter_record = {
            "iteration": iteration,
            "composite": top_score.composite,
            "is_best": top_score.composite > best_score.composite,
            "label": top_label,
            "variants_tried": len(variants),
        }

        if top_score.composite > best_score.composite and top_variant:
            delta = top_score.composite - best_score.composite
            console.print(
                f"\n  [bold green]✓ New best: {top_score.composite:.4f} "
                f"(+{delta:.4f})[/bold green]  [{top_label}] {top_note[:50]}"
            )
            best_score = top_score
            best_prompts = {
                "underline": top_variant["underline"],
                "highlight": top_variant["highlight"],
            }
            current_prompts = dict(best_prompts)
            iter_record["is_best"] = True
            no_improve_streak = 0

            if not args.no_save:
                with open(PROMPTS_FILE, "w") as f:
                    json.dump(best_prompts, f, indent=2)
                console.print(
                    f"  [dim]Saved to {PROMPTS_FILE.relative_to(PROMPTS_FILE.parent.parent)}[/dim]"
                )
        else:
            console.print(
                f"\n  [dim]No improvement this iteration "
                f"(best still {best_score.composite:.4f})[/dim]"
            )
            no_improve_streak += 1

        history.append(iter_record)
        run_log.append({
            "iteration": iteration,
            "scores": [
                {"label": l, "composite": s.composite, "note": n}
                for l, s, n in variant_scores
            ],
            **iter_record,
        })
        with open(log_path, "w") as f:
            json.dump(run_log, f, indent=2)

        console.print()

    # ── final summary ─────────────────────────────────────────────────────────
    console.print(Rule("[dim]Run complete[/dim]"))
    console.print(f"  Iterations: {iteration}")
    console.print(
        f"  Best composite: [bold green]{best_score.composite:.4f}[/bold green]  "
        f"(baseline: {baseline_score.composite:.4f}  "
        f"Δ={best_score.composite - baseline_score.composite:+.4f})"
    )
    console.print(f"  Log saved: {log_path}")
    if not args.no_save:
        console.print(
            f"  Prompts saved: "
            f"{PROMPTS_FILE.relative_to(PROMPTS_FILE.parent.parent)}"
        )


# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Claw Cutter prompt trainer")
    p.add_argument("--sample",     type=int, default=20,  help="Cards per evaluation (default 20)")
    p.add_argument("--variants",   type=int, default=3,   help="Variants per iteration (default 3)")
    p.add_argument("--max-iters",  type=int, default=0,   help="Stop after N iters (0 = unlimited)")
    p.add_argument("--no-save",    action="store_true",   help="Don't write back to prompts.json")
    p.add_argument("--api-key",    default="",            help="Anthropic API key")
    args = p.parse_args()

    asyncio.run(main(args))
