#!/usr/bin/env python3
"""A-share multi-factor watchlist screener using Tushare daily_basic + stock_basic.

Research watchlist, not a buy list. For deep fundamental quality, follow up
finalists with `fina_indicator`, income/balance/cashflow, and news checks.

Outputs (default <cwd>/financial-research/)
  watchlists/YYYYMMDD_a_share_<preset>.csv
  watchlists/YYYYMMDD_a_share_<preset>.json
  reports/YYYYMMDD_a-share-<preset>-screen.md  (when --report)

Agent-native conventions
  --format json|table   stdout shape; auto-JSON when stdout is not a TTY
  --schema              emit parameter schema (incl. preset registry), then exit
  --dry-run             preview filters and output paths without calling Tushare
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
    emit_schema,
    emit_success,
    new_request_id,
    resolve_format,
)

DEFAULT_ROOT_NAME = "financial-research"

PRESETS = {
    "a_dividend_quality": {
        "min_mv": 1_000_000,  # Tushare total_mv unit is 10k CNY; 1,000,000 = 10bn CNY
        "min_pe": 0,
        "max_pe": 30,
        "min_pb": 0,
        "max_pb": 5,
        "min_dv_ttm": 2.0,
        "weights": {"dividend": 0.35, "valuation": 0.30, "size": 0.20, "liquidity": 0.15},
        "description": "A-share stable dividend/valuation watchlist",
    },
    "a_value": {
        "min_mv": 1_000_000,
        "min_pe": 3,
        "max_pe": 35,
        "min_pb": 0.3,
        "max_pb": 4,
        "min_dv_ttm": 0,
        "weights": {"dividend": 0.10, "valuation": 0.50, "size": 0.25, "liquidity": 0.15},
        "description": "A-share low valuation watchlist with basic liquidity/size controls",
    },
}

SCHEMA = {
    "name": "screen_a_share",
    "description": "A-share Tushare watchlist screener (multi-factor, preset-driven)",
    "params": {
        "preset": {"type": "string", "enum": sorted(PRESETS.keys()), "default": "a_dividend_quality"},
        "date": {"type": "string", "format": "YYYYMMDD", "default": "today", "description": "Start trade date; searches backward"},
        "lookback_days": {"type": "integer", "default": 14},
        "top": {"type": "integer", "default": 50},
        "top_report": {"type": "integer", "default": 30},
        "include_industry": {"type": "list<string>", "description": "Industry substring filter; repeatable"},
        "exclude_industry": {"type": "list<string>", "description": "Industry substring exclusion; repeatable"},
        "report": {"type": "bool", "default": False, "description": "Also emit Markdown report source"},
        "out_dir": {"type": "string", "default": "./financial-research"},
    },
    "presets": PRESETS,
    "returns": {
        "trade_date": "YYYYMMDD",
        "candidates": "rows in final watchlist",
        "csv": "absolute path",
        "json": "absolute path",
        "markdown_report_source": "absolute path or null",
        "preset": "preset name used",
        "preview": "list of top candidates with key metrics",
    },
    "error_codes": ["auth_missing", "validation_error", "no_data", "runtime_error"],
    "upstream_interfaces": ["pro.daily_basic", "pro.stock_basic"],
    "auth": {"env_var": "TUSHARE_TOKEN"},
}


def resolve_out_dir(arg_out_dir: str | None, subdir: str) -> Path:
    if arg_out_dir:
        root = Path(arg_out_dir).expanduser()
    else:
        root = Path.cwd() / DEFAULT_ROOT_NAME
    out = root / subdir
    out.mkdir(parents=True, exist_ok=True)
    return out


def yyyymmdd(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def parse_date(s: str | None) -> dt.date:
    if not s:
        return dt.datetime.now().date()
    return dt.datetime.strptime(s, "%Y%m%d").date()


def fetch_latest_daily_basic(pro, start_date: dt.date, lookback_days: int, fields: str):
    errors = []
    for i in range(lookback_days + 1):
        day = start_date - dt.timedelta(days=i)
        trade_date = yyyymmdd(day)
        try:
            df = pro.daily_basic(trade_date=trade_date, fields=fields)
        except Exception as e:
            errors.append(f"{trade_date}: {type(e).__name__}: {e}")
            continue
        if df is not None and not df.empty:
            return trade_date, df, errors
    return None, None, errors


def percentile_score(s: pd.Series, higher_is_better: bool = True) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    score = x.rank(pct=True)
    if not higher_is_better:
        score = 1 - score
    return score.fillna(0) * 100


def safe_num(df: pd.DataFrame, cols):
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")


def red_flags(row) -> str:
    flags = []
    name = str(row.get("name", ""))
    if "ST" in name.upper() or "退" in name:
        flags.append("ST/退市风险标记")
    if pd.isna(row.get("dv_ttm")) or row.get("dv_ttm", 0) <= 0:
        flags.append("股息数据缺失/无股息")
    if row.get("pe", 0) <= 0:
        flags.append("PE非正")
    if row.get("turnover_rate", 0) <= 0:
        flags.append("成交/换手异常")
    return "; ".join(flags) if flags else "需进一步核验财报、现金流、行业风险"


def reason(row) -> str:
    parts = []
    if row.get("dv_ttm", 0) >= 4:
        parts.append(f"股息率TTM约{row.get('dv_ttm'):.1f}%")
    if row.get("pe", 999) < 15:
        parts.append(f"PE约{row.get('pe'):.1f}")
    if row.get("pb", 999) < 2:
        parts.append(f"PB约{row.get('pb'):.1f}")
    if row.get("total_mv", 0) >= 5_000_000:
        parts.append("市值较大")
    return "；".join(parts) or "综合因子排名靠前"


def make_markdown(df: pd.DataFrame, args, trade_date: str, csv_path: Path, report_dir: Path) -> Path:
    top = df.head(args.top_report).copy()
    cols = ["rank", "ts_code", "name", "industry", "close", "pe", "pb", "dv_ttm", "total_mv", "score_total", "reason_selected", "red_flags"]
    cols = [c for c in cols if c in top.columns]
    table = top[cols].to_markdown(index=False)
    md = f"""# A股选股 Watchlist - {args.preset}

Date: {dt.datetime.now().astimezone().isoformat(timespec='seconds')}
Universe: A-share listed stocks
Preset: {args.preset}
Trade date: {trade_date}
Data sources: Tushare stock_basic + daily_basic

## Executive summary

- This is a research watchlist, not a buy list.
- Preset: {args.preset} ({PRESETS[args.preset]['description']}).
- Candidates retained: {len(df)}.
- CSV output: `{csv_path}`

## Filters

| Filter | Value |
|---|---:|
| Min market cap total_mv | {PRESETS[args.preset]['min_mv']} |
| PE range | {PRESETS[args.preset]['min_pe']}–{PRESETS[args.preset]['max_pe']} |
| PB range | {PRESETS[args.preset]['min_pb']}–{PRESETS[args.preset]['max_pb']} |
| Min dividend yield TTM | {PRESETS[args.preset]['min_dv_ttm']}% |
| Exclusions | ST/*ST by name, missing critical valuation fields |

## Top candidates

{table}

## Next checks

1. Verify latest annual/interim report and cash-flow quality.
2. Check dividend payout ratio and sustainability.
3. Check sector concentration and whether low valuation is a value trap.
4. Deep-dive top 3–8 names before any decision.

## Disclaimer

Data analysis only, not investment advice.
"""
    path = report_dir / f"{trade_date}_a-share-{args.preset}-screen.md"
    path.write_text(md, encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(
        description="A-share Tushare watchlist screener",
        epilog="Exit codes: 0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency",
    )
    ap.add_argument("--preset", choices=sorted(PRESETS), default="a_dividend_quality")
    ap.add_argument("--date", help="Start trade date YYYYMMDD; default today, searches backward")
    ap.add_argument("--lookback-days", type=int, default=14)
    ap.add_argument("--top", type=int, default=50, help="Rows to keep in output")
    ap.add_argument("--top-report", type=int, default=30)
    ap.add_argument("--include-industry", action="append", help="Only include industry substring; can repeat")
    ap.add_argument("--exclude-industry", action="append", help="Exclude industry substring; can repeat")
    ap.add_argument("--report", action="store_true", help="Create Markdown report source")
    ap.add_argument("--out-dir", dest="out_dir", default=None,
                    help="Output root; default <cwd>/financial-research/ (watchlists/ and reports/ subdirs auto-appended)")
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

    preset = PRESETS[args.preset]

    if args.dry_run:
        watchlist_dir = resolve_out_dir(args.out_dir, "watchlists")
        return emit_success(
            {
                "dry_run": True,
                "preset": args.preset,
                "filters": {
                    "min_mv": preset["min_mv"],
                    "pe_range": [preset["min_pe"], preset["max_pe"]],
                    "pb_range": [preset["min_pb"], preset["max_pb"]],
                    "min_dv_ttm": preset["min_dv_ttm"],
                    "include_industry": args.include_industry or [],
                    "exclude_industry": args.exclude_industry or [],
                },
                "weights": preset["weights"],
                "search_window": {
                    "start_date": yyyymmdd(start_date),
                    "lookback_days": args.lookback_days,
                },
                "would_call": ["pro.daily_basic", "pro.stock_basic"],
                "would_write_pattern": str(watchlist_dir / f"<trade_date>_a_share_{args.preset}.{{csv,json}}"),
                "would_write_report": args.report,
            },
            fmt, timer=timer, request_id=request_id,
            table_render=lambda: (
                print(f"preset: {args.preset}"),
                print(f"filters: pe={preset['min_pe']}-{preset['max_pe']}, pb={preset['min_pb']}-{preset['max_pb']}, min_mv={preset['min_mv']}, min_dv_ttm={preset['min_dv_ttm']}"),
                print(f"would_write: {watchlist_dir}/<trade_date>_a_share_{args.preset}.{{csv,json}}"),
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

    fields = "ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pb,total_mv,circ_mv,dv_ratio,dv_ttm"
    trade_date, db, errors = fetch_latest_daily_basic(pro, start_date, args.lookback_days, fields)
    if trade_date is None:
        return emit_failure(
            ExitCode.NO_DATA,
            f"No daily_basic data found in {args.lookback_days}d lookback from {yyyymmdd(start_date)}",
            fmt, code="no_data", retryable=True,
            context={
                "start_date": yyyymmdd(start_date),
                "lookback_days": args.lookback_days,
                "recent_errors": errors[-5:],
                "suggested_action": "increase --lookback-days or pick a recent trading day",
            },
            timer=timer, request_id=request_id,
        )

    basic = pro.stock_basic(list_status="L", fields="ts_code,symbol,name,area,industry,list_date")
    df = db.merge(basic, on="ts_code", how="left")
    safe_num(df, ["close", "turnover_rate", "volume_ratio", "pe", "pb", "total_mv", "circ_mv", "dv_ratio", "dv_ttm"])

    df = df[~df["name"].fillna("").str.contains("ST|退", case=False, regex=True)]
    df = df.dropna(subset=["pe", "pb", "total_mv"])
    df = df[(df["total_mv"] >= preset["min_mv"]) & (df["pe"] >= preset["min_pe"]) & (df["pe"] <= preset["max_pe"]) & (df["pb"] >= preset["min_pb"]) & (df["pb"] <= preset["max_pb"])]
    if preset["min_dv_ttm"] > 0:
        df = df[df["dv_ttm"].fillna(0) >= preset["min_dv_ttm"]]
    if args.include_industry:
        pat = "|".join(map(str, args.include_industry))
        df = df[df["industry"].fillna("").str.contains(pat, case=False, regex=True)]
    if args.exclude_industry:
        pat = "|".join(map(str, args.exclude_industry))
        df = df[~df["industry"].fillna("").str.contains(pat, case=False, regex=True)]

    if df.empty:
        return emit_failure(
            ExitCode.NO_DATA, "No candidates after filters",
            fmt, code="no_data", retryable=True,
            context={
                "preset": args.preset,
                "trade_date": trade_date,
                "filters_applied": {
                    "pe_range": [preset["min_pe"], preset["max_pe"]],
                    "pb_range": [preset["min_pb"], preset["max_pb"]],
                    "min_mv": preset["min_mv"],
                    "min_dv_ttm": preset["min_dv_ttm"],
                    "include_industry": args.include_industry,
                    "exclude_industry": args.exclude_industry,
                },
                "suggested_action": "loosen filters or change preset",
            },
            timer=timer, request_id=request_id,
        )

    df["score_dividend"] = percentile_score(df.get("dv_ttm", pd.Series(index=df.index)), True)
    df["score_valuation"] = (percentile_score(df["pe"], False) + percentile_score(df["pb"], False)) / 2
    df["score_size"] = percentile_score(df["total_mv"], True)
    df["score_liquidity"] = percentile_score(df.get("turnover_rate", pd.Series(index=df.index)), True)
    w = preset["weights"]
    df["score_total"] = (
        df["score_dividend"] * w.get("dividend", 0)
        + df["score_valuation"] * w.get("valuation", 0)
        + df["score_size"] * w.get("size", 0)
        + df["score_liquidity"] * w.get("liquidity", 0)
    )
    df = df.sort_values("score_total", ascending=False).head(args.top).reset_index(drop=True)
    df.insert(0, "rank", np.arange(1, len(df) + 1))
    df["market"] = "A-share"
    df["reason_selected"] = df.apply(reason, axis=1)
    df["red_flags"] = df.apply(red_flags, axis=1)
    df["next_check"] = "核验财报、现金流、分红可持续性和行业风险"
    df["source_snapshot"] = f"Tushare daily_basic/stock_basic {trade_date}"

    watchlist_dir = resolve_out_dir(args.out_dir, "watchlists")
    base = watchlist_dir / f"{trade_date}_a_share_{args.preset}"
    csv_path = base.with_suffix(".csv")
    json_path = base.with_suffix(".json")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(df.to_json(orient="records", force_ascii=False, indent=2), encoding="utf-8")

    md_path: Path | None = None
    if args.report:
        report_dir = resolve_out_dir(args.out_dir, "reports")
        md_path = make_markdown(df, args, trade_date, csv_path, report_dir)

    show_cols = ["rank", "ts_code", "name", "industry", "close", "pe", "pb", "dv_ttm", "score_total"]
    preview_cols = [c for c in show_cols if c in df.columns]
    preview = df[preview_cols].head(min(10, len(df))).to_dict(orient="records")

    def table() -> None:
        print(f"trade_date: {trade_date}")
        print(f"candidates: {len(df)}")
        print(f"csv: {csv_path}")
        print(f"json: {json_path}")
        if md_path is not None:
            print(f"markdown_report_source: {md_path}")
        print(df[preview_cols].head(min(10, len(df))).to_string(index=False))

    return emit_success(
        {
            "trade_date": trade_date,
            "candidates": len(df),
            "preset": args.preset,
            "csv": str(csv_path),
            "json": str(json_path),
            "markdown_report_source": str(md_path) if md_path else None,
            "preview": preview,
        },
        fmt, timer=timer, request_id=request_id, table_render=table,
    )


if __name__ == "__main__":
    raise SystemExit(main())
