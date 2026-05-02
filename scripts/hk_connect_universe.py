#!/usr/bin/env python3
"""Fetch Hong Kong Stock Connect (港股通) investable/holding universe via Tushare.

Primary interface: pro.hk_hold(trade_date=YYYYMMDD)
This returns Southbound Stock Connect holdings for HK stocks with fields like:
code, trade_date, ts_code, name, vol, ratio, exchange.

The script searches backward from --date until data is found, de-duplicates by
`ts_code`, and writes a CSV under <out-dir>/universes/ (default ./financial-research/universes/, override via --out-dir <root>; --out can also pass a full file path).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
import sys

import pandas as pd
import tushare as ts

DEFAULT_ROOT_NAME = "financial-research"
SUBDIR = "universes"


def resolve_out_dir(arg_out_dir: str | None) -> Path:
    """Return <root>/universes, where <root> defaults to cwd/financial-research."""
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
            return trade_date, df
    raise RuntimeError("No hk_hold data found. Recent errors: " + " | ".join(errors[-5:]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Export Hong Kong Stock Connect universe from Tushare hk_hold")
    ap.add_argument("--date", help="Start trade date YYYYMMDD; default today, searches backward")
    ap.add_argument("--lookback-days", type=int, default=14)
    ap.add_argument("--out", help="Output CSV path; overrides --out-dir if given")
    ap.add_argument("--out-dir", dest="out_dir", default=None,
                    help="Output root; default <cwd>/financial-research/ (universes/ subdir auto-appended)")
    ap.add_argument("--top", type=int, default=0, help="Print top N rows by holding ratio; 0 prints only summary")
    args = ap.parse_args()

    token = os.getenv("TUSHARE_TOKEN") or ts.get_token()
    if not token:
        print("ERROR: missing TUSHARE_TOKEN", file=sys.stderr)
        return 2
    pro = ts.pro_api(token)

    trade_date, raw = fetch_latest_hk_hold(pro, parse_date(args.date), args.lookback_days)
    df = raw.copy()
    # normalize/dedupe; hk_hold can already be per-stock, but keep robust
    if "ts_code" not in df.columns:
        print("ERROR: hk_hold result missing ts_code", file=sys.stderr)
        return 1
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

    print(f"trade_date: {trade_date}")
    print(f"rows: {len(df)}")
    print(f"output: {out}")
    print(f"columns: {','.join(map(str, df.columns))}")
    if args.top and args.top > 0:
        cols = [c for c in ["ts_code", "name", "ratio", "vol", "exchange"] if c in df.columns]
        print(df[cols].head(args.top).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
