#!/usr/bin/env python3
"""Small JSONL scratchpad helper for Dexter-style Hermes finance research.

Usage:
  dexter_scratchpad.py init "original query"
  dexter_scratchpad.py add /path/to/file.jsonl tool_result tool_name=tushare.daily args='{"ts_code":"000001.SZ"}' result='rows=10'
  dexter_scratchpad.py show /path/to/file.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(os.path.expanduser("~/.hermes/reports/dexter-scratchpad"))


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


def cmd_init(args: argparse.Namespace) -> None:
    query = args.query
    h = hashlib.md5(query.encode("utf-8")).hexdigest()[:12]
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = BASE_DIR / f"{ts}_{h}.jsonl"
    append(path, {"type": "init", "timestamp": now_iso(), "query": query})
    print(path)


def cmd_add(args: argparse.Namespace) -> None:
    path = Path(args.path).expanduser()
    entry: dict[str, Any] = {"type": args.type, "timestamp": now_iso()}
    for item in args.kv:
        if "=" not in item:
            raise SystemExit(f"Invalid key=value item: {item}")
        key, value = item.split("=", 1)
        entry[key] = parse_value(value)
    append(path, entry)
    print(path)


def cmd_show(args: argparse.Namespace) -> None:
    path = Path(args.path).expanduser()
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            obj = json.loads(line)
            print(f"{i}: " + json.dumps(obj, ensure_ascii=False, indent=2))
        except Exception:
            print(f"{i}: {line}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("query")
    p_init.set_defaults(func=cmd_init)

    p_add = sub.add_parser("add")
    p_add.add_argument("path")
    p_add.add_argument("type", choices=["plan", "tool_result", "thinking", "calculation", "validation", "final"])
    p_add.add_argument("kv", nargs="*")
    p_add.set_defaults(func=cmd_add)

    p_show = sub.add_parser("show")
    p_show.add_argument("path")
    p_show.set_defaults(func=cmd_show)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
