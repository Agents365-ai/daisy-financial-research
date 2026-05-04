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
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from _envelope import (  # noqa: E402
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
        parser.print_help(sys.stderr)
        return ExitCode.VALIDATION
    handlers = {
        "init": _cmd_init,
        "next": _cmd_next,
        "synthesize": _cmd_synthesize,
    }
    return handlers[args.cmd](args, fmt, timer)


if __name__ == "__main__":
    sys.exit(main())
