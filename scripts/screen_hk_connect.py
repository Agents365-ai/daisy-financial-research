#!/usr/bin/env python3
"""Hong Kong Stock Connect (港股通) watchlist screener using Tushare hk_hold + hk_daily.

Useful only when the user explicitly wants 港股通 / southbound universe. Ranks
by southbound holding ratio and optional recent momentum from hk_daily.
Fundamental/dividend checks should be verified with web sources before action.

Agent-native conventions
  --format json|table   stdout shape; auto-JSON when stdout is not a TTY
  --schema              emit parameter schema, then exit
  --dry-run             preview which trade_date/pool size would be used
  --with-momentum       fetch hk_daily for each ticker; emits NDJSON progress
                        events on stderr (one per ticker) so agents can detect
                        liveness during the long fetch loop.
  Exit codes            0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import tushare as ts

from _envelope import (
    ExitCode,
    Timer,
    add_common_args,
    emit_failure,
    emit_progress,
    emit_schema,
    emit_success,
    new_request_id,
    resolve_format,
)

DEFAULT_ROOT_NAME = "financial-research"
SUBDIR = "watchlists"

SCHEMA = {
    "name": "screen_hk_connect",
    "description": "HK Stock Connect (southbound) watchlist screener",
    "params": {
        "date": {"type": "string", "format": "YYYYMMDD", "default": "today"},
        "lookback_days": {"type": "integer", "default": 14},
        "top": {"type": "integer", "default": 50},
        "candidate_pool": {"type": "integer", "default": 120, "description": "Compute momentum for top N by holding ratio"},
        "with_momentum": {"type": "bool", "default": False, "description": "Fetch hk_daily; emits per-ticker progress on stderr"},
        "out_dir": {"type": "string", "default": "./financial-research"},
    },
    "returns": {
        "trade_date": "YYYYMMDD",
        "candidates": "rows in final watchlist",
        "csv": "absolute path",
        "json": "absolute path",
        "preview": "list of top candidates",
    },
    "error_codes": ["auth_missing", "validation_error", "no_data", "runtime_error"],
    "upstream_interfaces": ["pro.hk_hold", "pro.hk_daily"],
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


def fetch_latest_hk_hold(pro, start: dt.date, lookback: int):
    errors = []
    for i in range(lookback + 1):
        td = yyyymmdd(start - dt.timedelta(days=i))
        try:
            df = pro.hk_hold(trade_date=td)
        except Exception as e:
            errors.append(f"{td}: {type(e).__name__}: {e}")
            continue
        if df is not None and not df.empty:
            return td, df, errors
    return None, None, errors


def pct_return(pro, ts_code: str, end_date: str, days: int) -> float | None:
    end = dt.datetime.strptime(end_date, "%Y%m%d").date()
    start = yyyymmdd(end - dt.timedelta(days=int(days * 1.8) + 10))
    try:
        df = pro.hk_daily(ts_code=ts_code, start_date=start, end_date=end)
    except Exception:
        return None
    if df is None or len(df) < 2:
        return None
    df = df.sort_values("trade_date")
    latest = float(df.iloc[-1]["close"])
    base = float(df.iloc[max(0, len(df) - 1 - days)]["close"])
    if base <= 0:
        return None
    return (latest / base - 1) * 100


def main() -> int:
    ap = argparse.ArgumentParser(
        description="HK Stock Connect watchlist screener",
        epilog="Exit codes: 0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency",
    )
    ap.add_argument("--date", help="Start trade date YYYYMMDD; default today")
    ap.add_argument("--lookback-days", type=int, default=14)
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--candidate-pool", type=int, default=120, help="Compute momentum for top N by holding ratio")
    ap.add_argument("--with-momentum", action="store_true", help="Fetch hk_daily to compute approx 3M return for pool")
    ap.add_argument("--out-dir", dest="out_dir", default=None,
                    help="Output root; default <cwd>/financial-research/ (watchlists/ subdir auto-appended)")
    add_common_args(ap)
    args = ap.parse_args()

    fmt = resolve_format(args.format)
    timer = Timer()
    request_id = new_request_id()

    if args.schema:
        return emit_schema(SCHEMA, fmt, timer=timer)

    try:
        start_date = parse_date(args.date)
    except ValueError:
        return emit_failure(
            ExitCode.VALIDATION,
            f"invalid --date value: {args.date!r} (expected YYYYMMDD)",
            fmt, code="validation_error", retryable=False,
            context={"value": args.date, "expected_format": "YYYYMMDD"},
            timer=timer, request_id=request_id,
        )

    if args.dry_run:
        out_dir = resolve_out_dir(args.out_dir)
        suffix = "with_momentum" if args.with_momentum else "southbound_ratio"
        return emit_success(
            {
                "dry_run": True,
                "would_call": ["pro.hk_hold"] + (["pro.hk_daily"] if args.with_momentum else []),
                "search_window": {
                    "start_date": yyyymmdd(start_date),
                    "lookback_days": args.lookback_days,
                },
                "candidate_pool": args.candidate_pool,
                "with_momentum": args.with_momentum,
                "estimated_hk_daily_calls": args.candidate_pool if args.with_momentum else 0,
                "would_write_pattern": str(out_dir / f"<trade_date>_hk_connect_{suffix}.{{csv,json}}"),
            },
            fmt, timer=timer, request_id=request_id,
            table_render=lambda: (
                print(f"would_call: pro.hk_hold" + (" + pro.hk_daily x" + str(args.candidate_pool) if args.with_momentum else "")),
                print(f"would_write: {out_dir}/<trade_date>_hk_connect_{suffix}.{{csv,json}}"),
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

    if fmt == "json":
        emit_progress("start", command="screen_hk_connect.run", request_id=request_id)

    trade_date, df, errors = fetch_latest_hk_hold(pro, start_date, args.lookback_days)
    if trade_date is None:
        return emit_failure(
            ExitCode.NO_DATA,
            f"No hk_hold data found in {args.lookback_days}d lookback from {yyyymmdd(start_date)}",
            fmt, code="no_data", retryable=True,
            context={
                "start_date": yyyymmdd(start_date),
                "lookback_days": args.lookback_days,
                "recent_errors": errors[-5:],
            },
            timer=timer, request_id=request_id,
        )

    for c in ["ratio", "vol"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values(["ratio", "vol"], ascending=False).drop_duplicates("ts_code").reset_index(drop=True)
    pool = df.head(args.candidate_pool).copy()

    if args.with_momentum:
        if fmt == "json":
            emit_progress("progress", phase="momentum_start", request_id=request_id, total=len(pool))
        vals = []
        for idx, code in enumerate(pool["ts_code"], 1):
            v = pct_return(pro, code, trade_date, 63)
            vals.append(v)
            if fmt == "json" and (idx % 10 == 0 or idx == len(pool)):
                emit_progress("progress", phase="momentum", request_id=request_id, done=idx, total=len(pool))
        pool["return_3m_pct"] = vals
    else:
        pool["return_3m_pct"] = np.nan

    pool["score_southbound"] = pool["ratio"].rank(pct=True) * 100
    pool["score_momentum"] = pd.to_numeric(pool["return_3m_pct"], errors="coerce").rank(pct=True).fillna(0) * 100
    pool["score_total"] = pool["score_southbound"] * (0.7 if args.with_momentum else 1.0) + pool["score_momentum"] * (0.3 if args.with_momentum else 0.0)
    out = pool.sort_values("score_total", ascending=False).head(args.top).reset_index(drop=True)
    out.insert(0, "rank", np.arange(1, len(out) + 1))
    out["market"] = "HK Stock Connect"
    out["reason_selected"] = out.apply(lambda r: f"港股通持股比例约{r.get('ratio', float('nan')):.2f}%" + (f"；3M涨幅约{r.get('return_3m_pct'):.1f}%" if pd.notna(r.get('return_3m_pct')) else ""), axis=1)
    out["red_flags"] = "需核验流动性、分红可持续性、基本面和是否存在港股流动性陷阱"
    out["next_check"] = "用 Brave/Bailian 核验业绩、分红、回购、风险；对前3-8名做deep dive"
    out["source_snapshot"] = f"Tushare hk_hold/hk_daily {trade_date}"

    out_dir = resolve_out_dir(args.out_dir)
    suffix = "with_momentum" if args.with_momentum else "southbound_ratio"
    base = out_dir / f"{trade_date}_hk_connect_{suffix}"
    csv_path = base.with_suffix(".csv")
    json_path = base.with_suffix(".json")
    out.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(out.to_json(orient="records", force_ascii=False, indent=2), encoding="utf-8")

    cols = [c for c in ["rank", "ts_code", "name", "ratio", "vol", "return_3m_pct", "score_total"] if c in out.columns]
    preview = out[cols].head(min(10, len(out))).to_dict(orient="records")

    def table() -> None:
        print(f"trade_date: {trade_date}")
        print(f"candidates: {len(out)}")
        print(f"csv: {csv_path}")
        print(f"json: {json_path}")
        print(out[cols].head(min(10, len(out))).to_string(index=False))

    if fmt == "json":
        emit_progress("complete", request_id=request_id)

    return emit_success(
        {
            "trade_date": trade_date,
            "candidates": len(out),
            "csv": str(csv_path),
            "json": str(json_path),
            "with_momentum": args.with_momentum,
            "preview": preview,
        },
        fmt, timer=timer, request_id=request_id, table_render=table,
    )


if __name__ == "__main__":
    raise SystemExit(main())
