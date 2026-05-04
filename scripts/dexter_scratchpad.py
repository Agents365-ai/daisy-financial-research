#!/usr/bin/env python3
"""JSONL scratchpad helper for daisy-financial-research.

Subcommands:
  init      — create a new scratchpad and emit its path
  add       — append a typed entry (plan / tool_result / thinking / calculation /
              validation / debate_turn / final)
  show      — replay a scratchpad
  can-call  — soft loop-limit + query-similarity guard before a tool call;
              always allows (returns allowed=True) but emits a warning when the
              tool has already been called >= max_calls times or when a similar
              query appears in the scratchpad. Ported from
              virattt/dexter:src/agent/scratchpad.ts canCallTool.

Agent-native conventions
  --format json|table   stdout shape; auto-JSON when stdout is not a TTY
  --schema              emit parameter schema, then exit
  Exit codes            0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
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

DEFAULT_ROOT_NAME = "financial-research"
SUBDIR = "scratchpad"

ENTRY_TYPES = ["plan", "tool_result", "thinking", "calculation", "validation", "debate_turn", "final"]

SCHEMA = {
    "name": "dexter_scratchpad",
    "description": "Append-only JSONL scratchpad for finance research sessions",
    "subcommands": {
        "init": {
            "description": "Create a new scratchpad file",
            "params": {
                "query": {"type": "string", "required": True, "description": "Original user query"},
                "out_dir": {"type": "string", "default": "./financial-research", "description": "Output root; scratchpad/ subdir auto-appended"},
            },
            "returns": {"path": "absolute path to created .jsonl"},
        },
        "add": {
            "description": "Append a typed entry to an existing scratchpad",
            "params": {
                "path": {"type": "string", "required": True},
                "type": {"type": "string", "enum": ENTRY_TYPES, "required": True},
                "kv": {"type": "list<string>", "description": "key=value pairs; values are JSON-decoded if possible"},
            },
            "returns": {"path": "absolute path of the scratchpad"},
        },
        "show": {
            "description": "Replay a scratchpad file",
            "params": {"path": {"type": "string", "required": True}},
            "returns": {"entries": "list of parsed JSONL records"},
        },
        "can-call": {
            "description": ("Soft loop-limit + query-similarity guard. Always "
                            "returns allowed=True (this is a warning, not a "
                            "block) but flags when a tool has been used "
                            ">= max_calls times in this scratchpad, or when "
                            "the new query is textually similar to a prior "
                            "query for the same tool."),
            "params": {
                "path": {"type": "string", "required": True},
                "tool": {"type": "string", "required": True,
                          "description": "Substring-matched against entry.tool_name"},
                "query": {"type": "string", "required": True,
                           "description": "The new query / args you're about to send"},
                "max_calls": {"type": "integer", "default": 3,
                               "description": "Warn when prior call count >= this"},
                "similarity_threshold": {"type": "number", "default": 0.7,
                                          "description": "difflib.SequenceMatcher.ratio() threshold (0..1)"},
            },
            "returns": {
                "allowed": "always true (soft warning, not a hard block)",
                "warning": "human-readable warning string or null",
                "current_count": "int — prior calls to the same tool in this pad",
                "max_calls": "int — the threshold actually used",
                "similar_to": "list of up to 3 {tool_name, text, similarity, timestamp} sorted desc",
                "similarity_threshold": "float",
            },
        },
    },
    "error_codes": ["validation_error", "no_data", "runtime_error"],
}


def resolve_out_dir(arg_out_dir: str | None) -> Path:
    """Return <root>/scratchpad, where <root> defaults to cwd/financial-research."""
    if arg_out_dir:
        root = Path(arg_out_dir).expanduser()
    else:
        root = Path.cwd() / DEFAULT_ROOT_NAME
    out = root / SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    return out


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_value(value: str) -> Any:
    value = value.strip()
    if not value:
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def append(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def cmd_init(args: argparse.Namespace, fmt: str, timer: Timer) -> int:
    query = args.query
    h = hashlib.md5(query.encode("utf-8")).hexdigest()[:12]
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = resolve_out_dir(args.out_dir)
    path = out_dir / f"{ts}_{h}.jsonl"

    if args.dry_run:
        return emit_success(
            {"dry_run": True, "would_create": str(path), "query": query},
            fmt, timer=timer,
            table_render=lambda: print(f"would_create: {path}"),
        )

    append(path, {"type": "init", "timestamp": now_iso(), "query": query})
    return emit_success(
        {"path": str(path), "query": query},
        fmt, timer=timer,
        table_render=lambda: print(path),
    )


def cmd_add(args: argparse.Namespace, fmt: str, timer: Timer) -> int:
    path = Path(args.path).expanduser()
    entry: dict[str, Any] = {"type": args.type, "timestamp": now_iso()}
    for item in args.kv:
        if "=" not in item:
            return emit_failure(
                ExitCode.VALIDATION,
                f"Invalid key=value item: {item!r}",
                fmt,
                code="validation_error",
                retryable=False,
                context={"item": item, "expected": "key=value"},
                timer=timer,
            )
        key, value = item.split("=", 1)
        entry[key] = parse_value(value)

    if args.dry_run:
        return emit_success(
            {"dry_run": True, "would_append_to": str(path), "entry": entry},
            fmt, timer=timer,
            table_render=lambda: print(f"would_append_to: {path}"),
        )

    append(path, entry)
    return emit_success(
        {"path": str(path), "entry": entry},
        fmt, timer=timer,
        table_render=lambda: print(path),
    )


def cmd_show(args: argparse.Namespace, fmt: str, timer: Timer) -> int:
    path = Path(args.path).expanduser()
    if not path.exists():
        return emit_failure(
            ExitCode.NO_DATA,
            f"Scratchpad not found: {path}",
            fmt,
            code="no_data",
            retryable=False,
            context={"path": str(path)},
            timer=timer,
        )

    entries: list[dict[str, Any]] = []
    raw_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw_lines.append(line)
        try:
            entries.append(json.loads(line))
        except Exception:
            entries.append({"_unparseable": line})

    def table() -> None:
        for i, line in enumerate(raw_lines, 1):
            try:
                obj = json.loads(line)
                print(f"{i}: " + json.dumps(obj, ensure_ascii=False, indent=2))
            except Exception:
                print(f"{i}: {line}")

    return emit_success(
        {"path": str(path), "count": len(entries), "entries": entries},
        fmt, timer=timer,
        table_render=table,
    )


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize(s: str) -> str:
    """Lowercase + collapse non-alphanumeric runs to a single space."""
    return _NORMALIZE_RE.sub(" ", s.lower()).strip()


def _entry_searchable_text(entry: dict[str, Any]) -> str:
    """Concatenate all non-metadata string-ish fields into one comparable blob."""
    skip = {"type", "timestamp"}
    parts: list[str] = []
    for k, v in entry.items():
        if k in skip:
            continue
        if v is None:
            continue
        parts.append(str(v))
    return " ".join(parts)


def _load_jsonl_entries(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue
    return out


def cmd_can_call(args: argparse.Namespace, fmt: str, timer: Timer) -> int:
    path = Path(args.path).expanduser()
    if not path.exists():
        return emit_failure(
            ExitCode.NO_DATA,
            f"Scratchpad not found: {path}",
            fmt, code="no_data", retryable=False,
            context={"path": str(path),
                     "hint": "create one with `dexter_scratchpad.py init <query>`"},
            timer=timer,
        )

    if args.max_calls < 1:
        return emit_failure(
            ExitCode.VALIDATION,
            f"--max-calls must be >= 1 (got {args.max_calls})",
            fmt, code="validation_error", retryable=False,
            context={"max_calls": args.max_calls}, timer=timer,
        )
    if not (0.0 <= args.similarity_threshold <= 1.0):
        return emit_failure(
            ExitCode.VALIDATION,
            f"--similarity-threshold must be in [0, 1] (got {args.similarity_threshold})",
            fmt, code="validation_error", retryable=False,
            context={"similarity_threshold": args.similarity_threshold}, timer=timer,
        )

    tool_query = args.tool.strip()
    if not tool_query:
        return emit_failure(
            ExitCode.VALIDATION, "tool argument cannot be empty", fmt,
            code="validation_error", retryable=False,
            context={"tool": args.tool}, timer=timer,
        )

    new_query_norm = _normalize(args.query)
    entries = _load_jsonl_entries(path)

    # A "tool call" entry is one whose tool_name field substring-matches the
    # tool argument. The convention from SKILL.md §1 is
    # `add ... tool_result tool_name='X'`.
    tool_calls: list[dict[str, Any]] = []
    for e in entries:
        name = e.get("tool_name")
        if not isinstance(name, str):
            continue
        if tool_query.lower() in name.lower():
            tool_calls.append(e)

    # Similarity scan against every prior call's searchable text.
    similar: list[dict[str, Any]] = []
    for e in tool_calls:
        text = _entry_searchable_text(e)
        text_norm = _normalize(text)
        if not text_norm or not new_query_norm:
            continue
        ratio = difflib.SequenceMatcher(None, new_query_norm, text_norm).ratio()
        if ratio >= args.similarity_threshold:
            similar.append({
                "tool_name": e.get("tool_name"),
                "text": text[:200],
                "similarity": round(ratio, 3),
                "timestamp": e.get("timestamp"),
            })
    similar.sort(key=lambda s: s["similarity"], reverse=True)
    similar = similar[:3]

    parts: list[str] = []
    if len(tool_calls) >= args.max_calls:
        parts.append(
            f"{tool_query!r} has been called {len(tool_calls)} times "
            f"(>= max_calls={args.max_calls}); consider changing strategy"
        )
    if similar:
        top = similar[0]
        parts.append(
            f"new query is similar (ratio={top['similarity']}) to a prior "
            f"call at {top['timestamp']}: {top['text'][:80]!r}"
        )
    warning = "; ".join(parts) if parts else None

    data = {
        "allowed": True,
        "warning": warning,
        "current_count": len(tool_calls),
        "max_calls": args.max_calls,
        "similarity_threshold": args.similarity_threshold,
        "similar_to": similar,
        "tool": tool_query,
    }

    def table() -> None:
        flag = "OK" if warning is None else "WARN"
        print(f"[{flag}] tool={tool_query!r}  prior_calls={len(tool_calls)}/{args.max_calls}")
        if warning:
            print(f"  warning: {warning}")
        for s in similar:
            print(f"  similar (ratio={s['similarity']}): {s['text'][:100]!r}")

    return emit_success(data, fmt, timer=timer, table_render=table)


def main() -> int:
    p = argparse.ArgumentParser(
        description="JSONL scratchpad for daisy-financial-research",
        epilog="Exit codes: 0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency",
    )
    add_common_args(p)
    sub = p.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init", help="Create a new scratchpad")
    p_init.add_argument("query")
    p_init.add_argument("--out-dir", dest="out_dir", default=None,
                        help="Output root; default <cwd>/financial-research/")
    add_common_args(p_init)

    p_add = sub.add_parser("add", help="Append an entry to a scratchpad")
    p_add.add_argument("path")
    p_add.add_argument("type", choices=ENTRY_TYPES)
    p_add.add_argument("kv", nargs="*")
    add_common_args(p_add)

    p_show = sub.add_parser("show", help="Replay a scratchpad")
    p_show.add_argument("path")
    add_common_args(p_show)

    p_cc = sub.add_parser("can-call",
                           help="Soft loop-limit + similarity guard before a tool call")
    p_cc.add_argument("path")
    p_cc.add_argument("tool", help="Substring-matched against entry.tool_name")
    p_cc.add_argument("query", help="The query / args you're about to send")
    p_cc.add_argument("--max-calls", dest="max_calls", type=int, default=3,
                      help="Warn when prior call count >= this (default 3)")
    p_cc.add_argument("--similarity-threshold", dest="similarity_threshold",
                      type=float, default=0.7,
                      help="difflib ratio threshold in [0, 1] (default 0.7)")
    add_common_args(p_cc)

    args = p.parse_args()
    fmt = resolve_format(args.format)
    timer = Timer()

    if args.schema:
        return emit_schema(SCHEMA, fmt, timer=timer)

    valid_subs = ["init", "add", "show", "can-call"]
    if not args.cmd:
        return emit_failure(
            ExitCode.VALIDATION,
            "missing subcommand: choose one of " + " / ".join(valid_subs),
            fmt,
            code="validation_error",
            retryable=False,
            context={"valid_subcommands": valid_subs},
            timer=timer,
        )

    try:
        if args.cmd == "init":
            return cmd_init(args, fmt, timer)
        if args.cmd == "add":
            return cmd_add(args, fmt, timer)
        if args.cmd == "show":
            return cmd_show(args, fmt, timer)
        if args.cmd == "can-call":
            return cmd_can_call(args, fmt, timer)
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

    return ExitCode.RUNTIME


if __name__ == "__main__":
    raise SystemExit(main())
