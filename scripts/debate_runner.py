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
from dataclasses import dataclass
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
- Most recent bear argument to engage with (empty on the very first turn): {bear_argument}

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


# ---------------------------------------------------------------------------
# Per-debate-type rotation rules
# ---------------------------------------------------------------------------

ROTATION = {
    "research": ["Bull", "Bear"],          # length 2 -> bound = 2 * max_rounds
    "risk":     ["Aggressive", "Conservative", "Neutral"],  # length 3 -> 3 * max_rounds
}

SYNTH_SPEAKER = {
    "research": "ResearchManager",
    "risk":     "PortfolioManager",
}

PROMPT_KEY_FOR_SPEAKER = {
    "Bull": "research.bull",
    "Bear": "research.bear",
    "Aggressive": "risk.aggressive",
    "Conservative": "risk.conservative",
    "Neutral": "risk.neutral",
    "ResearchManager": "research.synthesis",
    "PortfolioManager": "risk.synthesis",
}


class _SafeDict(dict):
    """str.format_map fallback for missing placeholders."""

    def __missing__(self, key: str) -> str:
        return "_(not provided)_"


def _now_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _validate_init_args(args: argparse.Namespace):
    """Return (exit_code, error_code, context) tuple on validation failure, else None."""
    if not (1 <= args.max_rounds <= 5):
        return ExitCode.VALIDATION, "max_rounds_out_of_range", {
            "value": args.max_rounds, "allowed": "1..5",
        }
    if args.type == "research" and args.prior_synthesis_file:
        return ExitCode.VALIDATION, "prior_synthesis_not_applicable", {
            "reason": "research debates have no prior synthesis to reference",
        }
    if args.type == "risk" and not args.prior_synthesis_file:
        return ExitCode.VALIDATION, "prior_synthesis_required", {
            "reason": "every risk-layer prompt has {prior_synthesis}",
        }
    ctx_path = Path(args.context_file)
    if not ctx_path.exists():
        return ExitCode.VALIDATION, "context_file_missing", {"path": str(ctx_path)}
    try:
        ctx_text = _read_text_file(ctx_path)
        ctx_obj = json.loads(ctx_text)
    except json.JSONDecodeError as e:
        return ExitCode.VALIDATION, "context_file_invalid_json", {
            "path": str(ctx_path), "error": str(e),
        }
    if not isinstance(ctx_obj, dict):
        return ExitCode.VALIDATION, "context_file_invalid_json", {
            "path": str(ctx_path), "error": "top level must be a JSON object (mapping)",
        }
    if args.type == "risk":
        ps_path = Path(args.prior_synthesis_file)
        if not ps_path.exists():
            return ExitCode.VALIDATION, "prior_synthesis_missing", {"path": str(ps_path)}
    return None


def _render_prompt(speaker: str, vars_: dict) -> str:
    key = PROMPT_KEY_FOR_SPEAKER[speaker]
    return _PROMPTS[key].format_map(_SafeDict(vars_))


def _append_pad(pad: Path, record: dict):
    """Append a single JSONL record. Returns None on success or an exit code on failure."""
    try:
        pad.parent.mkdir(parents=True, exist_ok=True)
        with pad.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return None
    except OSError:
        return ExitCode.RUNTIME


@dataclass
class _Turn:
    speaker: str
    round: int
    turn: int
    argument: str


@dataclass
class _State:
    init: dict
    turns: list
    synthesized: bool

    @property
    def debate_type(self) -> str:
        return self.init["debate_type"]

    @property
    def max_rounds(self) -> int:
        return self.init["max_rounds"]

    @property
    def bound(self) -> int:
        return len(ROTATION[self.debate_type]) * self.max_rounds

    def round_for_turn(self, turn_idx: int) -> int:
        return (turn_idx - 1) // len(ROTATION[self.debate_type]) + 1


def _load_pad_records(pad: Path):
    """Returns (records, error). On failure, records is None and error is a (exit_code, code, ctx) tuple."""
    if not pad.exists():
        return [], None
    records = []
    for i, line in enumerate(pad.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            return None, (ExitCode.RUNTIME, "pad_corrupted",
                          {"path": str(pad), "line_number": i})
    return records, None


def _replay_state(pad: Path, debate_id: str):
    records, err = _load_pad_records(pad)
    if err is not None:
        return None, err
    inits = [r for r in records if r.get("type") == "debate_init"
             and r.get("debate_id") == debate_id]
    if len(inits) == 0:
        return None, (ExitCode.VALIDATION, "debate_id_not_found",
                      {"debate_id": debate_id, "path": str(pad)})
    if len(inits) > 1:
        return None, (ExitCode.VALIDATION, "debate_id_duplicate",
                      {"debate_id": debate_id, "count": len(inits)})
    init = inits[0]
    debate_type = init["debate_type"]
    rotation = ROTATION[debate_type]

    turns_raw = sorted(
        (r for r in records
         if r.get("type") == "debate_turn" and r.get("debate_id") == debate_id),
        key=lambda r: r["turn"],
    )
    expected_turn = 1
    for r in turns_raw:
        if r["turn"] != expected_turn:
            return None, (ExitCode.VALIDATION, "debate_state_corrupted", {
                "debate_id": debate_id,
                "at_turn": expected_turn,
                "expected_speaker": rotation[(expected_turn - 1) % len(rotation)],
                "actual_speaker": None,
                "rotation_rule": f"{debate_type}: " + ", ".join(rotation) + ", ...",
                "reason": "missing turn",
            })
        expected_speaker = rotation[(r["turn"] - 1) % len(rotation)]
        if r["speaker"] != expected_speaker:
            return None, (ExitCode.VALIDATION, "debate_state_corrupted", {
                "debate_id": debate_id,
                "at_turn": r["turn"],
                "expected_speaker": expected_speaker,
                "actual_speaker": r["speaker"],
                "rotation_rule": f"{debate_type}: " + ", ".join(rotation) + ", ...",
            })
        expected_turn += 1

    turns = [_Turn(speaker=r["speaker"], round=r["round"], turn=r["turn"],
                   argument=r["argument"]) for r in turns_raw]
    synthesized = any(r.get("type") == "debate_synthesis"
                      and r.get("debate_id") == debate_id for r in records)
    return _State(init=init, turns=turns, synthesized=synthesized), None


def _build_speaker_vars_for_speaker_turn(state: _State, ctx: dict, speaker: str) -> dict:
    """Variable bag for rendering a per-turn speaker prompt."""
    vars_ = dict(ctx)
    vars_["ticker"] = state.init["ticker"]
    if state.debate_type == "risk":
        prior_path = state.init.get("prior_synthesis_file_path")
        if prior_path:
            try:
                vars_["prior_synthesis"] = _read_text_file(Path(prior_path))
            except OSError:
                vars_["prior_synthesis"] = "_(not provided)_"

    last_by_role = {}
    for t in state.turns:
        last_by_role[t.speaker] = t.argument

    if state.debate_type == "research":
        if speaker == "Bear":
            vars_["bull_argument"] = last_by_role.get("Bull", "_(not yet spoken)_")
        elif speaker == "Bull":
            vars_["bear_argument"] = last_by_role.get("Bear", "_(not yet spoken)_")
    elif state.debate_type == "risk":
        if speaker == "Aggressive":
            vars_["conservative_response"] = last_by_role.get("Conservative", "_(not yet spoken)_")
            vars_["neutral_response"] = last_by_role.get("Neutral", "_(not yet spoken)_")
        elif speaker == "Conservative":
            vars_["aggressive_response"] = last_by_role.get("Aggressive", "_(not yet spoken)_")
            vars_["neutral_response"] = last_by_role.get("Neutral", "_(not yet spoken)_")
        elif speaker == "Neutral":
            vars_["aggressive_response"] = last_by_role.get("Aggressive", "_(not yet spoken)_")
            vars_["conservative_response"] = last_by_role.get("Conservative", "_(not yet spoken)_")
    return vars_


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
    err = _validate_init_args(args)
    if err is not None:
        exit_code, code, ctx = err
        return emit_failure(exit_code, code.replace("_", " "), fmt,
                            code=code, context=ctx, timer=timer)

    ctx_path = Path(args.context_file)
    ctx_text = _read_text_file(ctx_path)
    ctx_obj = json.loads(ctx_text)

    prior_text = None
    prior_hash = None
    if args.type == "risk":
        prior_path = Path(args.prior_synthesis_file)
        prior_text = _read_text_file(prior_path)
        prior_hash = _sha256_text(prior_text)

    debate_id = f"dbg_{_now_compact()}_{args.type}"

    init_record = {
        "ts": _now_iso_z(),
        "type": "debate_init",
        "debate_id": debate_id,
        "debate_type": args.type,
        "ticker": args.ticker,
        "max_rounds": args.max_rounds,
        "context_file_path": str(ctx_path.resolve()),
        "context_file_sha256": _sha256_text(ctx_text),
        "context_keys": sorted(ctx_obj.keys()),
        "prior_synthesis_file_path": (
            str(Path(args.prior_synthesis_file).resolve()) if args.prior_synthesis_file else None
        ),
        "prior_synthesis_sha256": prior_hash,
    }

    speaker = ROTATION[args.type][0]
    vars_ = {**ctx_obj, "ticker": args.ticker}
    if prior_text is not None:
        vars_["prior_synthesis"] = prior_text
    if args.type == "research":
        vars_.setdefault("bear_argument", "_(not yet spoken)_")
    if args.type == "risk":
        vars_.setdefault("conservative_response", "_(not yet spoken)_")
        vars_.setdefault("neutral_response", "_(not yet spoken)_")

    prompt = _render_prompt(speaker, vars_)

    if args.dry_run:
        return emit_success({
            "debate_id": debate_id,
            "next_action": "speak",
            "speaker": speaker,
            "round": 1,
            "turn": 1,
            "prompt": prompt,
            "would_write": init_record,
        }, fmt, timer=timer, meta_extra={"dry_run": True})

    write_err = _append_pad(Path(args.pad), init_record)
    if write_err is not None:
        return emit_failure(write_err, "failed to append init record",
                            fmt, code="pad_write_failed", retryable=True,
                            context={"path": str(args.pad)}, timer=timer)

    return emit_success({
        "debate_id": debate_id,
        "next_action": "speak",
        "speaker": speaker,
        "round": 1,
        "turn": 1,
        "prompt": prompt,
    }, fmt, timer=timer)


def _cmd_next(args: argparse.Namespace, fmt: str, timer: Timer) -> int:
    pad = Path(args.pad)

    arg_path = Path(args.argument_file)
    if not arg_path.exists():
        return emit_failure(ExitCode.VALIDATION, "argument file missing", fmt,
                            code="argument_file_missing",
                            context={"path": str(arg_path)}, timer=timer)
    arg_text = _read_text_file(arg_path)
    if not arg_text.strip():
        return emit_failure(ExitCode.VALIDATION, "argument file is whitespace-only", fmt,
                            code="argument_empty",
                            context={"path": str(arg_path)}, timer=timer)

    state, err = _replay_state(pad, args.debate_id)
    if err is not None:
        exit_code, code, ctx = err
        return emit_failure(exit_code, code.replace("_", " "), fmt,
                            code=code, context=ctx, timer=timer)
    if state.synthesized:
        return emit_failure(ExitCode.VALIDATION, "debate already synthesized", fmt,
                            code="debate_already_synthesized",
                            context={"debate_id": args.debate_id}, timer=timer)

    rotation = ROTATION[state.debate_type]
    just_spoke_turn = len(state.turns) + 1
    just_spoke_speaker = rotation[(just_spoke_turn - 1) % len(rotation)]
    just_spoke_round = state.round_for_turn(just_spoke_turn)

    new_turn_record = {
        "ts": _now_iso_z(),
        "type": "debate_turn",
        "debate_id": args.debate_id,
        "speaker": just_spoke_speaker,
        "round": just_spoke_round,
        "turn": just_spoke_turn,
        "argument": arg_text.rstrip("\n"),
    }

    ctx_path = Path(state.init["context_file_path"])
    if not ctx_path.exists():
        return emit_failure(ExitCode.VALIDATION, "context file missing", fmt,
                            code="context_file_missing",
                            context={"path": str(ctx_path)}, timer=timer)
    ctx_obj, warnings = _read_context_with_drift_check(state)
    warnings.extend(_check_prior_synthesis_drift(state))

    new_count = just_spoke_turn  # number of turns AFTER this one is recorded
    if new_count >= state.bound:
        next_data = {
            "debate_id": args.debate_id,
            "next_action": "synthesize",
            "speaker": None,
            "round": just_spoke_round,
            "turn": new_count,
            "prompt": "",
        }
    else:
        next_speaker = rotation[new_count % len(rotation)]
        augmented = _State(
            init=state.init,
            turns=state.turns + [_Turn(speaker=just_spoke_speaker,
                                       round=just_spoke_round,
                                       turn=just_spoke_turn,
                                       argument=arg_text.rstrip("\n"))],
            synthesized=False,
        )
        vars_ = _build_speaker_vars_for_speaker_turn(augmented, ctx_obj, next_speaker)
        next_data = {
            "debate_id": args.debate_id,
            "next_action": "speak",
            "speaker": next_speaker,
            "round": state.round_for_turn(new_count + 1),
            "turn": new_count + 1,
            "prompt": _render_prompt(next_speaker, vars_),
        }

    meta_extra = {"dry_run": True} if args.dry_run else None
    if warnings:
        meta_extra = meta_extra or {}
        meta_extra["warnings"] = warnings

    if args.dry_run:
        return emit_success(next_data, fmt, timer=timer, meta_extra=meta_extra)

    write_err = _append_pad(pad, new_turn_record)
    if write_err is not None:
        return emit_failure(write_err, "failed to append turn record", fmt,
                            code="pad_write_failed", retryable=True,
                            context={"path": str(pad)}, timer=timer)
    if warnings:
        _persist_warnings(pad, args.debate_id, warnings)
    return emit_success(next_data, fmt, timer=timer, meta_extra=meta_extra)


def _read_context_with_drift_check(state: _State):
    """Read context file, detect drift vs init-time hash. Returns (ctx_obj, warnings)."""
    warnings = []
    ctx_path = Path(state.init["context_file_path"])
    ctx_text = _read_text_file(ctx_path)
    if _sha256_text(ctx_text) != state.init["context_file_sha256"]:
        warnings.append({
            "code": "context_file_hash_drift",
            "context": {"path": str(ctx_path),
                        "init_hash": state.init["context_file_sha256"],
                        "current_hash": _sha256_text(ctx_text)},
        })
    return json.loads(ctx_text), warnings


def _check_prior_synthesis_drift(state: _State):
    warnings = []
    if state.debate_type != "risk":
        return warnings
    ps_path = state.init.get("prior_synthesis_file_path")
    if not ps_path:
        return warnings
    p = Path(ps_path)
    if not p.exists():
        return warnings
    cur = _sha256_text(_read_text_file(p))
    if cur != state.init.get("prior_synthesis_sha256"):
        warnings.append({
            "code": "prior_synthesis_hash_drift",
            "context": {"path": str(p),
                        "init_hash": state.init.get("prior_synthesis_sha256"),
                        "current_hash": cur},
        })
    return warnings


def _persist_warnings(pad: Path, debate_id: str, warnings: list) -> None:
    for w in warnings:
        _append_pad(pad, {
            "ts": _now_iso_z(),
            "type": "debate_warning",
            "debate_id": debate_id,
            "code": w["code"],
            "context": w["context"],
        })


def _build_synthesis_vars(state: _State, ctx: dict) -> dict:
    """Variable bag for rendering a synthesis prompt.

    Concatenates all per-role arguments separated by '\\n\\n' so multi-round
    debates are visible in full.
    """
    vars_ = dict(ctx)
    vars_["ticker"] = state.init["ticker"]
    if state.debate_type == "risk":
        prior_path = state.init.get("prior_synthesis_file_path")
        if prior_path:
            try:
                vars_["prior_synthesis"] = _read_text_file(Path(prior_path))
            except OSError:
                vars_["prior_synthesis"] = "_(not provided)_"

    by_role = {}
    for t in state.turns:
        by_role.setdefault(t.speaker, []).append(t.argument)

    if state.debate_type == "research":
        vars_["bull_argument"] = "\n\n".join(by_role.get("Bull", [])) or "_(not provided)_"
        vars_["bear_argument"] = "\n\n".join(by_role.get("Bear", [])) or "_(not provided)_"
    elif state.debate_type == "risk":
        vars_["aggressive_argument"]   = "\n\n".join(by_role.get("Aggressive",   [])) or "_(not provided)_"
        vars_["conservative_argument"] = "\n\n".join(by_role.get("Conservative", [])) or "_(not provided)_"
        vars_["neutral_argument"]      = "\n\n".join(by_role.get("Neutral",      [])) or "_(not provided)_"
    return vars_


def _cmd_synthesize(args: argparse.Namespace, fmt: str, timer: Timer) -> int:
    pad = Path(args.pad)
    state, err = _replay_state(pad, args.debate_id)
    if err is not None:
        exit_code, code, ctx = err
        return emit_failure(exit_code, code.replace("_", " "), fmt,
                            code=code, context=ctx, timer=timer)
    if state.synthesized:
        return emit_failure(ExitCode.VALIDATION, "debate already synthesized", fmt,
                            code="debate_already_synthesized",
                            context={"debate_id": args.debate_id}, timer=timer)
    if len(state.turns) < state.bound:
        return emit_failure(ExitCode.VALIDATION,
                            "debate not ready for synthesis", fmt,
                            code="debate_not_ready_for_synthesis", context={
                                "debate_id": args.debate_id,
                                "current_turn": len(state.turns),
                                "required_turn": state.bound,
                            }, timer=timer)

    ctx_path = Path(state.init["context_file_path"])
    if not ctx_path.exists():
        return emit_failure(ExitCode.VALIDATION, "context file missing", fmt,
                            code="context_file_missing",
                            context={"path": str(ctx_path)}, timer=timer)
    ctx_obj, warnings = _read_context_with_drift_check(state)
    warnings.extend(_check_prior_synthesis_drift(state))

    speaker = SYNTH_SPEAKER[state.debate_type]
    vars_ = _build_synthesis_vars(state, ctx_obj)
    prompt = _render_prompt(speaker, vars_)

    final_round = state.round_for_turn(state.bound)
    data = {
        "debate_id": args.debate_id,
        "next_action": "done",
        "speaker": speaker,
        "round": final_round,
        "turn": state.bound,
        "prompt": prompt,
    }

    meta_extra = {"dry_run": True} if args.dry_run else None
    if warnings:
        meta_extra = meta_extra or {}
        meta_extra["warnings"] = warnings

    if args.dry_run:
        return emit_success(data, fmt, timer=timer, meta_extra=meta_extra)

    synth_record = {
        "ts": _now_iso_z(),
        "type": "debate_synthesis",
        "debate_id": args.debate_id,
        "synthesis_prompt_rendered": True,
    }
    write_err = _append_pad(pad, synth_record)
    if write_err is not None:
        return emit_failure(write_err, "failed to append synthesis record", fmt,
                            code="pad_write_failed", retryable=True,
                            context={"path": str(pad)}, timer=timer)
    if warnings:
        _persist_warnings(pad, args.debate_id, warnings)
    return emit_success(data, fmt, timer=timer, meta_extra=meta_extra)


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
