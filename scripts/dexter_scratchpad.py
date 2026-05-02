#!/usr/bin/env python3
"""JSONL scratchpad helper for daisy-financial-research.

Subcommands:
  init  — create a new scratchpad and emit its path
  add   — append a typed entry (plan / tool_result / thinking / calculation / validation / final)
  show  — replay a scratchpad

Agent-native conventions
  --format json|table   stdout shape; auto-JSON when stdout is not a TTY
  --schema              emit parameter schema, then exit
  Exit codes            0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency
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

DEFAULT_ROOT_NAME = "financial-research"
SUBDIR = "scratchpad"

ENTRY_TYPES = ["plan", "tool_result", "thinking", "calculation", "validation", "final"]

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

    args = p.parse_args()
    fmt = resolve_format(args.format)
    timer = Timer()

    if args.schema:
        return emit_schema(SCHEMA, fmt, timer=timer)

    if not args.cmd:
        return emit_failure(
            ExitCode.VALIDATION,
            "missing subcommand: choose one of init / add / show",
            fmt,
            code="validation_error",
            retryable=False,
            context={"valid_subcommands": ["init", "add", "show"]},
            timer=timer,
        )

    try:
        if args.cmd == "init":
            return cmd_init(args, fmt, timer)
        if args.cmd == "add":
            return cmd_add(args, fmt, timer)
        if args.cmd == "show":
            return cmd_show(args, fmt, timer)
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
