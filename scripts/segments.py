#!/usr/bin/env python3
"""Operating-segment / 主营构成 breakdown across markets.

Ports the idea behind virattt/dexter's `getFinancialSegments` tool
(`/financials/segments/`) to a free, A-share-first data path:

  A-share (*.SH / *.SZ / *.BJ) → AKShare `stock_zygc_em` (primary)
  HK     (*.HK)                → no structured free source — emits no_data
                                 with a pointer to read the annual report's
                                 "Segment Information" note via filings/web.
  US     (bare ticker)         → same — no free segment-level breakdown;
                                 use SEC 10-K Note "Segment Reporting".

AKShare returns one row per (report_date, classification, segment_name):
classification is 按产品 / 按地区 / 按行业 (by product / region / industry).

Agent-native conventions
  --format json|table   stdout shape; auto-JSON when stdout is not a TTY
  --schema              emit parameter schema, then exit
  --dry-run             preview the request shape; no upstream call
  Exit codes            0 ok · 1 runtime · 3 validation · 4 no_data · 5 dependency

Read-only — no `--out-dir`, no file writes.
"""
from __future__ import annotations

import argparse
import re
import sys
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


SCHEMA = {
    "name": "segments",
    "description": "Operating-segment breakdown by product / region / industry (A-share via AKShare; HK / US not supported by a free API).",
    "params": {
        "ts_code": {
            "type": "string", "required": True,
            "description": "Tushare-style ts_code: '600519.SH' / '000001.SZ' / '00005.HK' / 'AAPL'.",
        },
        "classification": {
            "type": "string", "required": False,
            "enum": ["按产品", "按地区", "按行业", "all"],
            "default": "all",
            "description": "Filter to one classification axis, or 'all' to return every axis present.",
        },
        "limit": {
            "type": "integer", "required": False, "default": 4,
            "description": "Most recent N report dates to keep (after grouping). Set 0 to keep all.",
        },
    },
    "returns": {
        "ts_code": "as supplied",
        "market": "a_share | hk | us",
        "akshare_symbol": "exchange-prefixed code passed to AKShare, e.g. SH600519",
        "rows": "list[{report_date, classification, segment, revenue, revenue_share_pct, cost, cost_share_pct, profit, profit_share_pct, gross_margin_pct}]",
        "report_dates": "list of distinct report_date strings retained (most-recent first)",
        "source": "akshare:stock_zygc_em",
    },
    "error_codes": ["validation_error", "no_data", "dependency_missing", "runtime_error"],
    "deps": {"required": ["akshare"], "lazy_imported": True},
    "auth": {"env_var": None},
    "market_routing": {
        "a_share": "akshare:stock_zygc_em",
        "hk": "no_data (hint: read 'Segment Information' note in annual report via filings/web)",
        "us": "no_data (hint: SEC 10-K Note 'Segment Reporting' via filings/web)",
    },
}


# `pro.daily`-style suffix grammar reused from technical_indicators.py.
_A_SHARE_RE = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$", re.IGNORECASE)
_HK_RE = re.compile(r"^(\d{4,5})\.HK$", re.IGNORECASE)
_US_RE = re.compile(r"^[A-Z][A-Z.\-]{0,9}$")


def classify_market(ts_code: str) -> str:
    """Return 'a_share' | 'hk' | 'us' | 'unknown' from a ts_code."""
    s = ts_code.strip().upper()
    if _A_SHARE_RE.match(s):
        return "a_share"
    if _HK_RE.match(s):
        return "hk"
    if _US_RE.match(s):
        return "us"
    return "unknown"


def akshare_symbol_for_a_share(ts_code: str) -> str:
    """'600519.SH' → 'SH600519'; '000001.SZ' → 'SZ000001'."""
    m = _A_SHARE_RE.match(ts_code.strip().upper())
    if not m:
        raise ValueError(f"not an A-share ts_code: {ts_code!r}")
    code, exch = m.group(1), m.group(2)
    return f"{exch}{code}"


def import_akshare():
    """Lazy-import akshare; raises RuntimeError if absent."""
    try:
        import akshare  # noqa: F401
        return akshare
    except ImportError as e:
        raise RuntimeError(f"akshare not installed: {e}") from e


def to_py(value: Any) -> Any:
    try:
        import pandas as pd
    except ImportError:
        pd = None
    if value is None:
        return None
    if pd is not None and pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            return value
    return value


def coerce_float(value: Any) -> float | None:
    v = to_py(value)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# AKShare column names for stock_zygc_em (as of akshare 1.x). Coded defensively
# in case AKShare renames columns — missing columns map to None.
_COL_REPORT_DATE = "报告日期"
_COL_CLASSIFICATION = "分类类型"
_COL_SEGMENT = "主营构成"
_COL_REVENUE = "主营收入"
_COL_REVENUE_PCT = "收入比例"
_COL_COST = "主营成本"
_COL_COST_PCT = "成本比例"
_COL_PROFIT = "主营利润"
_COL_PROFIT_PCT = "利润比例"
_COL_GROSS_MARGIN = "毛利率"


def normalize_row(r: Any) -> dict[str, Any]:
    """One AKShare row → a flat dict with English keys."""
    rd = to_py(r.get(_COL_REPORT_DATE))
    return {
        "report_date": str(rd)[:10] if rd is not None else None,
        "classification": str(to_py(r.get(_COL_CLASSIFICATION)) or ""),
        "segment": str(to_py(r.get(_COL_SEGMENT)) or ""),
        "revenue": coerce_float(r.get(_COL_REVENUE)),
        "revenue_share_pct": coerce_float(r.get(_COL_REVENUE_PCT)),
        "cost": coerce_float(r.get(_COL_COST)),
        "cost_share_pct": coerce_float(r.get(_COL_COST_PCT)),
        "profit": coerce_float(r.get(_COL_PROFIT)),
        "profit_share_pct": coerce_float(r.get(_COL_PROFIT_PCT)),
        "gross_margin_pct": coerce_float(r.get(_COL_GROSS_MARGIN)),
    }


def run_a_share(args, fmt, timer, request_id) -> int:
    ts_code = args.ts_code.strip().upper()
    try:
        symbol = akshare_symbol_for_a_share(ts_code)
    except ValueError as e:
        return emit_failure(ExitCode.VALIDATION, str(e), fmt,
                            context={"value": ts_code},
                            timer=timer, request_id=request_id)

    if args.dry_run:
        return emit_success(
            {"dry_run": True,
             "would_call": "ak.stock_zygc_em",
             "args": {"symbol": symbol},
             "classification_filter": args.classification,
             "limit": args.limit},
            fmt, timer=timer, request_id=request_id,
            table_render=lambda: print(f"would_call: stock_zygc_em(symbol={symbol!r})"),
        )

    try:
        ak = import_akshare()
    except RuntimeError as e:
        return emit_failure(ExitCode.DEPENDENCY, str(e), fmt,
                            context={"required": "akshare", "install": "pip install akshare"},
                            timer=timer, request_id=request_id)

    try:
        df = ak.stock_zygc_em(symbol=symbol)
    except Exception as e:
        return emit_failure(ExitCode.RUNTIME,
                            f"stock_zygc_em failed: {type(e).__name__}: {e}",
                            fmt, retryable=True,
                            context={"endpoint": "stock_zygc_em", "symbol": symbol},
                            timer=timer, request_id=request_id)

    if df is None or len(df) == 0:
        return emit_failure(ExitCode.NO_DATA,
                            f"no segment data for {ts_code}",
                            fmt,
                            context={"ts_code": ts_code, "akshare_symbol": symbol,
                                     "hint": "newly listed companies and ST/delisted names often have no 主营构成 history"},
                            timer=timer, request_id=request_id)

    rows = [normalize_row(r) for _, r in df.iterrows()]

    if args.classification and args.classification != "all":
        rows = [r for r in rows if r["classification"] == args.classification]
        if not rows:
            return emit_failure(ExitCode.NO_DATA,
                                f"no rows with classification={args.classification!r}",
                                fmt,
                                context={"ts_code": ts_code,
                                         "available_classifications":
                                             sorted({normalize_row(r2)["classification"]
                                                     for _, r2 in df.iterrows()})},
                                timer=timer, request_id=request_id)

    # Keep most-recent N report_dates (after sorting desc); 0 = keep all.
    all_dates = sorted({r["report_date"] for r in rows if r["report_date"]}, reverse=True)
    if args.limit and args.limit > 0:
        kept_dates = set(all_dates[: args.limit])
        rows = [r for r in rows if r["report_date"] in kept_dates]
        report_dates = sorted(kept_dates, reverse=True)
    else:
        report_dates = all_dates

    data = {
        "ts_code": ts_code,
        "market": "a_share",
        "akshare_symbol": symbol,
        "rows": rows,
        "report_dates": report_dates,
        "source": "akshare:stock_zygc_em",
    }

    def table() -> None:
        print(f"ts_code: {ts_code}  symbol: {symbol}  rows: {len(rows)}  dates: {len(report_dates)}")
        last = None
        for r in rows:
            key = (r["report_date"], r["classification"])
            if key != last:
                print(f"\n  {r['report_date']}  [{r['classification']}]")
                last = key
            rev = r["revenue"]
            pct = r["revenue_share_pct"]
            margin = r["gross_margin_pct"]
            print(f"    {r['segment']}  rev={rev}  share={pct}%  margin={margin}%")

    return emit_success(data, fmt, timer=timer, request_id=request_id, table_render=table)


def run_hk_or_us(args, fmt, timer, request_id, market: str) -> int:
    """HK and US share the same 'no free API' resolution."""
    if args.dry_run:
        return emit_success(
            {"dry_run": True, "market": market, "would_call": None,
             "note": "no free segment endpoint for this market"},
            fmt, timer=timer, request_id=request_id,
            table_render=lambda: print(f"would_call: <none>  market={market}"),
        )

    hint_filing = ("read 'Segment Information' note in the latest annual report (10-K Note for US, "
                   "annual report 'Operating Segments' / 'Segment Information' section for HK)")
    return emit_failure(
        ExitCode.NO_DATA,
        f"no free segment-data API for {market.upper()} tickers — use filings/web search instead",
        fmt,
        context={"ts_code": args.ts_code, "market": market, "hint": hint_filing,
                 "alternative_tools": ["read_filings", "brave_web_search", "browser"]},
        timer=timer, request_id=request_id,
    )


def main() -> int:
    p = argparse.ArgumentParser(
        description="Operating-segment breakdown (A-share via AKShare stock_zygc_em).",
        epilog="Exit codes: 0 ok · 1 runtime · 3 validation · 4 no_data · 5 dependency",
    )
    p.add_argument("--ts-code", dest="ts_code", required=False,
                   help="Tushare-style ts_code (e.g. 600519.SH, 00005.HK, AAPL).")
    p.add_argument("--classification", choices=["按产品", "按地区", "按行业", "all"],
                   default="all",
                   help="Filter to one axis, or 'all' (default).")
    p.add_argument("--limit", type=int, default=4,
                   help="Keep N most-recent report dates (0 = keep all). Default 4.")
    add_common_args(p)

    args = p.parse_args()
    fmt = resolve_format(args.format)
    timer = Timer()
    request_id = new_request_id()

    if args.schema:
        return emit_schema(SCHEMA, fmt, timer=timer)

    if not args.ts_code:
        return emit_failure(
            ExitCode.VALIDATION,
            "missing required --ts-code",
            fmt, context={"hint": "e.g. --ts-code 600519.SH"},
            timer=timer, request_id=request_id,
        )

    market = classify_market(args.ts_code)
    if market == "unknown":
        return emit_failure(
            ExitCode.VALIDATION,
            f"unrecognized ts_code shape: {args.ts_code!r}",
            fmt,
            context={"value": args.ts_code,
                     "accepted_shapes": ["NNNNNN.SH", "NNNNNN.SZ", "NNNNNN.BJ", "NNNNN.HK", "AAPL"]},
            timer=timer, request_id=request_id,
        )

    try:
        if market == "a_share":
            return run_a_share(args, fmt, timer, request_id)
        return run_hk_or_us(args, fmt, timer, request_id, market)
    except Exception as e:
        return emit_failure(
            ExitCode.RUNTIME, f"{type(e).__name__}: {e}",
            fmt, context={"ts_code": args.ts_code, "market": market},
            timer=timer, request_id=request_id,
        )


if __name__ == "__main__":
    raise SystemExit(main())
