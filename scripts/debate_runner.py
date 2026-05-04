#!/usr/bin/env python3
"""Multi-agent debate orchestrator for daisy-financial-research.

Drives the Bull/Bear/Synthesis loop and the Aggressive/Conservative/Neutral/
Portfolio-Manager loop documented in references/debate-prompts.md and
references/risk-debate-prompts.md. The script is a state-machine referee +
template renderer: it never calls an LLM, never fetches data, and never
persists state outside the agent-supplied --pad path.

State for a single debate lives in a per-task scratchpad JSONL (the same
format dexter_scratchpad.py uses). All records carry a debate_id so
multiple debates can coexist in the same pad.

Exit codes (also documented in --help epilogs):
  0 ok
  1 runtime
  2 auth         (not used; this script reaches no upstream service)
  3 validation
  4 no_data      (not used; missing data is reported as validation)
  5 dependency   (not used; stdlib only)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _envelope import (
    ExitCode,
    Timer,
    add_common_args,
    emit_failure,
    emit_schema,
    emit_success,
    resolve_format,
)

SCHEMA: dict[str, Any] = {
    "name": "debate_runner",
    "description": (
        "Multi-agent debate orchestrator. Three subcommands: init / next / "
        "synthesize. State stored in the agent-supplied scratchpad JSONL "
        "under a generated debate_id."
    ),
    "subcommands": {
        "init": {
            "description": (
                "Start a new debate. Generates debate_id, writes a "
                "debate_init record to the pad, returns the first speaker's "
                "rendered prompt."
            ),
            "parameters": {
                "--type": {"required": True, "choices": ["research", "risk"]},
                "--ticker": {"required": True, "type": "string"},
                "--pad": {"required": True, "type": "path"},
                "--context-file": {
                    "required": True,
                    "type": "path",
                    "format": "JSON object mapping placeholder name to text",
                },
                "--max-rounds": {"required": False, "type": "int 1..5", "default": 1},
                "--prior-synthesis-file": {
                    "required": "when --type=risk",
                    "type": "path",
                    "format": "Plain text from the prior research synthesis",
                },
            },
            "outputs": {
                "data": {
                    "debate_id": "string",
                    "next_action": "speak",
                    "speaker": "Bull (research) | Aggressive (risk)",
                    "round": "int (1)",
                    "turn": "int (1)",
                    "prompt": "string (rendered first-speaker prompt)",
                },
            },
        },
        "next": {
            "description": (
                "Record the just-completed speaker's argument and return "
                "the next prompt, or signal that synthesize should be called."
            ),
            "parameters": {
                "--pad": {"required": True, "type": "path"},
                "--debate-id": {"required": True, "type": "string"},
                "--argument-file": {
                    "required": True,
                    "type": "path",
                    "format": "Plain text containing the speaker's full argument",
                },
            },
            "outputs": {
                "data": {
                    "debate_id": "string",
                    "next_action": "speak | synthesize",
                    "speaker": "the next speaker, or null when next_action=synthesize",
                    "round": "int (1-based)",
                    "turn": "int (1-based, global counter)",
                    "prompt": "next-speaker prompt, or '' when next_action=synthesize",
                },
            },
        },
        "synthesize": {
            "description": (
                "Render the synthesis prompt. Must be called after next has "
                "returned next_action=synthesize. Does not take an argument file."
            ),
            "parameters": {
                "--pad": {"required": True, "type": "path"},
                "--debate-id": {"required": True, "type": "string"},
            },
            "outputs": {
                "data": {
                    "debate_id": "string",
                    "next_action": "done",
                    "speaker": "ResearchManager (research) | PortfolioManager (risk)",
                    "round": "int (final)",
                    "turn": "int (final)",
                    "prompt": "string (rendered synthesis prompt)",
                },
            },
        },
    },
    "errors": {
        "context_file_invalid_json": "exit=3, init: --context-file is not a JSON object",
        "context_file_missing": "exit=3, init/next: --context-file path does not exist",
        "max_rounds_out_of_range": "exit=3, init: --max-rounds not in 1..5",
        "prior_synthesis_not_applicable": "exit=3, init: --prior-synthesis-file passed with --type=research",
        "prior_synthesis_required": "exit=3, init: --prior-synthesis-file missing with --type=risk",
        "prior_synthesis_missing": "exit=3, init: --prior-synthesis-file path does not exist",
        "debate_id_not_found": "exit=3, next/synthesize: pad has no debate_init for that id",
        "debate_id_duplicate": "exit=3, next/synthesize: pad has 2+ debate_init records with the same id",
        "debate_state_corrupted": "exit=3, next/synthesize: turn-sequence anomaly (gap, dup, or wrong rotation)",
        "debate_already_synthesized": "exit=3, next/synthesize: synthesis already recorded",
        "debate_not_ready_for_synthesis": "exit=3, synthesize: count < bound",
        "argument_file_missing": "exit=3, next: --argument-file path does not exist",
        "argument_empty": "exit=3, next: --argument-file is whitespace-only",
        "pad_write_failed": "exit=1, init/next: filesystem error appending to pad",
        "pad_corrupted": "exit=1, next/synthesize: pad has a non-JSONL line",
    },
    "data_sources": {
        "scratchpad_jsonl": "Reads + appends. Format defined by dexter_scratchpad.py.",
        "context_file": "Read-only. Agent-supplied JSON object with placeholder values.",
        "prior_synthesis_file": "Read-only. Plain text. Required for --type=risk.",
        "argument_file": "Read-only. Plain text. Required for next.",
    },
    "error_codes": [
        "validation_error",
        "runtime_error",
    ],
}

# Prompt templates. These MUST stay byte-identical to the fenced ```text
# blocks in references/debate-prompts.md and references/risk-debate-prompts.md.
# tests/test_debate_prompts_match_references.py enforces this.
_PROMPTS: dict[str, str] = {
    "research.bull": """\
You are a Bull Analyst advocating for investing in {ticker}. Build a strong, evidence-based case emphasizing growth potential, competitive advantages, and positive market indicators. Use the supplied research and data to address concerns and counter likely bear arguments preemptively.

Focus on:
- Growth potential: market opportunity, revenue trajectory, scalability, addressable market.
- Competitive advantages: moats — unique products, brand, distribution, regulation, network effects, cost position, dominant share.
- Positive indicators: financial health, industry tailwind, recent positive catalysts, capital return (dividend / buyback) where relevant.
- Anticipated bear counterpoints: name the strongest two or three bear arguments and rebut each with specific data.
- For banks / insurers / financial-sector names: lead with RoTE / RoE, CET1, payout ratio + buyback capacity, NIM trend, credit cost, P/B vs cost-of-equity. Do not lead with DCF.

Engagement style: a tight, conversational paragraph or two, not a bullet dump. Cite specific numbers (date, source) for every claim that drives the conclusion.

Resources you have:
- Market / price data: {market_data}
- Financials and ratios: {fundamentals}
- News and catalysts: {news}
- Sector context: {sector_context}
- Past calls on this ticker (from the cross-session memory log): {past_context}

Deliver the bull argument now.""",
    "research.bear": """\
You are a Bear Analyst making the case against investing in {ticker}. Present a well-reasoned argument emphasizing risks, structural challenges, and negative indicators. Use the supplied research to highlight downside and to expose weaknesses in the bull case.

Focus on:
- Risks and headwinds: market saturation, financial fragility, leverage, regulatory exposure, macro sensitivity, currency / commodity drag.
- Competitive weaknesses: weakening market position, declining innovation, share loss to peers or substitutes, governance issues.
- Negative indicators: deteriorating margins, cash-flow weakness, accruals quality, unfavorable insider activity, earnings-quality issues, recent adverse news.
- Bull counterpoints: name the bull's strongest two or three claims and challenge each with specific data — flag over-optimistic assumptions, mark-to-model risk, or one-off items inflating the trend.
- For banks / insurers: NPL formation and coverage, RWA density, capital adequacy under stress, dividend coverage by core earnings, exposure to property / sovereign / FX shocks. Do not rely on DCF.

Engagement style: tight, conversational paragraph or two; cite specific numbers (date, source) for every claim.

Resources you have:
- Market / price data: {market_data}
- Financials and ratios: {fundamentals}
- News and catalysts: {news}
- Sector context: {sector_context}
- Past calls on this ticker (from the cross-session memory log): {past_context}
- Bull case to rebut: {bull_argument}

Deliver the bear argument now.""",
    "research.synthesis": """\
You are the Research Manager. Critically evaluate the bull / bear debate above and deliver a clear, actionable investment plan.

Rating scale (use exactly one):
- Buy          — Strong conviction in the bull thesis; recommend taking or growing the position
- Overweight   — Constructive view; recommend gradually increasing exposure
- Hold         — Balanced view; recommend maintaining the current position
- Underweight  — Cautious view; recommend trimming exposure
- Sell         — Strong conviction in the bear thesis; recommend exiting or avoiding the position

Commit to a clear stance whenever the strongest arguments warrant one; reserve Hold for situations where the evidence on both sides is genuinely balanced. Do not hedge for politeness.

Output format (exactly these sections, in this order):

**Rating:** <one of Buy / Overweight / Hold / Underweight / Sell>

**Thesis (2-4 sentences):** the single strongest reason for the rating, anchored on the most decisive piece of evidence.

**What would change the view:** the two or three observable conditions that would move the rating up or down a notch (specific metric thresholds, dates, catalysts).

**Risks acknowledged:** the strongest one or two bear points the rating cannot fully neutralize, named honestly.

**Holding period and re-check trigger:** target horizon (e.g. "next 2 quarters / next earnings"), and the precise event or metric that should trigger the next look.

Debate to evaluate:
{bull_argument}

{bear_argument}

Past calls on this ticker (from the cross-session memory log):
{past_context}

Deliver the rating and plan now.""",
    "risk.aggressive": """\
As the Aggressive Risk Analyst, your role is to actively champion high-reward, high-risk opportunities, emphasizing bold strategies and competitive advantages. When evaluating the prior recommendation and plan, focus intently on the potential upside, growth potential, and innovative benefits — even when these come with elevated risk. Use the supplied research to strengthen your arguments and challenge opposing views.

Specifically, respond directly to each point made by the conservative and neutral analysts (if any), countering with data-driven rebuttals and persuasive reasoning. Highlight where their caution might miss critical opportunities or where their assumptions may be overly conservative.

Prior recommendation to push aggressively on:
{prior_synthesis}

Research and data:
- Market / price data: {market_data}
- Financials and ratios: {fundamentals}
- News and catalysts: {news}
- Sector context: {sector_context}

Existing arguments in the debate (may be empty on first turn):
- Last conservative argument: {conservative_response}
- Last neutral argument: {neutral_response}

Engage actively. Address specific concerns raised, refute weaknesses in their logic, and assert the benefits of risk-taking. Maintain a focus on debating and persuading, not just presenting data. Output conversationally, no headers or bullets.""",
    "risk.conservative": """\
As the Conservative Risk Analyst, your primary objective is to protect capital, minimize volatility, and ensure steady, reliable growth. You prioritize stability, security, and risk mitigation — assess potential losses, downturns, and adverse scenarios carefully. When evaluating the prior recommendation, critically examine high-risk elements and point out where the plan may expose the position to undue risk and where more cautious alternatives could secure long-term gains.

Prior recommendation to push back on:
{prior_synthesis}

Research and data:
- Market / price data: {market_data}
- Financials and ratios: {fundamentals}
- News and catalysts: {news}
- Sector context: {sector_context}

Existing arguments in the debate:
- Last aggressive argument: {aggressive_response}
- Last neutral argument: {neutral_response}

Question their optimism. Emphasize potential downsides they may have overlooked. Address each of their counterpoints to showcase why a conservative stance is the safest path. Focus on debating and critiquing, not just presenting data. Output conversationally, no headers or bullets.""",
    "risk.neutral": """\
As the Neutral Risk Analyst, provide a balanced perspective, weighing both upside and risk in the prior recommendation. Prioritize a well-rounded approach: factor in broader market trends, potential economic shifts, and diversification.

Prior recommendation to balance:
{prior_synthesis}

Research and data:
- Market / price data: {market_data}
- Financials and ratios: {fundamentals}
- News and catalysts: {news}
- Sector context: {sector_context}

Existing arguments in the debate:
- Last aggressive argument: {aggressive_response}
- Last conservative argument: {conservative_response}

Challenge both sides — point out where the aggressive view is overly optimistic and where the conservative view is overly cautious. Propose a moderate sizing / stop / horizon that captures the upside while protecting against the most likely downside scenarios. Output conversationally, no headers or bullets.""",
    "risk.synthesis": """\
As the Portfolio Manager, synthesize the risk analysts' debate above and deliver the final position plan.

Rating Scale (use exactly one):
- Buy          — Strong conviction to enter or add to position
- Overweight   — Favorable outlook; gradually increase exposure
- Hold         — Maintain current position; no action needed
- Underweight  — Reduce exposure; take partial profits
- Sell         — Exit position or avoid entry

Context:
- Prior research synthesis (directional view): {prior_synthesis}
- Past calls on this ticker (from the cross-session memory log): {past_context}

Risk Analysts Debate History:
{aggressive_argument}

{conservative_argument}

{neutral_argument}

---

Output exactly the section headers below, in this order (the rest of daisy's
report writers, memory log, and any external parsers depend on this shape):

**Rating**: <Buy / Overweight / Hold / Underweight / Sell>

**Executive Summary**: <Two to four sentences covering entry strategy, position sizing, key risk levels, and time horizon.>

**Investment Thesis**: <Detailed reasoning anchored in specific evidence from the analysts' debate. If past lessons are referenced above, incorporate them; otherwise rely solely on the current analysis.>

**Price Target**: <Optional. Numeric target in the instrument's quote currency, or omit the line entirely.>

**Time Horizon**: <Optional. e.g. "3-6 months", or omit the line entirely.>

Be decisive. Reserve Hold for situations where the evidence on both sides is genuinely balanced. Ground every claim in specific evidence from the analysts.""",
}


def _build_parser() -> argparse.ArgumentParser:
    epilog = (
        "Exit codes: 0 ok | 1 runtime | 2 auth (unused) | 3 validation | "
        "4 no_data (unused) | 5 dependency (unused)."
    )
    parser = argparse.ArgumentParser(
        prog="debate_runner.py",
        description=__doc__.split("\n")[0],
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=False)

    p_init = sub.add_parser("init", description="Start a new debate.", epilog=epilog,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    p_init.add_argument("--type", choices=["research", "risk"], required=True)
    p_init.add_argument("--ticker", required=True)
    p_init.add_argument("--pad", required=True)
    p_init.add_argument("--context-file", required=True)
    p_init.add_argument("--max-rounds", type=int, default=1)
    p_init.add_argument("--prior-synthesis-file", default=None)
    add_common_args(p_init)

    p_next = sub.add_parser("next", description="Advance the debate.", epilog=epilog,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    p_next.add_argument("--pad", required=True)
    p_next.add_argument("--debate-id", required=True)
    p_next.add_argument("--argument-file", required=True)
    add_common_args(p_next)

    p_synth = sub.add_parser("synthesize", description="Render the synthesis prompt.",
                             epilog=epilog,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
    p_synth.add_argument("--pad", required=True)
    p_synth.add_argument("--debate-id", required=True)
    add_common_args(p_synth)

    # --schema and --format on the root parser too, so `debate_runner.py --schema`
    # works without picking a subcommand.
    add_common_args(parser)
    return parser


def _cmd_init(args: argparse.Namespace, fmt: str, timer: Timer) -> int:
    raise NotImplementedError("Task 3")


def _cmd_next(args: argparse.Namespace, fmt: str, timer: Timer) -> int:
    raise NotImplementedError("Task 5")


def _cmd_synthesize(args: argparse.Namespace, fmt: str, timer: Timer) -> int:
    raise NotImplementedError("Task 6")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    timer = Timer()
    fmt = resolve_format(getattr(args, "format", None))
    if getattr(args, "schema", False):
        return emit_schema(SCHEMA, fmt, timer=timer)
    if not args.cmd:
        return emit_failure(
            ExitCode.VALIDATION,
            "missing subcommand: choose one of init / next / synthesize",
            fmt,
            code="validation_error",
            retryable=False,
            context={"valid_subcommands": ["init", "next", "synthesize"]},
            timer=timer,
        )
    handlers = {
        "init": _cmd_init,
        "next": _cmd_next,
        "synthesize": _cmd_synthesize,
    }
    try:
        return handlers[args.cmd](args, fmt, timer)
    except Exception as e:
        return emit_failure(
            ExitCode.RUNTIME,
            f"{type(e).__name__}: {e}",
            fmt,
            code="runtime_error",
            retryable=False,
            context={"subcommand": args.cmd},
            timer=timer,
        )


if __name__ == "__main__":
    raise SystemExit(main())
