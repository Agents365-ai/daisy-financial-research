#!/usr/bin/env python3
"""HK valuation + fundamentals fallback via AKShare.

Closes the documented Tushare gap (`pro.hk_daily_basic` is unavailable in
this env, returns `请指定正确的接口名`). For HK ts_codes, this helper
fetches:

  valuation     PE-TTM / PE-LYR / PB-MRQ / PB-LYR / PS-TTM / PCF-TTM
                + sector rank, plus a security profile (board, listing
                date, Stock Connect eligibility, ISIN).
                Source: AKShare stock_hk_valuation_comparison_em
                      + stock_hk_security_profile_em

  fundamentals  Annual or quarterly: BPS, EPS, ROE, ROA, gross/net
                margin, leverage, growth rates.
                Source: AKShare stock_financial_hk_analysis_indicator_em

Inputs accept both Tushare-style ts_codes (`00005.HK`) and bare codes
(`00005`); the `.HK` suffix is stripped internally.

Agent-native conventions
  --format json|table   stdout shape; auto-JSON when stdout is not a TTY
  --schema              emit parameter schema, then exit
  --dry-run             preview the request shape; no upstream call
  Exit codes            0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency

Use this when:
  - Tushare returns `请指定正确的接口名` for an HK interface
  - You need a free, no-token PE/PB snapshot for an HK ticker
  - You need RoTE / RoE / leverage for a bank (HSBC etc.) where DCF is
    the wrong primary frame

This script does not require TUSHARE_TOKEN.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
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


HK_NAME_DICT_PATH = Path(__file__).resolve().parent.parent / "references" / "hk-ticker-name.json"
_HK_NAME_CACHE: dict[str, str] | None = None


def lookup_hk_name(code: str) -> str | None:
    """Return Chinese short name for a 5-digit HK code, or None.

    Lazy-loads references/hk-ticker-name.json on first call. Curated list
    ported from TradingAgents-CN; not a complete HK universe — the goal is
    to cover the major names that show up in research without requiring
    an AKShare round-trip.
    """
    global _HK_NAME_CACHE
    if _HK_NAME_CACHE is None:
        try:
            raw = json.loads(HK_NAME_DICT_PATH.read_text(encoding="utf-8"))
            _HK_NAME_CACHE = {k: v for k, v in raw.items() if not k.startswith("_")}
        except Exception:
            _HK_NAME_CACHE = {}
    return _HK_NAME_CACHE.get(code)


SCHEMA = {
    "name": "akshare_hk_valuation",
    "description": "HK valuation + fundamentals fallback via AKShare (no Tushare token required)",
    "subcommands": {
        "valuation": {
            "description": "Current PE/PB/PS snapshot + security profile (with local-dict name fallback)",
            "params": {
                "ts_code": {"type": "string", "required": True,
                            "description": "Accepts '00005.HK' or '00005'"},
            },
            "returns": {
                "ts_code": "normalized 5-digit", "name": "Chinese short name",
                "pe_ttm": "float or null", "pe_lyr": "float or null",
                "pb_mrq": "float or null", "pb_lyr": "float or null",
                "ps_ttm": "float or null", "pcf_ttm": "float or null",
                "sector_rank_pe_ttm": "int or null",
                "profile": "security profile dict",
                "name_source": "akshare | local_dict | unknown",
                "source": "akshare:stock_hk_valuation_comparison_em + stock_hk_security_profile_em",
            },
        },
        "fundamentals": {
            "description": "Annual or quarterly financial indicators",
            "params": {
                "ts_code": {"type": "string", "required": True},
                "period": {"type": "string", "enum": ["年度", "中报", "季报"], "default": "年度"},
                "limit": {"type": "integer", "default": 8, "description": "Most recent N reports"},
            },
            "returns": {
                "ts_code": "normalized", "name": "string",
                "period": "period type used",
                "rows": "list of report dicts (REPORT_DATE, BPS, EPS, ROE_YEARLY, ROA, ...)",
                "source": "akshare:stock_financial_hk_analysis_indicator_em",
            },
        },
        "name": {
            "description": "Local-dict-only Chinese short name lookup (no API call)",
            "params": {
                "ts_code": {"type": "string", "required": True,
                            "description": "Accepts '00005.HK' or '00005'"},
            },
            "returns": {
                "ts_code": "normalized 5-digit", "name": "Chinese name or null",
                "source": "local_dict (references/hk-ticker-name.json) | unknown",
            },
        },
    },
    "error_codes": ["validation_error", "no_data", "dependency_missing", "runtime_error"],
    "deps": {"required": ["akshare"], "name_subcommand": "no deps"},
    "auth": {"env_var": None},
    "fallback_chain": {
        "valuation_name": ["akshare row.简称", "references/hk-ticker-name.json", "''"],
    },
}


HK_CODE_RE = re.compile(r"^(\d{4,5})(?:\.HK)?$", re.IGNORECASE)


def normalize_hk_code(s: str) -> str:
    """Accept '00005.HK', '00005', or '5'; return zero-padded 5-digit code."""
    s = s.strip().upper()
    m = HK_CODE_RE.match(s)
    if not m:
        raise ValueError(f"unrecognized HK ts_code: {s!r} (expected '00005.HK' or '00005')")
    return m.group(1).zfill(5)


def import_akshare():
    """Import akshare on demand and surface a structured dependency_missing error."""
    try:
        import akshare  # noqa: F401
        return akshare
    except ImportError as e:
        raise RuntimeError(f"akshare not installed: {e}") from e


def to_py(value: Any) -> Any:
    """Convert numpy/pandas scalars to native Python for JSON serialization."""
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


def coerce_int(value: Any) -> int | None:
    v = to_py(value)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ----- subcommands -----

def cmd_valuation(args, fmt, timer, request_id) -> int:
    try:
        code = normalize_hk_code(args.ts_code)
    except ValueError as e:
        return emit_failure(ExitCode.VALIDATION, str(e), fmt,
                            code="validation_error", retryable=False,
                            context={"value": args.ts_code},
                            timer=timer, request_id=request_id)

    if args.dry_run:
        return emit_success(
            {"dry_run": True,
             "would_call": ["ak.stock_hk_valuation_comparison_em",
                            "ak.stock_hk_security_profile_em"],
             "ts_code": code},
            fmt, timer=timer, request_id=request_id,
            table_render=lambda: print(f"would_call: stock_hk_valuation_comparison_em(symbol={code!r})"),
        )

    try:
        ak = import_akshare()
    except RuntimeError as e:
        return emit_failure(ExitCode.DEPENDENCY, str(e), fmt,
                            code="dependency_missing", retryable=False,
                            context={"required": "akshare", "install": "pip install akshare"},
                            timer=timer, request_id=request_id)

    try:
        val_df = ak.stock_hk_valuation_comparison_em(symbol=code)
    except Exception as e:
        return emit_failure(ExitCode.RUNTIME,
                            f"stock_hk_valuation_comparison_em failed: {type(e).__name__}: {e}",
                            fmt, code="runtime_error", retryable=True,
                            context={"endpoint": "stock_hk_valuation_comparison_em", "ts_code": code},
                            timer=timer, request_id=request_id)

    if val_df is None or len(val_df) == 0:
        return emit_failure(ExitCode.NO_DATA, f"no valuation row for {code}",
                            fmt, code="no_data", retryable=False,
                            context={"ts_code": code, "endpoint": "stock_hk_valuation_comparison_em"},
                            timer=timer, request_id=request_id)

    row = val_df.iloc[0]
    name = str(to_py(row.get("简称")) or "")
    name_source = "akshare"
    if not name:
        local_name = lookup_hk_name(code)
        if local_name:
            name = local_name
            name_source = "local_dict"
        else:
            name_source = "unknown"

    # Security profile is best-effort — failures don't fail the whole call.
    profile: dict[str, Any] = {}
    try:
        prof_df = ak.stock_hk_security_profile_em(symbol=code)
        if prof_df is not None and len(prof_df) > 0:
            prow = prof_df.iloc[0]
            profile = {
                "board": str(to_py(prow.get("板块")) or ""),
                "listing_date": str(to_py(prow.get("上市日期")) or ""),
                "security_type": str(to_py(prow.get("证券类型")) or ""),
                "lot_size": coerce_int(prow.get("每手股数")),
                "isin": str(to_py(prow.get("ISIN（国际证券识别编码）")) or ""),
                "stock_connect_sh": to_py(prow.get("是否沪港通标的")) == "是",
                "stock_connect_sz": to_py(prow.get("是否深港通标的")) == "是",
            }
    except Exception:
        profile = {}

    data = {
        "ts_code": code,
        "name": name,
        "pe_ttm": coerce_float(row.get("市盈率-TTM")),
        "pe_lyr": coerce_float(row.get("市盈率-LYR")),
        "pb_mrq": coerce_float(row.get("市净率-MRQ")),
        "pb_lyr": coerce_float(row.get("市净率-LYR")),
        "ps_ttm": coerce_float(row.get("市销率-TTM")),
        "pcf_ttm": coerce_float(row.get("市现率-TTM")),
        "sector_rank_pe_ttm": coerce_int(row.get("市盈率-TTM排名")),
        "sector_rank_pb_mrq": coerce_int(row.get("市净率-MRQ排名")),
        "profile": profile,
        "name_source": name_source,
        "source": "akshare:stock_hk_valuation_comparison_em + stock_hk_security_profile_em",
    }

    def table() -> None:
        print(f"ts_code: {data['ts_code']}  name: {data['name']}")
        print(f"PE_TTM={data['pe_ttm']}  PE_LYR={data['pe_lyr']}")
        print(f"PB_MRQ={data['pb_mrq']}  PB_LYR={data['pb_lyr']}")
        print(f"PS_TTM={data['ps_ttm']}  PCF_TTM={data['pcf_ttm']}")
        print(f"sector_rank_PE_TTM={data['sector_rank_pe_ttm']}  sector_rank_PB_MRQ={data['sector_rank_pb_mrq']}")
        if profile:
            print(f"board={profile.get('board')}  listing={profile.get('listing_date')}  lot={profile.get('lot_size')}")
            print(f"connect_sh={profile.get('stock_connect_sh')}  connect_sz={profile.get('stock_connect_sz')}")

    return emit_success(data, fmt, timer=timer, request_id=request_id, table_render=table)


def cmd_fundamentals(args, fmt, timer, request_id) -> int:
    try:
        code = normalize_hk_code(args.ts_code)
    except ValueError as e:
        return emit_failure(ExitCode.VALIDATION, str(e), fmt,
                            code="validation_error", retryable=False,
                            context={"value": args.ts_code},
                            timer=timer, request_id=request_id)

    if args.dry_run:
        return emit_success(
            {"dry_run": True,
             "would_call": "ak.stock_financial_hk_analysis_indicator_em",
             "ts_code": code, "period": args.period, "limit": args.limit},
            fmt, timer=timer, request_id=request_id,
            table_render=lambda: print(f"would_call: stock_financial_hk_analysis_indicator_em(symbol={code!r}, indicator={args.period!r})"),
        )

    try:
        ak = import_akshare()
    except RuntimeError as e:
        return emit_failure(ExitCode.DEPENDENCY, str(e), fmt,
                            code="dependency_missing", retryable=False,
                            context={"required": "akshare", "install": "pip install akshare"},
                            timer=timer, request_id=request_id)

    try:
        df = ak.stock_financial_hk_analysis_indicator_em(symbol=code, indicator=args.period)
    except Exception as e:
        return emit_failure(ExitCode.RUNTIME,
                            f"stock_financial_hk_analysis_indicator_em failed: {type(e).__name__}: {e}",
                            fmt, code="runtime_error", retryable=True,
                            context={"endpoint": "stock_financial_hk_analysis_indicator_em",
                                     "ts_code": code, "period": args.period},
                            timer=timer, request_id=request_id)

    if df is None or len(df) == 0:
        return emit_failure(ExitCode.NO_DATA,
                            f"no fundamentals for {code} ({args.period})",
                            fmt, code="no_data", retryable=False,
                            context={"ts_code": code, "period": args.period},
                            timer=timer, request_id=request_id)

    keep_cols = [
        "REPORT_DATE", "BPS", "BASIC_EPS", "DILUTED_EPS", "EPS_TTM",
        "OPERATE_INCOME", "OPERATE_INCOME_YOY", "HOLDER_PROFIT", "HOLDER_PROFIT_YOY",
        "GROSS_PROFIT_RATIO", "NET_PROFIT_RATIO",
        "ROE_YEARLY", "ROE_AVG", "ROA", "ROIC_YEARLY",
        "DEBT_ASSET_RATIO", "OCF_SALES", "TAX_EBT", "CURRENCY",
    ]
    present = [c for c in keep_cols if c in df.columns]
    rows = []
    for _, r in df.head(args.limit).iterrows():
        row: dict[str, Any] = {}
        for c in present:
            v = to_py(r.get(c))
            if isinstance(v, str):
                row[c.lower()] = v
            else:
                row[c.lower()] = coerce_float(v) if c not in ("REPORT_DATE", "CURRENCY") else v
        if "report_date" in row and row["report_date"] is not None:
            row["report_date"] = str(row["report_date"])[:10]
        rows.append(row)

    name_col = "SECURITY_NAME_ABBR"
    name = str(to_py(df.iloc[0].get(name_col)) or "") if name_col in df.columns else ""

    data = {
        "ts_code": code,
        "name": name,
        "period": args.period,
        "rows": rows,
        "source": "akshare:stock_financial_hk_analysis_indicator_em",
    }

    def table() -> None:
        print(f"ts_code: {code}  name: {name}  period: {args.period}  rows: {len(rows)}")
        for row in rows:
            d = row.get("report_date", "n/a")
            roe = row.get("roe_yearly")
            eps = row.get("eps_ttm") or row.get("basic_eps")
            bps = row.get("bps")
            margin = row.get("net_profit_ratio")
            print(f"  {d}  ROE_YEARLY={roe}  EPS_TTM={eps}  BPS={bps}  NET_MARGIN%={margin}")

    return emit_success(data, fmt, timer=timer, request_id=request_id, table_render=table)


def cmd_name(args, fmt, timer, request_id) -> int:
    try:
        code = normalize_hk_code(args.ts_code)
    except ValueError as e:
        return emit_failure(ExitCode.VALIDATION, str(e), fmt,
                            code="validation_error", retryable=False,
                            context={"value": args.ts_code},
                            timer=timer, request_id=request_id)

    if args.dry_run:
        return emit_success(
            {"dry_run": True, "would_call": "lookup_hk_name (local dict)",
             "ts_code": code},
            fmt, timer=timer, request_id=request_id,
            table_render=lambda: print(f"would_call: lookup_hk_name({code!r})"),
        )

    name = lookup_hk_name(code)
    if not name:
        return emit_failure(ExitCode.NO_DATA,
                            f"no local-dict entry for {code}",
                            fmt, code="no_data", retryable=False,
                            context={"ts_code": code,
                                     "hint": "use the 'valuation' subcommand for an AKShare lookup, "
                                             "or extend references/hk-ticker-name.json"},
                            timer=timer, request_id=request_id)

    data = {"ts_code": code, "name": name, "source": "local_dict"}

    def table() -> None:
        print(f"{code}: {name}  (local_dict)")

    return emit_success(data, fmt, timer=timer, request_id=request_id, table_render=table)


# ----- main -----

def main() -> int:
    p = argparse.ArgumentParser(
        description="HK valuation + fundamentals fallback via AKShare (no Tushare token)",
        epilog="Exit codes: 0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency",
    )
    add_common_args(p)
    sub = p.add_subparsers(dest="cmd")

    p_val = sub.add_parser("valuation", help="Current PE/PB/PS snapshot + profile")
    p_val.add_argument("--ts-code", dest="ts_code", required=True,
                       help="HK ticker, e.g. 00005.HK or 00005")
    add_common_args(p_val)

    p_fund = sub.add_parser("fundamentals", help="Annual/quarterly financial indicators")
    p_fund.add_argument("--ts-code", dest="ts_code", required=True)
    p_fund.add_argument("--period", choices=["年度", "中报", "季报"], default="年度")
    p_fund.add_argument("--limit", type=int, default=8)
    add_common_args(p_fund)

    p_name = sub.add_parser("name", help="Local-dict-only Chinese name lookup (no API call)")
    p_name.add_argument("--ts-code", dest="ts_code", required=True,
                        help="HK ticker, e.g. 00005.HK or 00005")
    add_common_args(p_name)

    args = p.parse_args()
    fmt = resolve_format(args.format)
    timer = Timer()
    request_id = new_request_id()

    if args.schema:
        return emit_schema(SCHEMA, fmt, timer=timer)

    if not args.cmd:
        return emit_failure(
            ExitCode.VALIDATION,
            "missing subcommand: choose one of valuation / fundamentals / name",
            fmt, code="validation_error", retryable=False,
            context={"valid_subcommands": ["valuation", "fundamentals", "name"]},
            timer=timer, request_id=request_id,
        )

    try:
        if args.cmd == "valuation":
            return cmd_valuation(args, fmt, timer, request_id)
        if args.cmd == "fundamentals":
            return cmd_fundamentals(args, fmt, timer, request_id)
        if args.cmd == "name":
            return cmd_name(args, fmt, timer, request_id)
    except Exception as e:
        return emit_failure(
            ExitCode.RUNTIME, f"{type(e).__name__}: {e}",
            fmt, code="runtime_error", retryable=False,
            context={"subcommand": args.cmd}, timer=timer, request_id=request_id,
        )

    return ExitCode.RUNTIME


if __name__ == "__main__":
    raise SystemExit(main())
