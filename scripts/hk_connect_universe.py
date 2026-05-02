#!/usr/bin/env python3
"""Hong Kong Stock Connect (港股通) universe export via Tushare hk_hold.

Returns Southbound Stock Connect holdings (code, ts_code, name, vol, ratio,
exchange) for the latest trade date with available data, searching backward
from --date when needed.

Agent-native conventions
  --format json|table   stdout shape; auto-JSON when stdout is not a TTY
  --schema              emit parameter schema, then exit
  --dry-run             preview which trade_date and output path would be used
  Exit codes            0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
import sys

import pandas as pd
import tushare as ts

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
SUBDIR = "universes"

SCHEMA = {
    "name": "hk_connect_universe",
    "description": "Export Hong Kong Stock Connect (港股通) universe from Tushare hk_hold",
    "params": {
        "date": {"type": "string", "format": "YYYYMMDD", "default": "today", "description": "Start trade date; searches backward"},
        "lookback_days": {"type": "integer", "default": 14},
        "out": {"type": "string", "description": "Full output CSV path; overrides --out-dir"},
        "out_dir": {"type": "string", "default": "./financial-research", "description": "Root; universes/ subdir auto-appended"},
        "top": {"type": "integer", "default": 0, "description": "Print top N rows by holding ratio in table mode"},
    },
    "returns": {
        "trade_date": "YYYYMMDD of the date with data",
        "csv": "absolute path to written CSV",
        "rows": "number of unique ts_codes",
        "columns": "list of column names",
        "preview": "list of top-N rows when --top > 0",
    },
    "error_codes": ["auth_missing", "validation_error", "no_data", "runtime_error"],
    "upstream_interfaces": ["pro.hk_hold"],
    "auth": {"env_var": "TUSHARE_TOKEN"},
}


def resolve_out_dir(arg_out_dir: str | None) -> Path:
    if arg_out_dir:
        root = Path(arg_out_dir).expanduser()
    else:
        root = Path.cwd() / DEFAULT_ROOT_NAME
    out = root / SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    return out


def yyyymmdd(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def parse_date(s: str | None) -> dt.date:
    if not s:
        return dt.datetime.now().date()
    return dt.datetime.strptime(s, "%Y%m%d").date()


def fetch_latest_hk_hold(pro, start_date: dt.date, lookback_days: int):
    errors = []
    for i in range(lookback_days + 1):
        day = start_date - dt.timedelta(days=i)
        trade_date = yyyymmdd(day)
        try:
            df = pro.hk_hold(trade_date=trade_date)
        except Exception as e:
            errors.append(f"{trade_date}: {type(e).__name__}: {e}")
            continue
        if df is not None and not df.empty:
            return trade_date, df, errors
    return None, None, errors


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Export Hong Kong Stock Connect universe from Tushare hk_hold",
        epilog="Exit codes: 0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency",
    )
    ap.add_argument("--date", help="Start trade date YYYYMMDD; default today, searches backward")
    ap.add_argument("--lookback-days", type=int, default=14)
    ap.add_argument("--out", help="Output CSV path; overrides --out-dir if given")
    ap.add_argument("--out-dir", dest="out_dir", default=None,
                    help="Output root; default <cwd>/financial-research/ (universes/ subdir auto-appended)")
    ap.add_argument("--top", type=int, default=0, help="Print top N rows by holding ratio; 0 prints only summary")
    add_common_args(ap)
    args = ap.parse_args()

    fmt = resolve_format(args.format)
    timer = Timer()
    request_id = new_request_id()

    if args.schema:
        return emit_schema(SCHEMA, fmt, timer=timer)

    try:
        start_date = parse_date(args.date)
    except ValueError as e:
        return emit_failure(
            ExitCode.VALIDATION,
            f"invalid --date value: {args.date!r} (expected YYYYMMDD)",
            fmt, code="validation_error", retryable=False,
            context={"value": args.date, "expected_format": "YYYYMMDD"},
            timer=timer, request_id=request_id,
        )

    if args.dry_run:
        if args.out:
            preview_out = str(Path(args.out).expanduser())
        else:
            preview_out = str(resolve_out_dir(args.out_dir) / "<trade_date>_hk-connect-universe.csv")
        return emit_success(
            {
                "dry_run": True,
                "would_call": "pro.hk_hold",
                "search_window": {
                    "start_date": yyyymmdd(start_date),
                    "lookback_days": args.lookback_days,
                },
                "would_write": preview_out,
            },
            fmt, timer=timer, request_id=request_id,
            table_render=lambda: (
                print(f"would_call: pro.hk_hold (start={yyyymmdd(start_date)}, lookback={args.lookback_days})"),
                print(f"would_write: {preview_out}"),
            ),
        )

    token = os.getenv("TUSHARE_TOKEN") or ts.get_token()
    if not token:
        return emit_failure(
            ExitCode.AUTH, "missing TUSHARE_TOKEN",
            fmt, code="auth_missing", retryable=False,
            context={"env_var": "TUSHARE_TOKEN"},
            timer=timer, request_id=request_id,
        )
    pro = ts.pro_api(token)

    trade_date, raw, errors = fetch_latest_hk_hold(pro, start_date, args.lookback_days)
    if trade_date is None:
        return emit_failure(
            ExitCode.NO_DATA,
            f"No hk_hold data found in {args.lookback_days}d lookback from {yyyymmdd(start_date)}",
            fmt, code="no_data", retryable=True,
            context={
                "start_date": yyyymmdd(start_date),
                "lookback_days": args.lookback_days,
                "recent_errors": errors[-5:],
                "suggested_action": "increase --lookback-days or pick a recent trading day",
            },
            timer=timer, request_id=request_id,
        )

    df = raw.copy()
    if "ts_code" not in df.columns:
        return emit_failure(
            ExitCode.RUNTIME, "hk_hold result missing ts_code column",
            fmt, code="runtime_error", retryable=False,
            context={"columns": list(df.columns)},
            timer=timer, request_id=request_id,
        )
    for col in ["vol", "ratio"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    sort_cols = [c for c in ["ratio", "vol"] if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols, ascending=False)
    df = df.drop_duplicates("ts_code", keep="first").reset_index(drop=True)

    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = resolve_out_dir(args.out_dir)
        out = out_dir / f"{trade_date}_hk-connect-universe.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")

    preview_cols = [c for c in ["ts_code", "name", "ratio", "vol", "exchange"] if c in df.columns]
    preview = df[preview_cols].head(args.top).to_dict(orient="records") if args.top > 0 else []

    def table() -> None:
        print(f"trade_date: {trade_date}")
        print(f"rows: {len(df)}")
        print(f"output: {out}")
        print(f"columns: {','.join(map(str, df.columns))}")
        if args.top and args.top > 0:
            print(df[preview_cols].head(args.top).to_string(index=False))

    return emit_success(
        {
            "trade_date": trade_date,
            "csv": str(out),
            "rows": len(df),
            "columns": list(df.columns),
            "preview": preview,
        },
        fmt, timer=timer, request_id=request_id, table_render=table,
    )


if __name__ == "__main__":
    raise SystemExit(main())
