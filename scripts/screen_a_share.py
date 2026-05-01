#!/usr/bin/env python3
"""A-share multi-factor watchlist screener using Tushare daily_basic.

This is intentionally conservative and reproducible. It creates a research
watchlist, not a buy list. For deep fundamental quality, follow up finalists
with `fina_indicator`, income/balance/cashflow and news checks.

Outputs:
  ~/.hermes/reports/financial-research/watchlists/YYYYMMDD_a_share_<preset>.csv
  ~/.hermes/reports/financial-research/watchlists/YYYYMMDD_a_share_<preset>.json
  optional Markdown report source for financial_report.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import tushare as ts

OUT_DIR = Path(os.path.expanduser("~/.hermes/reports/financial-research/watchlists"))
REPORT_DIR = Path(os.path.expanduser("~/.hermes/reports/financial-research"))

PRESETS = {
    "a_dividend_quality": {
        "min_mv": 1_000_000,  # Tushare total_mv is usually 10k CNY; 1,000,000 = 10bn CNY
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
            return trade_date, df
    raise RuntimeError("No daily_basic data found. Recent errors: " + " | ".join(errors[-5:]))


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


def make_markdown(df: pd.DataFrame, args, trade_date: str, csv_path: Path) -> Path:
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
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{trade_date}_a-share-{args.preset}-screen.md"
    path.write_text(md, encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="A-share Tushare watchlist screener")
    ap.add_argument("--preset", choices=sorted(PRESETS), default="a_dividend_quality")
    ap.add_argument("--date", help="Start trade date YYYYMMDD; default today, searches backward")
    ap.add_argument("--lookback-days", type=int, default=14)
    ap.add_argument("--top", type=int, default=50, help="Rows to keep in output")
    ap.add_argument("--top-report", type=int, default=30)
    ap.add_argument("--include-industry", action="append", help="Only include industry substring; can repeat")
    ap.add_argument("--exclude-industry", action="append", help="Exclude industry substring; can repeat")
    ap.add_argument("--report", action="store_true", help="Create Markdown report source")
    args = ap.parse_args()

    token = os.getenv("TUSHARE_TOKEN") or ts.get_token()
    if not token:
        print("ERROR: missing TUSHARE_TOKEN", file=sys.stderr)
        return 2
    pro = ts.pro_api(token)
    preset = PRESETS[args.preset]

    fields = "ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pb,total_mv,circ_mv,dv_ratio,dv_ttm"
    trade_date, db = fetch_latest_daily_basic(pro, parse_date(args.date), args.lookback_days, fields)
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
        print("No candidates after filters", file=sys.stderr)
        return 1

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

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = OUT_DIR / f"{trade_date}_a_share_{args.preset}"
    csv_path = base.with_suffix(".csv")
    json_path = base.with_suffix(".json")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(df.to_json(orient="records", force_ascii=False, indent=2), encoding="utf-8")

    print(f"trade_date: {trade_date}")
    print(f"candidates: {len(df)}")
    print(f"csv: {csv_path}")
    print(f"json: {json_path}")
    if args.report:
        md_path = make_markdown(df, args, trade_date, csv_path)
        print(f"markdown_report_source: {md_path}")
    show_cols = ["rank", "ts_code", "name", "industry", "close", "pe", "pb", "dv_ttm", "score_total"]
    print(df[show_cols].head(min(10, len(df))).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
