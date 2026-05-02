#!/usr/bin/env python3
"""Hong Kong Stock Connect watchlist screener using Tushare hk_hold + hk_daily.

This is useful only when the user explicitly wants 港股通 / southbound universe.
It ranks by southbound holding ratio and optional recent momentum from hk_daily.
Fundamental/dividend checks should be verified with web sources before action.
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

DEFAULT_ROOT_NAME = "financial-research"
SUBDIR = "watchlists"


def resolve_out_dir(arg_out_dir: str | None) -> Path:
    """Return <root>/watchlists, where <root> defaults to cwd/financial-research."""
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
    for i in range(lookback + 1):
        td = yyyymmdd(start - dt.timedelta(days=i))
        try:
            df = pro.hk_hold(trade_date=td)
        except Exception:
            continue
        if df is not None and not df.empty:
            return td, df
    raise RuntimeError("No hk_hold data found")


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
    ap = argparse.ArgumentParser(description="HK Stock Connect watchlist screener")
    ap.add_argument("--date", help="Start trade date YYYYMMDD; default today")
    ap.add_argument("--lookback-days", type=int, default=14)
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--candidate-pool", type=int, default=120, help="Compute momentum for top N by holding ratio")
    ap.add_argument("--with-momentum", action="store_true", help="Fetch hk_daily to compute approx 3M return for pool")
    ap.add_argument("--out-dir", dest="out_dir", default=None,
                    help="Output root; default <cwd>/financial-research/ (watchlists/ subdir auto-appended)")
    args = ap.parse_args()

    token = os.getenv("TUSHARE_TOKEN") or ts.get_token()
    if not token:
        print("ERROR: missing TUSHARE_TOKEN", file=sys.stderr)
        return 2
    pro = ts.pro_api(token)
    trade_date, df = fetch_latest_hk_hold(pro, parse_date(args.date), args.lookback_days)
    for c in ["ratio", "vol"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values(["ratio", "vol"], ascending=False).drop_duplicates("ts_code").reset_index(drop=True)
    pool = df.head(args.candidate_pool).copy()

    if args.with_momentum:
        vals = []
        for code in pool["ts_code"]:
            vals.append(pct_return(pro, code, trade_date, 63))
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
    print(f"trade_date: {trade_date}")
    print(f"candidates: {len(out)}")
    print(f"csv: {csv_path}")
    print(f"json: {json_path}")
    cols = [c for c in ["rank", "ts_code", "name", "ratio", "vol", "return_3m_pct", "score_total"] if c in out.columns]
    print(out[cols].head(min(10, len(out))).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
