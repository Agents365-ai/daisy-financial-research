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
  backtest   risk-adjusted decision-level metrics across a date window

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
import os
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
        "backtest": {
            "description": (
                "Risk-adjusted decision-level metrics across a date window. "
                "Computes per-rating mean / hit-rate / t-stat on alpha plus an "
                "annualized-alpha figure, Sortino-flavored ratio, and the max "
                "drawdown of the cumulative-alpha curve. NOT a portfolio Sharpe "
                "ratio — daisy logs decisions, not a continuous NAV. The metric "
                "names make this explicit (alpha_t_stat, annualized_alpha_pct, "
                "max_cum_alpha_drawdown_pct)."
            ),
            "params": {
                "from_": {"type": "string", "format": "YYYY-MM-DD or YYYYMMDD",
                           "description": "Window start; default = earliest resolved entry"},
                "to": {"type": "string", "format": "YYYY-MM-DD or YYYYMMDD",
                       "description": "Window end; default = latest resolved entry"},
                "rating": {"type": "string", "enum": RATINGS,
                           "description": "Optional filter to a single rating bucket"},
                "log": {"type": "string"},
                "out_dir": {"type": "string", "default": "./financial-research"},
            },
            "returns": {
                "path": "log path",
                "window": "{from, to, auto_derived}",
                "n_resolved": "int",
                "by_rating": "{Buy: {count, mean_raw_pct, raw_hit_rate, mean_alpha_pct, alpha_hit_rate, alpha_t_stat, mean_holding_days, annualized_alpha_pct}, ...}",
                "overall": "same metric set across all included entries plus cumulative-alpha drawdown",
                "cumulative_alpha_curve": "list of {date, cum_alpha_pct} sorted by date",
            },
        },
        "compute-returns": {
            "description": "Fetch close[decision_date] / close[as_of_date] / benchmark and compute raw + alpha. No log mutation.",
            "params": {
                "ticker": {"type": "string", "required": True, "example": "600519.SH"},
                "date": {"type": "string", "required": True, "description": "Decision date"},
                "as_of": {"type": "string", "default": "today"},
                "benchmark": {"type": "string", "description": "Override default benchmark ts_code"},
            },
            "returns": {
                "decision_date": "YYYY-MM-DD",
                "as_of_date": "YYYY-MM-DD",
                "decision_close": "float",
                "as_of_close": "float",
                "raw_return_pct": "float",
                "benchmark_ts_code": "string or null",
                "benchmark_return_pct": "float or null",
                "alpha_return_pct": "float or null",
                "holding_days": "calendar days",
                "data_source": "string",
            },
        },
        "auto-resolve": {
            "description": "compute-returns + resolve in one call. Closes the resolve loop.",
            "params": {
                "ticker": {"type": "string", "required": True},
                "date": {"type": "string", "required": True, "description": "Decision date of the pending entry to resolve"},
                "reflection": {"type": "string", "required": True, "description": "Lesson learned (use references/reflection-prompt.md template)"},
                "as_of": {"type": "string", "default": "today"},
                "benchmark": {"type": "string", "description": "Override default benchmark ts_code"},
            },
            "returns": {
                "computed": "compute-returns output",
                "updated": "bool",
                "new_tag": "resolved tag line",
            },
        },
    },
    "benchmarks": {
        "a_share": "000300.SH (CSI 300, via pro.index_daily)",
        "hk": "HSI.HK (Hang Seng Index, via pro.index_global)",
        "us": "SPY (via yfinance, optional dep)",
    },
    "error_codes": ["validation_error", "no_data", "runtime_error", "auth_missing", "dependency_missing"],
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


# ----- market routing + close-price fetchers -----

A_SHARE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
HK_RE = re.compile(r"^\d{4,5}\.HK$")

DEFAULT_BENCHMARK = {
    "a_share": "000300.SH",  # CSI 300
    "hk": "HSI.HK",
    "us": "SPY",
}


def detect_market(ts_code: str) -> str:
    """Return 'a_share' / 'hk' / 'us'. Raises ValueError on unrecognized patterns."""
    s = ts_code.strip().upper()
    if A_SHARE_RE.match(s):
        return "a_share"
    if HK_RE.match(s):
        return "hk"
    if re.match(r"^[A-Z\.\^\-]+$", s):
        return "us"
    raise ValueError(
        f"unrecognized ts_code pattern: {ts_code!r} "
        "(expected NNNNNN.SH/SZ/BJ, NNNNN.HK, or US-style symbol like AAPL)"
    )


def to_iso_date(s: str) -> str:
    """Normalize YYYYMMDD or YYYY-MM-DD to YYYY-MM-DD."""
    return normalize_date(s)


def to_yyyymmdd(s: str) -> str:
    """Normalize YYYY-MM-DD to YYYYMMDD."""
    iso = normalize_date(s)
    return iso.replace("-", "")


def import_tushare():
    try:
        import tushare as ts
        return ts
    except ImportError as e:
        raise RuntimeError(f"tushare not installed: {e}") from e


def import_yfinance():
    try:
        import yfinance as yf
        return yf
    except ImportError as e:
        raise RuntimeError(f"yfinance not installed: {e}") from e


def _date_window(target_iso: str, days: int, direction: str) -> tuple[str, str]:
    """Return (start_yyyymmdd, end_yyyymmdd) for searching trading days around target.

    direction='forward': window is [target, target+days]
    direction='backward': window is [target-days, target]
    """
    target = dt.datetime.strptime(target_iso, "%Y-%m-%d").date()
    if direction == "forward":
        start, end = target, target + dt.timedelta(days=days)
    else:
        start, end = target - dt.timedelta(days=days), target
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _pick_close(df, direction: str) -> tuple[float, str] | tuple[None, None]:
    """Pick the closest trading day's close from a Tushare df.

    direction='forward' → earliest (first trading day on/after target).
    direction='backward' → latest (last trading day on/before target).
    Returns (close, trade_date) or (None, None).
    """
    if df is None or len(df) == 0:
        return None, None
    df = df.sort_values("trade_date", ascending=(direction == "forward"))
    row = df.iloc[0]
    return float(row["close"]), str(row["trade_date"])


def fetch_tushare_close(pro, endpoint: str, ts_code: str, target_iso: str,
                        direction: str, search_days: int = 14):
    """Fetch close on or around target_iso from a Tushare daily endpoint.

    endpoint ∈ {'daily', 'hk_daily', 'index_daily', 'index_global'}.
    Walks forward (decision date) or backward (as-of date) by up to search_days.
    Returns (close, trade_date_yyyymmdd) or (None, None).
    """
    start, end = _date_window(target_iso, search_days, direction)
    fn = getattr(pro, endpoint)
    try:
        df = fn(ts_code=ts_code, start_date=start, end_date=end)
    except Exception:
        return None, None
    return _pick_close(df, direction)


def fetch_close_for_market(pro, ts_code: str, target_iso: str,
                           direction: str, market: str | None = None):
    """Fetch close for a stock ts_code, routing by market. Returns (close, trade_date_yyyymmdd)."""
    market = market or detect_market(ts_code)
    if market == "a_share":
        return fetch_tushare_close(pro, "daily", ts_code, target_iso, direction)
    if market == "hk":
        return fetch_tushare_close(pro, "hk_daily", ts_code, target_iso, direction)
    if market == "us":
        # yfinance handles US directly; lazy import
        return _fetch_yfinance_close(ts_code, target_iso, direction)
    raise ValueError(f"unsupported market: {market}")


def fetch_benchmark_close(pro, benchmark_ts_code: str, target_iso: str, direction: str):
    """Fetch benchmark close. Tries Tushare index_daily first (A-share indexes),
    falls back to index_global, then hk_daily, then AKShare's Sina HK index
    daily for HSI-style codes (closes the HK benchmark gap)."""
    for endpoint in ("index_daily", "index_global", "hk_daily"):
        c, td = fetch_tushare_close(pro, endpoint, benchmark_ts_code, target_iso, direction)
        if c is not None:
            return c, td, f"tushare:pro.{endpoint}"

    # Final fallback: AKShare Sina HK index daily — handles HSI when Tushare's
    # HK index endpoints aren't available in the user's plan.
    if "HSI" in benchmark_ts_code.upper() or "HSCEI" in benchmark_ts_code.upper():
        c, td = _fetch_akshare_hk_index_close(benchmark_ts_code, target_iso, direction)
        if c is not None:
            return c, td, "akshare:stock_hk_index_daily_sina"
    return None, None, None


def _fetch_akshare_hk_index_close(benchmark_ts_code: str, target_iso: str,
                                  direction: str, search_days: int = 14):
    """AKShare Sina HK index fallback. Lazy-imported; silent if akshare absent."""
    try:
        import akshare as ak
    except ImportError:
        return None, None
    # Strip a trailing ".HK" if present and uppercase
    symbol = benchmark_ts_code.split(".")[0].upper()
    try:
        df = ak.stock_hk_index_daily_sina(symbol=symbol)
    except Exception:
        return None, None
    if df is None or len(df) == 0 or "close" not in df.columns or "date" not in df.columns:
        return None, None
    target = dt.datetime.strptime(target_iso, "%Y-%m-%d").date()
    # df["date"] is a Series of date strings or Timestamps
    df = df.copy()
    df["_d"] = df["date"].astype(str).str[:10]
    if direction == "forward":
        mask = df["_d"] >= target_iso
        # earliest on or after target
        sub = df[mask].sort_values("_d", ascending=True).head(search_days)
    else:
        mask = df["_d"] <= target_iso
        sub = df[mask].sort_values("_d", ascending=False).head(search_days)
    if len(sub) == 0:
        return None, None
    row = sub.iloc[0]
    return float(row["close"]), str(row["_d"]).replace("-", "")


def _fetch_yfinance_close(symbol: str, target_iso: str, direction: str,
                          search_days: int = 14):
    """yfinance fallback for US tickers and SPY benchmark."""
    yf = import_yfinance()
    target = dt.datetime.strptime(target_iso, "%Y-%m-%d").date()
    if direction == "forward":
        start, end = target, target + dt.timedelta(days=search_days)
    else:
        start, end = target - dt.timedelta(days=search_days), target + dt.timedelta(days=1)
    try:
        df = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                         progress=False, auto_adjust=False)
    except Exception:
        return None, None
    if df is None or len(df) == 0:
        return None, None
    df = df.sort_index(ascending=(direction == "forward"))
    row = df.iloc[0]
    close = float(row["Close"]) if hasattr(row["Close"], "item") is False else float(row["Close"].iloc[0])
    trade_date = df.index[0].strftime("%Y%m%d")
    return close, trade_date


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


# ----- backtest -----

def _annualize_alpha(alpha_pct: float, holding_days: int) -> float | None:
    """Linear-time annualization: alpha_pct * (365 / holding_days). Approximate
    but appropriate for short horizons; geometric compounding is overkill at
    typical 2-12 week holding periods."""
    if holding_days is None or holding_days <= 0:
        return None
    return alpha_pct * (365.0 / float(holding_days))


def _bucket_metrics(rows: list[dict]) -> dict[str, Any]:
    """Compute the metric set for one rating bucket (or the overall pool).

    rows is a list of dicts with at least raw_pct, alpha_pct, holding_days.
    Missing values are dropped per metric, not per row. Returns an empty dict
    when there's no usable data.
    """
    import math
    if not rows:
        return {"count": 0}

    raws = [r["raw_pct"] for r in rows if r.get("raw_pct") is not None]
    alphas = [r["alpha_pct"] for r in rows if r.get("alpha_pct") is not None]
    holds = [r["holding_days"] for r in rows if r.get("holding_days")]
    annualized = [
        _annualize_alpha(r["alpha_pct"], r["holding_days"])
        for r in rows
        if r.get("alpha_pct") is not None and r.get("holding_days")
    ]
    annualized = [a for a in annualized if a is not None]

    out: dict[str, Any] = {"count": len(rows)}
    if raws:
        out["mean_raw_pct"] = round(statistics.fmean(raws), 2)
        out["raw_hit_rate"] = round(sum(1 for x in raws if x > 0) / len(raws), 3)
    if alphas:
        out["mean_alpha_pct"] = round(statistics.fmean(alphas), 2)
        out["alpha_hit_rate"] = round(sum(1 for x in alphas if x > 0) / len(alphas), 3)
        if len(alphas) >= 2:
            sd = statistics.stdev(alphas)
            if sd > 1e-9:
                t_stat = statistics.fmean(alphas) / (sd / math.sqrt(len(alphas)))
                out["alpha_t_stat"] = round(t_stat, 2)
            else:
                out["alpha_t_stat"] = None
        else:
            out["alpha_t_stat"] = None
    if holds:
        out["mean_holding_days"] = round(statistics.fmean(holds), 1)
    if annualized:
        out["annualized_alpha_pct"] = round(statistics.fmean(annualized), 2)
        # Sortino-flavored: mean(annualized) / downside_dev where downside_dev =
        # sqrt(mean(min(x, 0)^2)). Treats the risk-free rate as 0 (we already
        # work in alpha space, not raw returns, so excess-vs-rf is double-counting).
        downside_sq = [min(a, 0.0) ** 2 for a in annualized]
        downside_dev = math.sqrt(sum(downside_sq) / len(downside_sq)) if downside_sq else 0.0
        if downside_dev > 1e-9:
            out["annualized_alpha_sortino_like"] = round(
                statistics.fmean(annualized) / downside_dev, 2
            )
        else:
            out["annualized_alpha_sortino_like"] = None
    return out


def cmd_backtest(args, fmt, timer, request_id) -> int:
    log = resolve_log_path(args.log, args.out_dir)
    entries = load_entries(log)

    # Parse window flags
    auto_derived_from = args.from_ is None
    auto_derived_to = args.to is None
    try:
        from_iso = normalize_date(args.from_) if args.from_ else None
        to_iso = normalize_date(args.to) if args.to else None
    except ValueError as e:
        return emit_failure(ExitCode.VALIDATION, str(e), fmt,
                            code="validation_error", retryable=False,
                            context={"value": args.from_ or args.to},
                            timer=timer, request_id=request_id)

    if from_iso and to_iso and from_iso > to_iso:
        return emit_failure(ExitCode.VALIDATION,
                            f"--from {from_iso} must be on or before --to {to_iso}",
                            fmt, code="validation_error", retryable=False,
                            context={"from": from_iso, "to": to_iso},
                            timer=timer, request_id=request_id)

    if args.rating and args.rating not in RATINGS:
        return emit_failure(ExitCode.VALIDATION,
                            f"unknown rating: {args.rating!r}",
                            fmt, code="validation_error", retryable=False,
                            context={"rating": args.rating, "valid": RATINGS},
                            timer=timer, request_id=request_id)

    resolved = [e for e in entries if not e["pending"]]

    # Apply filters and parse numeric fields once.
    rows: list[dict] = []
    for e in resolved:
        if from_iso and e["date"] < from_iso:
            continue
        if to_iso and e["date"] > to_iso:
            continue
        if args.rating and e["rating"] != args.rating:
            continue
        rows.append({
            "date": e["date"],
            "ticker": e["ticker"],
            "rating": e["rating"],
            "raw_pct": parse_pct(e["raw"]),
            "alpha_pct": parse_pct(e["alpha"]),
            "holding_days": parse_holding(e["holding"]),
        })

    if not rows:
        return emit_failure(ExitCode.NO_DATA,
                            "no resolved entries match the window/rating filter",
                            fmt, code="no_data", retryable=False,
                            context={"path": str(log),
                                     "from": from_iso, "to": to_iso,
                                     "rating": args.rating,
                                     "total_resolved_in_log": len(resolved)},
                            timer=timer, request_id=request_id)

    rows.sort(key=lambda r: r["date"])

    # Auto-derive window when not explicit
    if auto_derived_from:
        from_iso = rows[0]["date"]
    if auto_derived_to:
        to_iso = rows[-1]["date"]

    # Per-rating buckets
    by_rating: dict[str, list[dict]] = {}
    for r in rows:
        by_rating.setdefault(r["rating"], []).append(r)
    by_rating_out = {rating: _bucket_metrics(rs) for rating, rs in by_rating.items()}

    # Overall pool
    overall = _bucket_metrics(rows)

    # Cumulative-alpha curve and its drawdown
    curve: list[dict] = []
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    max_dd_date: str | None = None
    for r in rows:
        a = r["alpha_pct"]
        if a is None:
            continue
        running += a
        peak = max(peak, running)
        dd = running - peak  # <= 0
        if dd < max_dd:
            max_dd = dd
            max_dd_date = r["date"]
        curve.append({"date": r["date"], "cum_alpha_pct": round(running, 2)})

    overall["max_cum_alpha_drawdown_pct"] = round(max_dd, 2) if max_dd < 0 else 0.0
    overall["max_cum_alpha_drawdown_at"] = max_dd_date

    data = {
        "path": str(log),
        "window": {"from": from_iso, "to": to_iso,
                   "auto_derived_from": auto_derived_from,
                   "auto_derived_to": auto_derived_to},
        "rating_filter": args.rating,
        "n_resolved": len(rows),
        "by_rating": by_rating_out,
        "overall": overall,
        "cumulative_alpha_curve": curve,
    }

    def table() -> None:
        print(f"path: {log}")
        print(f"window: {from_iso} .. {to_iso}  (auto_from={auto_derived_from}, auto_to={auto_derived_to})")
        if args.rating:
            print(f"rating filter: {args.rating}")
        print(f"n_resolved: {len(rows)}")
        print("--- overall ---")
        for k, v in overall.items():
            print(f"  {k}: {v}")
        print("--- by rating ---")
        for rating, m in by_rating_out.items():
            line = f"  {rating}: count={m.get('count')}"
            for k in ("mean_alpha_pct", "alpha_hit_rate", "alpha_t_stat", "annualized_alpha_pct"):
                if k in m:
                    line += f"  {k}={m[k]}"
            print(line)

    return emit_success(data, fmt, timer=timer, request_id=request_id, table_render=table)


# ----- compute-returns / auto-resolve -----

def _compute_returns_core(args, fmt, timer, request_id):
    """Shared logic between cmd_compute_returns and cmd_auto_resolve.

    Returns (envelope_data, error_envelope_or_None). On any fatal error
    (validation, auth, no_data, dependency), the error envelope is already
    emitted and the second tuple member is the exit code.
    """
    try:
        decision_iso = normalize_date(args.date)
    except ValueError as e:
        return None, emit_failure(ExitCode.VALIDATION, str(e), fmt,
                                  code="validation_error", retryable=False,
                                  context={"value": args.date},
                                  timer=timer, request_id=request_id)

    try:
        as_of_iso = normalize_date(args.as_of) if args.as_of else dt.date.today().isoformat()
    except ValueError as e:
        return None, emit_failure(ExitCode.VALIDATION, str(e), fmt,
                                  code="validation_error", retryable=False,
                                  context={"value": args.as_of},
                                  timer=timer, request_id=request_id)

    if as_of_iso <= decision_iso:
        return None, emit_failure(ExitCode.VALIDATION,
                                  f"as_of date {as_of_iso} must be after decision date {decision_iso}",
                                  fmt, code="validation_error", retryable=False,
                                  context={"decision_date": decision_iso, "as_of_date": as_of_iso},
                                  timer=timer, request_id=request_id)

    try:
        market = detect_market(args.ticker)
    except ValueError as e:
        return None, emit_failure(ExitCode.VALIDATION, str(e), fmt,
                                  code="validation_error", retryable=False,
                                  context={"ticker": args.ticker},
                                  timer=timer, request_id=request_id)

    benchmark_ts = args.benchmark or DEFAULT_BENCHMARK[market]

    if args.dry_run:
        # Short-circuit before any upstream call
        return {
            "dry_run": True,
            "ticker": args.ticker,
            "market": market,
            "decision_date": decision_iso,
            "as_of_date": as_of_iso,
            "benchmark_ts_code": benchmark_ts,
            "would_call": (
                ["yfinance.download"] if market == "us"
                else ["pro.daily" if market == "a_share" else "pro.hk_daily",
                      "pro.index_daily / pro.index_global / pro.hk_daily (benchmark fallback chain)"]
            ),
        }, None

    # Need Tushare for stock close (A-share / HK) and for benchmark
    pro = None
    if market in ("a_share", "hk") or benchmark_ts != "SPY":
        token = os.environ.get("TUSHARE_TOKEN")
        if not token:
            try:
                ts_mod = import_tushare()
                token = ts_mod.get_token()
            except RuntimeError as e:
                return None, emit_failure(ExitCode.DEPENDENCY, str(e), fmt,
                                          code="dependency_missing", retryable=False,
                                          context={"required": "tushare"},
                                          timer=timer, request_id=request_id)
        if not token:
            return None, emit_failure(ExitCode.AUTH, "missing TUSHARE_TOKEN",
                                      fmt, code="auth_missing", retryable=False,
                                      context={"env_var": "TUSHARE_TOKEN"},
                                      timer=timer, request_id=request_id)
        try:
            ts_mod = import_tushare()
        except RuntimeError as e:
            return None, emit_failure(ExitCode.DEPENDENCY, str(e), fmt,
                                      code="dependency_missing", retryable=False,
                                      context={"required": "tushare"},
                                      timer=timer, request_id=request_id)
        pro = ts_mod.pro_api(token)

    # Fetch stock closes
    try:
        decision_close, decision_td = fetch_close_for_market(
            pro, args.ticker, decision_iso, "forward", market=market,
        )
        as_of_close, as_of_td = fetch_close_for_market(
            pro, args.ticker, as_of_iso, "backward", market=market,
        )
    except RuntimeError as e:
        return None, emit_failure(ExitCode.DEPENDENCY, str(e), fmt,
                                  code="dependency_missing", retryable=False,
                                  context={"ticker": args.ticker, "market": market},
                                  timer=timer, request_id=request_id)

    if decision_close is None or as_of_close is None:
        return None, emit_failure(ExitCode.NO_DATA,
                                  f"could not fetch closes for {args.ticker} (decision={decision_iso}, as_of={as_of_iso})",
                                  fmt, code="no_data", retryable=True,
                                  context={
                                      "ticker": args.ticker,
                                      "decision_date": decision_iso,
                                      "as_of_date": as_of_iso,
                                      "decision_close_found": decision_close is not None,
                                      "as_of_close_found": as_of_close is not None,
                                  },
                                  timer=timer, request_id=request_id)

    if decision_td == as_of_td:
        return None, emit_failure(ExitCode.VALIDATION,
                                  f"decision and as-of resolve to the same trading day {decision_td}; nothing to evaluate",
                                  fmt, code="validation_error", retryable=False,
                                  context={"trade_date": decision_td},
                                  timer=timer, request_id=request_id)

    raw_return_pct = (as_of_close / decision_close - 1) * 100

    # Fetch benchmark closes (best-effort; fall through to alpha=null on failure)
    bench_decision, bench_decision_td, bench_endpoint = None, None, None
    bench_as_of, bench_as_of_td = None, None
    bench_return_pct = None
    if benchmark_ts == "SPY" and market == "us":
        try:
            bench_decision, bench_decision_td = _fetch_yfinance_close(benchmark_ts, decision_iso, "forward")
            bench_as_of, bench_as_of_td = _fetch_yfinance_close(benchmark_ts, as_of_iso, "backward")
            bench_endpoint = "yfinance"
        except RuntimeError:
            pass
    else:
        bench_decision, bench_decision_td, bench_endpoint = fetch_benchmark_close(
            pro, benchmark_ts, decision_iso, "forward",
        )
        if bench_decision is not None:
            bench_as_of, bench_as_of_td, _ = fetch_benchmark_close(
                pro, benchmark_ts, as_of_iso, "backward",
            )

    benchmark_warning = None
    if bench_decision is not None and bench_as_of is not None and bench_decision > 0:
        bench_return_pct = (bench_as_of / bench_decision - 1) * 100
    else:
        benchmark_warning = (
            f"could not fetch benchmark {benchmark_ts!r}; alpha not computed"
        )

    alpha_return_pct = (
        round(raw_return_pct - bench_return_pct, 4)
        if bench_return_pct is not None else None
    )

    holding_days = (
        dt.datetime.strptime(as_of_iso, "%Y-%m-%d").date()
        - dt.datetime.strptime(decision_iso, "%Y-%m-%d").date()
    ).days

    data = {
        "ticker": args.ticker,
        "market": market,
        "decision_date": decision_iso,
        "as_of_date": as_of_iso,
        "decision_trade_date": decision_td,
        "as_of_trade_date": as_of_td,
        "decision_close": round(decision_close, 4),
        "as_of_close": round(as_of_close, 4),
        "raw_return_pct": round(raw_return_pct, 4),
        "benchmark_ts_code": benchmark_ts,
        "benchmark_decision_close": round(bench_decision, 4) if bench_decision is not None else None,
        "benchmark_as_of_close": round(bench_as_of, 4) if bench_as_of is not None else None,
        "benchmark_return_pct": round(bench_return_pct, 4) if bench_return_pct is not None else None,
        "alpha_return_pct": alpha_return_pct,
        "holding_days": holding_days,
        "data_source": (
            f"yfinance" if market == "us"
            else f"tushare:pro.{'hk_daily' if market == 'hk' else 'daily'} + {bench_endpoint}"
            if bench_endpoint else f"tushare:pro.{'hk_daily' if market == 'hk' else 'daily'}"
        ),
    }
    if benchmark_warning:
        data["benchmark_warning"] = benchmark_warning
    return data, None


def cmd_compute_returns(args, fmt, timer, request_id) -> int:
    data, err_code = _compute_returns_core(args, fmt, timer, request_id)
    if err_code is not None:
        return err_code

    def table() -> None:
        print(f"ticker: {data['ticker']}  market: {data['market']}")
        print(f"decision: {data['decision_date']} ({data['decision_trade_date']}) close={data['decision_close']}")
        print(f"as_of:    {data['as_of_date']} ({data['as_of_trade_date']}) close={data['as_of_close']}")
        print(f"raw_return%: {data['raw_return_pct']:+.2f}  holding_days: {data['holding_days']}")
        if data.get("alpha_return_pct") is not None:
            print(f"benchmark: {data['benchmark_ts_code']} return%={data['benchmark_return_pct']:+.2f} → alpha%={data['alpha_return_pct']:+.2f}")
        elif "benchmark_warning" in data:
            print(f"benchmark: {data.get('benchmark_warning')}")

    return emit_success(data, fmt, timer=timer, request_id=request_id, table_render=table)


def cmd_auto_resolve(args, fmt, timer, request_id) -> int:
    """Compute returns then run the same atomic-rewrite resolve logic as cmd_resolve."""
    data, err_code = _compute_returns_core(args, fmt, timer, request_id)
    if err_code is not None:
        return err_code

    if args.dry_run:
        # _compute_returns_core already short-circuited; just wrap the data
        return emit_success(
            {**data, "would_resolve": True},
            fmt, timer=timer, request_id=request_id,
            table_render=lambda: print(
                f"would_auto_resolve: {args.ticker} {data['decision_date']} "
                f"raw={data.get('raw_return_pct')} alpha={data.get('alpha_return_pct')}"
            ),
        )

    # Pull computed numbers and forward to the existing resolve logic
    if data.get("alpha_return_pct") is None:
        return emit_failure(ExitCode.NO_DATA,
                            f"could not compute alpha (benchmark fetch failed); "
                            f"resolve manually with --raw-return {data['raw_return_pct']}",
                            fmt, code="no_data", retryable=True,
                            context={**data, "suggestion":
                                     "Run `dexter_memory_log.py resolve --alpha-return <pct>` after fetching benchmark separately."},
                            timer=timer, request_id=request_id)

    # Reuse cmd_resolve: build a synthetic args-like namespace
    class _A:
        pass
    a = _A()
    a.ticker = args.ticker
    a.date = args.date
    a.raw_return = data["raw_return_pct"]
    a.alpha_return = data["alpha_return_pct"]
    a.holding_days = data["holding_days"]
    a.reflection = args.reflection
    a.log = args.log
    a.out_dir = args.out_dir
    a.dry_run = False  # already handled above

    # cmd_resolve emits its own envelope. To return a richer combined envelope,
    # we replicate its logic inline here. Atomic rewrite + REFLECTION append.
    log = resolve_log_path(args.log, args.out_dir)
    if not log.exists():
        return emit_failure(ExitCode.NO_DATA, f"log not found: {log}",
                            fmt, code="no_data", retryable=False,
                            context={"path": str(log)},
                            timer=timer, request_id=request_id)

    text = read_log(log)
    blocks = text.split(SEPARATOR)
    decision_iso = data["decision_date"]
    pending_prefix = f"[{decision_iso} | {args.ticker} |"
    raw_pct = f"{data['raw_return_pct']:+.1f}%"
    alpha_pct = f"{data['alpha_return_pct']:+.1f}%"

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
                    f"[{decision_iso} | {args.ticker} | {rating}"
                    f" | {raw_pct} | {alpha_pct} | {data['holding_days']}d]"
                )
                rest = "\n".join(lines[1:]).lstrip()
                rebuilt = f"{new_tag}\n\n{rest}\n\nREFLECTION:\n{args.reflection.strip()}"
                new_blocks.append(rebuilt)
                updated = True
                continue
        new_blocks.append(block)

    if not updated:
        return emit_failure(ExitCode.NO_DATA,
                            f"no pending entry found for {decision_iso} {args.ticker}",
                            fmt, code="no_data", retryable=False,
                            context={"decision_date": decision_iso, "ticker": args.ticker,
                                     "path": str(log), "computed_returns": data},
                            timer=timer, request_id=request_id)

    atomic_write(log, SEPARATOR.join(new_blocks))

    out = {
        "computed": data,
        "path": str(log),
        "updated": True,
        "new_tag": new_tag,
        "raw_return_pct": data["raw_return_pct"],
        "alpha_return_pct": data["alpha_return_pct"],
        "holding_days": data["holding_days"],
    }

    def table() -> None:
        print(f"resolved: {new_tag}")
        print(f"  raw_return%={data['raw_return_pct']:+.2f}  alpha%={data['alpha_return_pct']:+.2f}  holding={data['holding_days']}d")
        print(f"  benchmark: {data['benchmark_ts_code']} ({data['benchmark_return_pct']:+.2f}%)")
        print(f"  log: {log}")

    return emit_success(out, fmt, timer=timer, request_id=request_id, table_render=table)


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

    p_bt = sub.add_parser("backtest",
                           help="Risk-adjusted decision-level metrics across a window")
    p_bt.add_argument("--from", dest="from_",
                      help="Window start YYYY-MM-DD/YYYYMMDD; default = earliest resolved entry")
    p_bt.add_argument("--to", help="Window end; default = latest resolved entry")
    p_bt.add_argument("--rating", choices=RATINGS,
                      help="Optional filter to a single rating bucket")
    _shared(p_bt)

    p_cr = sub.add_parser("compute-returns",
                           help="Fetch close[decision]/close[as_of] + benchmark, compute raw + alpha (no log mutation)")
    p_cr.add_argument("--ticker", required=True)
    p_cr.add_argument("--date", required=True, help="Decision date")
    p_cr.add_argument("--as-of", dest="as_of", default=None, help="As-of date; default today")
    p_cr.add_argument("--benchmark", default=None, help="Override default benchmark ts_code")
    _shared(p_cr)

    p_ar = sub.add_parser("auto-resolve",
                           help="compute-returns + resolve in one call (closes the resolve loop)")
    p_ar.add_argument("--ticker", required=True)
    p_ar.add_argument("--date", required=True, help="Decision date of the pending entry to resolve")
    p_ar.add_argument("--reflection", required=True,
                      help="Lesson learned, 2-4 sentences (see references/reflection-prompt.md)")
    p_ar.add_argument("--as-of", dest="as_of", default=None)
    p_ar.add_argument("--benchmark", default=None)
    _shared(p_ar)

    args = p.parse_args()
    fmt = resolve_format(args.format)
    timer = Timer()
    request_id = new_request_id()

    if args.schema:
        return emit_schema(SCHEMA, fmt, timer=timer)

    valid_subs = ["record", "resolve", "list", "context", "stats",
                  "backtest", "compute-returns", "auto-resolve"]
    if not args.cmd:
        return emit_failure(
            ExitCode.VALIDATION,
            "missing subcommand: choose one of " + " / ".join(valid_subs),
            fmt, code="validation_error", retryable=False,
            context={"valid_subcommands": valid_subs},
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
        if args.cmd == "backtest":
            return cmd_backtest(args, fmt, timer, request_id)
        if args.cmd == "compute-returns":
            return cmd_compute_returns(args, fmt, timer, request_id)
        if args.cmd == "auto-resolve":
            return cmd_auto_resolve(args, fmt, timer, request_id)
    except Exception as e:
        return emit_failure(
            ExitCode.RUNTIME, f"{type(e).__name__}: {e}",
            fmt, code="runtime_error", retryable=False,
            context={"subcommand": args.cmd}, timer=timer, request_id=request_id,
        )

    return ExitCode.RUNTIME


if __name__ == "__main__":
    raise SystemExit(main())
