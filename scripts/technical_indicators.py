#!/usr/bin/env python3
"""Technical indicator calculator for daisy-financial-research.

Computes pandas-based technical indicators (SMA / EMA / MACD / RSI /
Bollinger / ATR / VWMA / etc.) at a point-in-time, with a strict
look-ahead-bias guard so backtests and historical analyses never see
future bars.

Data routing (auto by ts_code suffix):
  *.SH / *.SZ / *.BJ -> Tushare pro.daily      (needs TUSHARE_TOKEN)
  *.HK              -> Tushare pro.hk_daily    (needs TUSHARE_TOKEN)
  bare ticker (US)  -> yfinance.download       (no token, optional dep)

Indicator names follow stockstats conventions; see
references/technical-indicator-cheatsheet.md for the curated list.

Agent-native conventions
  --format json|table   stdout shape; auto-JSON when stdout is not a TTY
  --schema              emit parameter schema, then exit
  --dry-run             preview the request shape; no upstream call
  Exit codes            0 ok . 1 runtime . 2 auth . 3 validation . 4 no_data . 5 dependency

Source: design ported from TauricResearch/TradingAgents:
tradingagents/dataflows/stockstats_utils.py (one-indicator-at-a-time
version) - refactored for batch indicator output, multi-market routing,
and the daisy envelope contract.
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timedelta
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


DEFAULT_INDICATORS = [
    "close_50_sma", "close_200_sma",
    "macd", "macdh",
    "rsi",
    "boll", "atr", "vwma",
]

# Curated list from references/technical-indicator-cheatsheet.md. Names
# beyond this list are passed through to stockstats as-is and any KeyError
# is surfaced as a validation_error with the offending name.
KNOWN_INDICATORS = {
    "close_10_ema", "close_50_sma", "close_200_sma",
    "macd", "macds", "macdh",
    "rsi",
    "boll", "boll_ub", "boll_lb",
    "atr",
    "vwma",
}

INDICATOR_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
TS_CODE_RE = re.compile(r"^[A-Z0-9.]{1,10}(\.(SH|SZ|BJ|HK))?$", re.IGNORECASE)
DATE_RE = re.compile(r"^\d{8}$")


SCHEMA = {
    "name": "technical_indicators",
    "description": "Compute technical indicators at a point-in-time with look-ahead-bias guard",
    "params": {
        "ts_code": {
            "type": "string", "required": True,
            "description": "Ticker. *.SH/SZ/BJ -> tushare pro.daily; *.HK -> pro.hk_daily; bare -> yfinance",
        },
        "indicators": {
            "type": "string",
            "default": ",".join(DEFAULT_INDICATORS),
            "description": "Comma-separated stockstats indicator names",
        },
        "as_of": {
            "type": "string", "default": "today",
            "description": "Cutoff date YYYYMMDD or 'today'; rows after this are dropped",
        },
        "lookback_days": {
            "type": "integer", "default": 400,
            "description": "Calendar days of OHLCV history to fetch before --as-of (>=30)",
        },
        "history": {
            "type": "integer", "default": 1,
            "description": "Trading days returned per indicator at-or-before --as-of (1 = single value)",
        },
    },
    "data_sources": {
        "*.SH/SZ/BJ": "tushare:pro.daily",
        "*.HK": "tushare:pro.hk_daily",
        "bare ticker (US)": "yfinance:download (lazy-imported, optional extra)",
    },
    "supported_indicators_curated": sorted(KNOWN_INDICATORS),
    "default_indicators": DEFAULT_INDICATORS,
    "look_ahead_bias_guard": "rows where Date > --as-of are filtered before stockstats runs",
    "error_codes": ["validation_error", "auth_missing", "no_data", "runtime_error", "dependency_missing"],
    "deps": {
        "always": ["pandas", "stockstats"],
        "for_a_share_or_hk": ["tushare"],
        "for_us": ["yfinance"],
    },
    "auth": {"env_var": "TUSHARE_TOKEN", "required_for_suffixes": [".SH", ".SZ", ".BJ", ".HK"]},
}


def parse_indicators(arg: str) -> list[str]:
    items = [s.strip().lower() for s in arg.split(",") if s.strip()]
    if not items:
        raise ValueError("empty --indicators list")
    bad = [s for s in items if not INDICATOR_NAME_RE.match(s)]
    if bad:
        raise ValueError(f"invalid indicator name(s): {bad} (expected lowercase a-z0-9_, 2-31 chars)")
    return items


def resolve_market(ts_code: str) -> str:
    """Return one of 'a_share', 'hk', 'us' based on suffix."""
    s = ts_code.strip().upper()
    if s.endswith(".SH") or s.endswith(".SZ") or s.endswith(".BJ"):
        return "a_share"
    if s.endswith(".HK"):
        return "hk"
    return "us"


def parse_as_of(s: str) -> datetime:
    if s == "today":
        return datetime.now()
    if not DATE_RE.match(s):
        raise ValueError(f"--as-of must be YYYYMMDD or 'today', got {s!r}")
    return datetime.strptime(s, "%Y%m%d")


def fetch_a_share_or_hk(market: str, ts_code: str, start: str, end: str):
    """Fetch OHLCV via Tushare for A-share or HK. Returns DataFrame with
    capitalized columns (Date/Open/High/Low/Close/Volume) so stockstats.wrap
    can consume it directly.
    """
    import os
    import pandas as pd
    try:
        import tushare as ts
    except ImportError as e:
        raise RuntimeError(f"tushare not installed: {e}") from e
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        raise PermissionError("TUSHARE_TOKEN env var required for A-share / HK indicators")
    pro = ts.pro_api(token)
    fn = pro.daily if market == "a_share" else pro.hk_daily
    df = fn(ts_code=ts_code.upper(), start_date=start, end_date=end)
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.sort_values("trade_date").reset_index(drop=True)
    out = pd.DataFrame({
        "Date": pd.to_datetime(df["trade_date"], format="%Y%m%d"),
        "Open": pd.to_numeric(df.get("open"), errors="coerce"),
        "High": pd.to_numeric(df.get("high"), errors="coerce"),
        "Low": pd.to_numeric(df.get("low"), errors="coerce"),
        "Close": pd.to_numeric(df.get("close"), errors="coerce"),
        "Volume": pd.to_numeric(df.get("vol"), errors="coerce"),
    })
    return out.dropna(subset=["Close"]).reset_index(drop=True)


def fetch_us(ticker: str, start: str, end: str):
    """Fetch OHLCV via yfinance. start/end in YYYYMMDD."""
    import pandas as pd
    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError(f"yfinance not installed: {e}") from e
    s = datetime.strptime(start, "%Y%m%d").strftime("%Y-%m-%d")
    e = datetime.strptime(end, "%Y%m%d").strftime("%Y-%m-%d")
    df = yf.download(ticker, start=s, end=e, multi_level_index=False,
                     progress=False, auto_adjust=True)
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.reset_index()
    out = pd.DataFrame({
        "Date": pd.to_datetime(df["Date"]),
        "Open": pd.to_numeric(df.get("Open"), errors="coerce"),
        "High": pd.to_numeric(df.get("High"), errors="coerce"),
        "Low": pd.to_numeric(df.get("Low"), errors="coerce"),
        "Close": pd.to_numeric(df.get("Close"), errors="coerce"),
        "Volume": pd.to_numeric(df.get("Volume"), errors="coerce"),
    })
    return out.dropna(subset=["Close"]).reset_index(drop=True)


def compute_indicators(ohlcv, indicators: list[str], as_of: datetime, history: int):
    """Apply look-ahead-bias guard, run stockstats, return
    (results, n_rows_used). results is dict[indicator -> list[(date_str, value)]].
    Raises KeyError(indicator_name) if stockstats does not recognize one.
    """
    import pandas as pd
    try:
        from stockstats import wrap
    except ImportError as e:
        raise RuntimeError(f"stockstats not installed: {e}") from e
    cutoff = pd.Timestamp(as_of)
    df = ohlcv[ohlcv["Date"] <= cutoff].copy()
    n_rows_used = int(len(df))
    if df.empty:
        return {}, 0
    sdf = wrap(df)
    dates = pd.to_datetime(sdf["Date"]).dt.strftime("%Y-%m-%d").tolist()
    out: dict[str, list[tuple[str, float]]] = {}
    for ind in indicators:
        try:
            series = sdf[ind]
        except KeyError as e:
            raise KeyError(ind) from e
        values = series.tolist()
        pairs: list[tuple[str, float]] = []
        for d, v in zip(dates, values):
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv != fv:  # NaN
                continue
            pairs.append((d, fv))
        out[ind] = pairs[-history:] if pairs else []
    return out, n_rows_used


def main() -> int:
    p = argparse.ArgumentParser(
        description="Compute technical indicators at a point-in-time with look-ahead-bias guard",
        epilog="Exit codes: 0 ok . 1 runtime . 2 auth . 3 validation . 4 no_data . 5 dependency",
    )
    p.add_argument("--ts-code", dest="ts_code",
                   help="Ticker. *.SH/SZ/BJ -> tushare pro.daily; *.HK -> pro.hk_daily; bare -> yfinance")
    p.add_argument("--indicators", default=",".join(DEFAULT_INDICATORS),
                   help=f"Comma-separated stockstats names (default: {','.join(DEFAULT_INDICATORS)})")
    p.add_argument("--as-of", dest="as_of", default="today",
                   help="Cutoff YYYYMMDD or 'today'; rows after this are dropped")
    p.add_argument("--lookback-days", dest="lookback_days", type=int, default=400,
                   help="Calendar days of OHLCV history to fetch before --as-of (default 400, min 30)")
    p.add_argument("--history", type=int, default=1,
                   help="Trading days returned per indicator (default 1)")
    add_common_args(p)

    args = p.parse_args()
    fmt = resolve_format(args.format)
    timer = Timer()
    request_id = new_request_id()

    if args.schema:
        return emit_schema(SCHEMA, fmt, timer=timer)

    if not args.ts_code:
        return emit_failure(ExitCode.VALIDATION, "missing --ts-code", fmt,
                            code="validation_error", retryable=False,
                            context={"required": "--ts-code"},
                            timer=timer, request_id=request_id)

    ts_code = args.ts_code.strip()
    if not TS_CODE_RE.match(ts_code):
        return emit_failure(ExitCode.VALIDATION,
                            f"unrecognized ts_code: {ts_code!r}", fmt,
                            code="validation_error", retryable=False,
                            context={"ts_code": ts_code,
                                     "expected": "e.g. 600519.SH / 00005.HK / AAPL"},
                            timer=timer, request_id=request_id)

    try:
        indicators = parse_indicators(args.indicators)
    except ValueError as e:
        return emit_failure(ExitCode.VALIDATION, str(e), fmt,
                            code="validation_error", retryable=False,
                            context={"indicators": args.indicators},
                            timer=timer, request_id=request_id)

    try:
        as_of_dt = parse_as_of(args.as_of)
    except ValueError as e:
        return emit_failure(ExitCode.VALIDATION, str(e), fmt,
                            code="validation_error", retryable=False,
                            context={"as_of": args.as_of},
                            timer=timer, request_id=request_id)

    if args.lookback_days < 30:
        return emit_failure(ExitCode.VALIDATION,
                            f"--lookback-days must be >= 30, got {args.lookback_days}", fmt,
                            code="validation_error", retryable=False,
                            context={"lookback_days": args.lookback_days, "min": 30},
                            timer=timer, request_id=request_id)
    if args.history < 1:
        return emit_failure(ExitCode.VALIDATION,
                            f"--history must be >= 1, got {args.history}", fmt,
                            code="validation_error", retryable=False,
                            context={"history": args.history, "min": 1},
                            timer=timer, request_id=request_id)

    market = resolve_market(ts_code)
    start_dt = as_of_dt - timedelta(days=args.lookback_days)
    start_str = start_dt.strftime("%Y%m%d")
    end_str = as_of_dt.strftime("%Y%m%d")

    if args.dry_run:
        if market == "a_share":
            would_call = "tushare.pro.daily"
        elif market == "hk":
            would_call = "tushare.pro.hk_daily"
        else:
            would_call = "yfinance.download"
        return emit_success(
            {"dry_run": True, "would_call": would_call,
             "ts_code": ts_code, "market": market,
             "indicators": indicators,
             "window": {"start": start_str, "end": end_str},
             "lookback_days": args.lookback_days, "history": args.history,
             "as_of": as_of_dt.strftime("%Y-%m-%d")},
            fmt, timer=timer, request_id=request_id,
            table_render=lambda: print(
                f"would_call: {would_call}({ts_code} {start_str}..{end_str}); "
                f"indicators={indicators}"),
        )

    # ----- live path -----
    try:
        if market in ("a_share", "hk"):
            try:
                ohlcv = fetch_a_share_or_hk(market, ts_code, start_str, end_str)
            except PermissionError as e:
                return emit_failure(ExitCode.AUTH, str(e), fmt,
                                    code="auth_missing", retryable=False,
                                    context={"env_var": "TUSHARE_TOKEN", "ts_code": ts_code},
                                    timer=timer, request_id=request_id)
            except RuntimeError as e:
                return emit_failure(ExitCode.DEPENDENCY, str(e), fmt,
                                    code="dependency_missing", retryable=False,
                                    context={"required": "tushare", "install": "pip install tushare"},
                                    timer=timer, request_id=request_id)
        else:
            try:
                ohlcv = fetch_us(ts_code, start_str, end_str)
            except RuntimeError as e:
                return emit_failure(ExitCode.DEPENDENCY, str(e), fmt,
                                    code="dependency_missing", retryable=False,
                                    context={"required": "yfinance", "install": "pip install yfinance"},
                                    timer=timer, request_id=request_id)
    except Exception as e:
        return emit_failure(ExitCode.RUNTIME,
                            f"OHLCV fetch failed: {type(e).__name__}: {e}", fmt,
                            code="runtime_error", retryable=True,
                            context={"market": market, "ts_code": ts_code,
                                     "window": f"{start_str}..{end_str}"},
                            timer=timer, request_id=request_id)

    if ohlcv is None or len(ohlcv) == 0:
        return emit_failure(ExitCode.NO_DATA,
                            f"no OHLCV data returned for {ts_code} in {start_str}..{end_str}", fmt,
                            code="no_data", retryable=True,
                            context={"ts_code": ts_code, "window": f"{start_str}..{end_str}",
                                     "hint": "extend --lookback-days or check the ticker"},
                            timer=timer, request_id=request_id)

    try:
        results, n_rows_used = compute_indicators(ohlcv, indicators, as_of_dt, args.history)
    except KeyError as e:
        bad_name = e.args[0] if e.args else "unknown"
        return emit_failure(ExitCode.VALIDATION,
                            f"unknown indicator: {bad_name!r}", fmt,
                            code="validation_error", retryable=False,
                            context={"indicator": bad_name,
                                     "known_curated": sorted(KNOWN_INDICATORS),
                                     "hint": "see references/technical-indicator-cheatsheet.md"},
                            timer=timer, request_id=request_id)
    except RuntimeError as e:
        return emit_failure(ExitCode.DEPENDENCY, str(e), fmt,
                            code="dependency_missing", retryable=False,
                            context={"required": "stockstats", "install": "pip install stockstats"},
                            timer=timer, request_id=request_id)

    if not results or n_rows_used == 0:
        return emit_failure(ExitCode.NO_DATA,
                            f"no rows on or before {as_of_dt.strftime('%Y-%m-%d')} for {ts_code}", fmt,
                            code="no_data", retryable=True,
                            context={"ts_code": ts_code, "as_of": as_of_dt.strftime("%Y-%m-%d"),
                                     "hint": "extend --lookback-days or pick a later --as-of"},
                            timer=timer, request_id=request_id)

    indicator_payload: dict[str, Any] = {}
    for ind, pairs in results.items():
        if not pairs:
            indicator_payload[ind] = None
        elif args.history == 1:
            d, v = pairs[-1]
            indicator_payload[ind] = {"date": d, "value": v}
        else:
            indicator_payload[ind] = [{"date": d, "value": v} for d, v in pairs]

    source = (
        "tushare:pro.daily" if market == "a_share" else
        "tushare:pro.hk_daily" if market == "hk" else
        "yfinance:download"
    )

    data = {
        "ts_code": ts_code,
        "market": market,
        "as_of": as_of_dt.strftime("%Y-%m-%d"),
        "history": args.history,
        "n_rows_used": n_rows_used,
        "indicators": indicator_payload,
        "source": source,
    }

    def table() -> None:
        print(f"ts_code: {ts_code}  market: {market}  as_of: {data['as_of']}  rows: {n_rows_used}")
        for ind, payload in indicator_payload.items():
            if payload is None:
                print(f"  {ind}: N/A (insufficient data)")
            elif isinstance(payload, dict):
                v = payload["value"]
                shown = f"{v:.4f}" if abs(v) < 1e6 else f"{v:.2e}"
                print(f"  {ind} @ {payload['date']}: {shown}")
            else:
                tail = ", ".join(f"{p['date']}:{p['value']:.4f}" for p in payload)
                print(f"  {ind}: {tail}")

    return emit_success(data, fmt, timer=timer, request_id=request_id, table_render=table)


if __name__ == "__main__":
    raise SystemExit(main())
