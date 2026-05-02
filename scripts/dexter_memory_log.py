#!/usr/bin/env python3
"""Cross-session decision memory log for daisy-financial-research.

A single append-only Markdown file at <out-dir>/memory/decision-log.md
(default ./financial-research/memory/decision-log.md). Each entry has:

  [YYYY-MM-DD | ticker | rating | pending]            (initial)
  [YYYY-MM-DD | ticker | rating | +X.X% | +Y.Y% | Nd] (after resolve)

  DECISION:
  <thesis / reasoning written by the agent at decision time>

  REFLECTION:
  <lesson written when the call is resolved>

Entries are separated by `<!-- ENTRY_END -->`, an HTML-comment delimiter
LLMs will not emit accidentally. Updates are atomic (temp file + rename).

Subcommands
  record     append a new pending entry (idempotent — same date+ticker+pending is a no-op)
  resolve    replace the pending tag with realized returns and append REFLECTION
  list       structured listing with --status / --ticker / --since filters
  context    formatted past-context block for prompt injection at plan step
  stats      aggregate win-rate / mean-alpha / per-rating breakdown

Adapted from TradingAgents/tradingagents/agents/utils/memory.py — same on-disk
format, exposed via the daisy agent-native CLI envelope (--format / --schema /
--dry-run / structured exit codes).

Agent-native conventions
  --format json|table   stdout shape; auto-JSON when stdout is not a TTY
  --schema              emit parameter schema, then exit
  --dry-run             preview the change without writing
  Exit codes            0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import statistics
from pathlib import Path
from typing import Any

from _envelope import (
    ExitCode,
    Timer,
    add_common_args,
    emit_failure,
    emit_schema,
    emit_success,
    new_request_id,
    resolve_format,
)

DEFAULT_ROOT_NAME = "financial-research"
SUBDIR = "memory"
DEFAULT_LOG_FILENAME = "decision-log.md"

SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"

RATINGS = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]

DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nREFLECTION:|\Z)", re.DOTALL)
REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)$", re.DOTALL)

SCHEMA = {
    "name": "dexter_memory_log",
    "description": "Append-only Markdown decision log with pending → resolved lifecycle",
    "subcommands": {
        "record": {
            "description": "Append a new pending entry. Idempotent on (date, ticker).",
            "params": {
                "ticker": {"type": "string", "required": True, "example": "600519.SH"},
                "rating": {"type": "string", "enum": RATINGS, "required": True},
                "decision": {"type": "string", "required": True, "description": "Thesis / reasoning text"},
                "date": {"type": "string", "format": "YYYY-MM-DD or YYYYMMDD", "default": "today"},
                "log": {"type": "string", "description": "Override default log path"},
                "out_dir": {"type": "string", "default": "./financial-research"},
            },
            "returns": {"path": "log path", "entry_added": "bool", "skipped_reason": "duplicate or null"},
        },
        "resolve": {
            "description": "Mark a pending entry resolved with realized returns and a reflection.",
            "params": {
                "ticker": {"type": "string", "required": True},
                "date": {"type": "string", "format": "YYYY-MM-DD or YYYYMMDD", "required": True},
                "raw_return": {"type": "float", "required": True, "description": "Percent, e.g. 4.8 for +4.8%"},
                "alpha_return": {"type": "float", "required": True, "description": "Percent vs benchmark"},
                "holding_days": {"type": "integer", "required": True},
                "reflection": {"type": "string", "required": True, "description": "Lesson learned, 2-4 sentences"},
                "log": {"type": "string"},
                "out_dir": {"type": "string", "default": "./financial-research"},
            },
            "returns": {"path": "log path", "updated": "bool"},
        },
        "list": {
            "description": "Structured listing of entries with optional filters.",
            "params": {
                "status": {"type": "string", "enum": ["pending", "resolved", "all"], "default": "all"},
                "ticker": {"type": "string", "description": "Exact ts_code filter"},
                "since": {"type": "string", "format": "YYYY-MM-DD or YYYYMMDD"},
                "log": {"type": "string"},
                "out_dir": {"type": "string", "default": "./financial-research"},
            },
            "returns": {"path": "log path", "count": "int", "entries": "list of parsed entries"},
        },
        "context": {
            "description": "Past-context block for prompt injection (recent same-ticker + cross-ticker lessons).",
            "params": {
                "ticker": {"type": "string", "required": True},
                "n_same": {"type": "integer", "default": 5},
                "n_cross": {"type": "integer", "default": 3},
                "log": {"type": "string"},
                "out_dir": {"type": "string", "default": "./financial-research"},
            },
            "returns": {"context": "formatted text block", "n_same": "int", "n_cross": "int"},
        },
        "stats": {
            "description": "Aggregate stats: win rate, mean returns, per-rating breakdown.",
            "params": {
                "since": {"type": "string", "format": "YYYY-MM-DD or YYYYMMDD"},
                "log": {"type": "string"},
                "out_dir": {"type": "string", "default": "./financial-research"},
            },
            "returns": {
                "total": "int", "pending": "int", "resolved": "int",
                "win_rate": "fraction with raw_return > 0",
                "alpha_win_rate": "fraction with alpha_return > 0",
                "mean_raw_return_pct": "float", "mean_alpha_return_pct": "float",
                "by_rating": "dict",
            },
        },
    },
    "error_codes": ["validation_error", "no_data", "runtime_error"],
    "format": {
        "separator": "<!-- ENTRY_END -->",
        "tag_pending": "[YYYY-MM-DD | ticker | rating | pending]",
        "tag_resolved": "[YYYY-MM-DD | ticker | rating | +X.X% | +Y.Y% | Nd]",
        "ratings": RATINGS,
    },
}


# ----- helpers -----

def resolve_log_path(args_log: str | None, args_out_dir: str | None) -> Path:
    if args_log:
        return Path(args_log).expanduser()
    if args_out_dir:
        root = Path(args_out_dir).expanduser()
    else:
        root = Path.cwd() / DEFAULT_ROOT_NAME
    out = root / SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    return out / DEFAULT_LOG_FILENAME


def normalize_date(s: str | None) -> str:
    """Accept YYYYMMDD, YYYY-MM-DD, or empty (today). Return YYYY-MM-DD."""
    if not s:
        return dt.date.today().isoformat()
    s = s.strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    raise ValueError(f"unrecognized date: {s!r} (expected YYYY-MM-DD or YYYYMMDD)")


def read_log(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def parse_entry(raw: str) -> dict | None:
    lines = raw.strip().splitlines()
    if not lines:
        return None
    tag = lines[0].strip()
    if not (tag.startswith("[") and tag.endswith("]")):
        return None
    fields = [f.strip() for f in tag[1:-1].split("|")]
    if len(fields) < 4:
        return None
    pending = fields[3] == "pending"
    entry: dict[str, Any] = {
        "date": fields[0],
        "ticker": fields[1],
        "rating": fields[2],
        "pending": pending,
        "raw": None if pending else (fields[3] if len(fields) > 3 else None),
        "alpha": None if pending else (fields[4] if len(fields) > 4 else None),
        "holding": None if pending else (fields[5] if len(fields) > 5 else None),
    }
    body = "\n".join(lines[1:]).strip()
    d = DECISION_RE.search(body)
    r = REFLECTION_RE.search(body)
    entry["decision"] = d.group(1).strip() if d else ""
    entry["reflection"] = r.group(1).strip() if r else ""
    return entry


def load_entries(path: Path) -> list[dict]:
    text = read_log(path)
    if not text:
        return []
    out = []
    for raw in text.split(SEPARATOR):
        raw = raw.strip()
        if not raw:
            continue
        e = parse_entry(raw)
        if e:
            out.append(e)
    return out


def parse_pct(s: str | None) -> float | None:
    if s is None:
        return None
    m = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*%?$", s.strip())
    return float(m.group(1)) if m else None


def parse_holding(s: str | None) -> int | None:
    if s is None:
        return None
    m = re.match(r"^(\d+)\s*d?$", s.strip())
    return int(m.group(1)) if m else None


# ----- subcommands -----

def cmd_record(args, fmt, timer, request_id) -> int:
    try:
        date = normalize_date(args.date)
    except ValueError as e:
        return emit_failure(ExitCode.VALIDATION, str(e), fmt,
                            code="validation_error", retryable=False,
                            context={"value": args.date}, timer=timer, request_id=request_id)

    if args.rating not in RATINGS:
        return emit_failure(ExitCode.VALIDATION,
                            f"invalid rating: {args.rating!r} (expected one of {RATINGS})",
                            fmt, code="validation_error", retryable=False,
                            context={"value": args.rating, "allowed": RATINGS},
                            timer=timer, request_id=request_id)

    log = resolve_log_path(args.log, args.out_dir)
    pending_prefix = f"[{date} | {args.ticker} |"

    existing = read_log(log)
    for line in existing.splitlines():
        if line.startswith(pending_prefix) and line.endswith("| pending]"):
            return emit_success(
                {"path": str(log), "entry_added": False,
                 "skipped_reason": "duplicate_pending",
                 "date": date, "ticker": args.ticker},
                fmt, timer=timer, request_id=request_id,
                table_render=lambda: print(f"skipped (duplicate pending): {date} {args.ticker}"),
            )

    tag = f"[{date} | {args.ticker} | {args.rating} | pending]"
    body = f"{tag}\n\nDECISION:\n{args.decision.strip()}"

    if args.dry_run:
        return emit_success(
            {"dry_run": True, "would_append_to": str(log),
             "would_write_tag": tag, "decision_chars": len(args.decision)},
            fmt, timer=timer, request_id=request_id,
            table_render=lambda: print(f"would_append: {tag} → {log}"),
        )

    log.parent.mkdir(parents=True, exist_ok=True)
    if existing and not existing.endswith(SEPARATOR):
        # ensure separator before appending; safe even if file ends without one
        with log.open("a", encoding="utf-8") as f:
            f.write(SEPARATOR if existing.strip() else "")
            f.write(body)
            f.write(SEPARATOR)
    else:
        with log.open("a", encoding="utf-8") as f:
            f.write(body)
            f.write(SEPARATOR)

    return emit_success(
        {"path": str(log), "entry_added": True, "skipped_reason": None,
         "date": date, "ticker": args.ticker, "rating": args.rating, "tag": tag},
        fmt, timer=timer, request_id=request_id,
        table_render=lambda: print(f"recorded: {tag} → {log}"),
    )


def cmd_resolve(args, fmt, timer, request_id) -> int:
    try:
        date = normalize_date(args.date)
    except ValueError as e:
        return emit_failure(ExitCode.VALIDATION, str(e), fmt,
                            code="validation_error", retryable=False,
                            context={"value": args.date}, timer=timer, request_id=request_id)

    if args.holding_days <= 0:
        return emit_failure(ExitCode.VALIDATION,
                            f"--holding-days must be positive (got {args.holding_days})",
                            fmt, code="validation_error", retryable=False,
                            timer=timer, request_id=request_id)

    log = resolve_log_path(args.log, args.out_dir)
    if not log.exists():
        return emit_failure(ExitCode.NO_DATA, f"log not found: {log}",
                            fmt, code="no_data", retryable=False,
                            context={"path": str(log)}, timer=timer, request_id=request_id)

    text = read_log(log)
    blocks = text.split(SEPARATOR)
    pending_prefix = f"[{date} | {args.ticker} |"
    raw_pct = f"{args.raw_return:+.1f}%"
    alpha_pct = f"{args.alpha_return:+.1f}%"

    new_blocks: list[str] = []
    updated = False
    new_tag: str | None = None
    for block in blocks:
        s = block.strip()
        if not updated and s:
            lines = s.splitlines()
            tag_line = lines[0].strip()
            if tag_line.startswith(pending_prefix) and tag_line.endswith("| pending]"):
                fields = [f.strip() for f in tag_line[1:-1].split("|")]
                rating = fields[2] if len(fields) > 2 else "Hold"
                new_tag = (
                    f"[{date} | {args.ticker} | {rating}"
                    f" | {raw_pct} | {alpha_pct} | {args.holding_days}d]"
                )
                rest = "\n".join(lines[1:]).lstrip()
                rebuilt = f"{new_tag}\n\n{rest}\n\nREFLECTION:\n{args.reflection.strip()}"
                new_blocks.append(rebuilt)
                updated = True
                continue
        new_blocks.append(block)

    if not updated:
        return emit_failure(ExitCode.NO_DATA,
                            f"no pending entry found for {date} {args.ticker}",
                            fmt, code="no_data", retryable=False,
                            context={"date": date, "ticker": args.ticker, "path": str(log)},
                            timer=timer, request_id=request_id)

    if args.dry_run:
        return emit_success(
            {"dry_run": True, "would_update": str(log),
             "new_tag": new_tag, "raw_return_pct": args.raw_return,
             "alpha_return_pct": args.alpha_return, "holding_days": args.holding_days},
            fmt, timer=timer, request_id=request_id,
            table_render=lambda: print(f"would_update: {new_tag} → {log}"),
        )

    atomic_write(log, SEPARATOR.join(new_blocks))
    return emit_success(
        {"path": str(log), "updated": True, "new_tag": new_tag,
         "raw_return_pct": args.raw_return, "alpha_return_pct": args.alpha_return,
         "holding_days": args.holding_days},
        fmt, timer=timer, request_id=request_id,
        table_render=lambda: print(f"resolved: {new_tag} → {log}"),
    )


def cmd_list(args, fmt, timer, request_id) -> int:
    log = resolve_log_path(args.log, args.out_dir)
    entries = load_entries(log)

    if args.since:
        try:
            since = normalize_date(args.since)
        except ValueError as e:
            return emit_failure(ExitCode.VALIDATION, str(e), fmt,
                                code="validation_error", retryable=False,
                                context={"value": args.since}, timer=timer, request_id=request_id)
        entries = [e for e in entries if e["date"] >= since]

    if args.ticker:
        entries = [e for e in entries if e["ticker"] == args.ticker]

    if args.status == "pending":
        entries = [e for e in entries if e["pending"]]
    elif args.status == "resolved":
        entries = [e for e in entries if not e["pending"]]

    def table() -> None:
        if not entries:
            print(f"(no entries) {log}")
            return
        for e in entries:
            tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | "
            tag += "pending]" if e["pending"] else f"{e['raw']} | {e['alpha']} | {e['holding']}]"
            print(tag)

    return emit_success(
        {"path": str(log), "count": len(entries), "entries": entries},
        fmt, timer=timer, request_id=request_id, table_render=table,
    )


def format_full(e: dict) -> str:
    raw = e["raw"] or "n/a"
    alpha = e["alpha"] or "n/a"
    holding = e["holding"] or "n/a"
    tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {raw} | {alpha} | {holding}]"
    parts = [tag, f"DECISION:\n{e['decision']}"]
    if e["reflection"]:
        parts.append(f"REFLECTION:\n{e['reflection']}")
    return "\n\n".join(parts)


def format_reflection_only(e: dict) -> str:
    raw = e["raw"] or "n/a"
    tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {raw}]"
    if e["reflection"]:
        return f"{tag}\n{e['reflection']}"
    snippet = e["decision"][:300]
    suffix = "..." if len(e["decision"]) > 300 else ""
    return f"{tag}\n{snippet}{suffix}"


def cmd_context(args, fmt, timer, request_id) -> int:
    log = resolve_log_path(args.log, args.out_dir)
    resolved = [e for e in load_entries(log) if not e["pending"]]
    same: list[dict] = []
    cross: list[dict] = []
    for e in reversed(resolved):
        if len(same) >= args.n_same and len(cross) >= args.n_cross:
            break
        if e["ticker"] == args.ticker and len(same) < args.n_same:
            same.append(e)
        elif e["ticker"] != args.ticker and len(cross) < args.n_cross:
            cross.append(e)

    parts: list[str] = []
    if same:
        parts.append(f"Past analyses of {args.ticker} (most recent first):")
        parts.extend(format_full(e) for e in same)
    if cross:
        parts.append("Recent cross-ticker lessons:")
        parts.extend(format_reflection_only(e) for e in cross)
    block = "\n\n".join(parts)

    return emit_success(
        {"path": str(log), "ticker": args.ticker, "n_same": len(same),
         "n_cross": len(cross), "context": block},
        fmt, timer=timer, request_id=request_id,
        table_render=lambda: print(block if block else f"(no past entries) {log}"),
    )


def cmd_stats(args, fmt, timer, request_id) -> int:
    log = resolve_log_path(args.log, args.out_dir)
    entries = load_entries(log)
    if args.since:
        try:
            since = normalize_date(args.since)
        except ValueError as e:
            return emit_failure(ExitCode.VALIDATION, str(e), fmt,
                                code="validation_error", retryable=False,
                                context={"value": args.since}, timer=timer, request_id=request_id)
        entries = [e for e in entries if e["date"] >= since]

    pending = [e for e in entries if e["pending"]]
    resolved = [e for e in entries if not e["pending"]]
    raws = [parse_pct(e["raw"]) for e in resolved if parse_pct(e["raw"]) is not None]
    alphas = [parse_pct(e["alpha"]) for e in resolved if parse_pct(e["alpha"]) is not None]

    by_rating: dict[str, dict] = {}
    for e in resolved:
        bucket = by_rating.setdefault(e["rating"], {"count": 0, "alphas": []})
        bucket["count"] += 1
        a = parse_pct(e["alpha"])
        if a is not None:
            bucket["alphas"].append(a)
    by_rating_out = {
        r: {
            "count": v["count"],
            "mean_alpha_pct": round(statistics.fmean(v["alphas"]), 2) if v["alphas"] else None,
            "alpha_win_rate": round(sum(1 for x in v["alphas"] if x > 0) / len(v["alphas"]), 3)
                if v["alphas"] else None,
        }
        for r, v in by_rating.items()
    }

    win_rate = round(sum(1 for x in raws if x > 0) / len(raws), 3) if raws else None
    alpha_win = round(sum(1 for x in alphas if x > 0) / len(alphas), 3) if alphas else None
    mean_raw = round(statistics.fmean(raws), 2) if raws else None
    mean_alpha = round(statistics.fmean(alphas), 2) if alphas else None

    data = {
        "path": str(log),
        "total": len(entries),
        "pending": len(pending),
        "resolved": len(resolved),
        "win_rate": win_rate,
        "alpha_win_rate": alpha_win,
        "mean_raw_return_pct": mean_raw,
        "mean_alpha_return_pct": mean_alpha,
        "by_rating": by_rating_out,
    }

    def table() -> None:
        print(f"path: {log}")
        print(f"total={len(entries)}  pending={len(pending)}  resolved={len(resolved)}")
        if resolved:
            print(f"win_rate={win_rate}  alpha_win_rate={alpha_win}")
            print(f"mean_raw_return_pct={mean_raw}  mean_alpha_return_pct={mean_alpha}")
            for r, v in by_rating_out.items():
                print(f"  {r}: count={v['count']} mean_alpha%={v['mean_alpha_pct']} alpha_win={v['alpha_win_rate']}")

    return emit_success(data, fmt, timer=timer, request_id=request_id, table_render=table)


# ----- main -----

def main() -> int:
    p = argparse.ArgumentParser(
        description="Cross-session decision memory log for daisy-financial-research",
        epilog="Exit codes: 0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency",
    )
    add_common_args(p)
    sub = p.add_subparsers(dest="cmd")

    def _shared(sp):
        sp.add_argument("--log", help="Override log path (default <out-dir>/memory/decision-log.md)")
        sp.add_argument("--out-dir", dest="out_dir", default=None,
                        help="Output root; default <cwd>/financial-research/")
        add_common_args(sp)

    p_rec = sub.add_parser("record", help="Append a new pending entry")
    p_rec.add_argument("--ticker", required=True)
    p_rec.add_argument("--rating", required=True, choices=RATINGS)
    p_rec.add_argument("--decision", required=True, help="Thesis / reasoning text")
    p_rec.add_argument("--date", help="YYYY-MM-DD or YYYYMMDD; default today")
    _shared(p_rec)

    p_res = sub.add_parser("resolve", help="Resolve a pending entry with realized returns")
    p_res.add_argument("--ticker", required=True)
    p_res.add_argument("--date", required=True, help="Date of the pending entry to resolve")
    p_res.add_argument("--raw-return", dest="raw_return", type=float, required=True,
                       help="Realized return in percent, e.g. 4.8 for +4.8%%")
    p_res.add_argument("--alpha-return", dest="alpha_return", type=float, required=True,
                       help="Excess return vs benchmark in percent")
    p_res.add_argument("--holding-days", dest="holding_days", type=int, required=True)
    p_res.add_argument("--reflection", required=True, help="Lesson learned, 2-4 sentences")
    _shared(p_res)

    p_list = sub.add_parser("list", help="List entries with optional filters")
    p_list.add_argument("--status", choices=["pending", "resolved", "all"], default="all")
    p_list.add_argument("--ticker")
    p_list.add_argument("--since")
    _shared(p_list)

    p_ctx = sub.add_parser("context", help="Past-context block for prompt injection")
    p_ctx.add_argument("--ticker", required=True)
    p_ctx.add_argument("--n-same", dest="n_same", type=int, default=5)
    p_ctx.add_argument("--n-cross", dest="n_cross", type=int, default=3)
    _shared(p_ctx)

    p_stats = sub.add_parser("stats", help="Aggregate stats")
    p_stats.add_argument("--since")
    _shared(p_stats)

    args = p.parse_args()
    fmt = resolve_format(args.format)
    timer = Timer()
    request_id = new_request_id()

    if args.schema:
        return emit_schema(SCHEMA, fmt, timer=timer)

    if not args.cmd:
        return emit_failure(
            ExitCode.VALIDATION,
            "missing subcommand: choose one of record / resolve / list / context / stats",
            fmt, code="validation_error", retryable=False,
            context={"valid_subcommands": ["record", "resolve", "list", "context", "stats"]},
            timer=timer, request_id=request_id,
        )

    try:
        if args.cmd == "record":
            return cmd_record(args, fmt, timer, request_id)
        if args.cmd == "resolve":
            return cmd_resolve(args, fmt, timer, request_id)
        if args.cmd == "list":
            return cmd_list(args, fmt, timer, request_id)
        if args.cmd == "context":
            return cmd_context(args, fmt, timer, request_id)
        if args.cmd == "stats":
            return cmd_stats(args, fmt, timer, request_id)
    except Exception as e:
        return emit_failure(
            ExitCode.RUNTIME, f"{type(e).__name__}: {e}",
            fmt, code="runtime_error", retryable=False,
            context={"subcommand": args.cmd}, timer=timer, request_id=request_id,
        )

    return ExitCode.RUNTIME


if __name__ == "__main__":
    raise SystemExit(main())
