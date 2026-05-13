# Data-source routing reference

The canonical "where do I look first?" table for daisy-financial-research, indexed by **(market × data type)**. When two sources can both answer a question, this doc fixes the order; when the primary source has a known gap, the fallback is documented inline.

This is the formal version of the routing logic that already lives in `SKILL.md` §3 and `CLAUDE.md` "Tushare gotchas" / "Search routing" — consolidated so an agent doesn't have to cross-reference both.

Adapted from `hsliuping/TradingAgents-CN:tradingagents/dataflows/providers/{china,hk,us}/`. The MongoDB cache, BaoStock provider, Finnhub adapter, and database-driven priority configs are intentionally **not** ported — they belong to a hosted product, not a portable skill.

## Hard rules

1. **Search-routing stack is committed.** Tushare for structured data, **Brave MCP** as primary web search, **Bailian WebSearch MCP** as Chinese / China-market supplement, Python for math, browser only for dynamic pages. **Asta / Semantic Scholar is explicitly not on the finance route.** Do not change without user sign-off.
2. **Tushare ts_codes use `YYYYMMDD` strings**, not `YYYY-MM-DD`. Tickers are `000001.SZ` / `600000.SH` / `00005.HK`.
3. **Look-ahead bias is a correctness bug.** Any historical / backtest call must filter rows where `Date > as_of` *before* downstream computation. `scripts/technical_indicators.py` already does this; new scripts must too.
4. **Lazy-import optional deps** so `--help` / `--schema` / `--dry-run` work without the upstream dep installed. Surface missing deps as `dependency_missing` (exit 5), not `runtime_error`.

## A-share (`*.SH` / `*.SZ` / `*.BJ`)

| Data type | Primary | Fallback | Notes |
|---|---|---|---|
| Daily OHLCV | `tushare.pro.daily` | (no fallback today; AKShare `stock_zh_a_hist` is a candidate but unimplemented) | Reliable. `daily(ts_code, start_date, end_date)`. |
| Daily valuation (PE/PB/total_mv) | `tushare.pro.daily_basic` | — | Use `fields=` to limit payload. |
| Income / balance / cash flow | `tushare.pro.income` / `pro.balancesheet` / `pro.cashflow` | — | Period as `YYYYMMDD` quarter-end. |
| Financial ratios | `tushare.pro.fina_indicator` | — | ROE / ROA / margin / leverage / growth. |
| Forecast / express | `tushare.pro.forecast` / `pro.express` | — | Earnings surprise driver. |
| Northbound / Stock Connect flow | `tushare.pro.moneyflow_hsgt` | — | Aggregate inflow per day. |
| Stock universe | `tushare.pro.stock_basic(list_status='L')` | — | Filter by exchange / industry / market cap. |
| Index daily | `tushare.pro.index_daily` (`000300.SH`) | — | Used as benchmark for `dexter_memory_log` `auto-resolve`. |
| Concept / ETF | `tushare.pro.concept` / `pro.fund_basic` | — | |
| Technical indicators | `scripts/technical_indicators.py` (auto-routes via `pro.daily`) | — | SMA / MACD / RSI / Bollinger / ATR / VWMA via stockstats. |
| Operating segments (主营构成) | `scripts/segments.py` (AKShare `stock_zygc_em`) | — | Rows by `分类类型`: 按产品 / 按地区 / 按行业. Free, no token. |
| Screening | `scripts/screen_a_share.py` | — | Preset registry in `references/stock-screening-presets.md`. |
| Web context | Brave MCP | Bailian WebSearch MCP | Use Bailian for China-only news / regulation. |

**Auth:** `TUSHARE_TOKEN` env var. Missing token → `auth_missing` (exit 2).

## Hong Kong (`*.HK`)

| Data type | Primary | Fallback | Notes |
|---|---|---|---|
| Daily OHLCV | `tushare.pro.hk_daily` | — | `hk_daily(ts_code='00005.HK', start_date, end_date)`. |
| Stock basic / list | `tushare.pro.hk_basic` | — | |
| **Daily valuation (PE/PB/PS)** | ⚠️ `tushare.pro.hk_daily_basic` returns `请指定正确的接口名` in the test env — **treat as unavailable** | `scripts/akshare_hk_valuation.py valuation --ts-code <code>` (AKShare `stock_hk_valuation_comparison_em` + `stock_hk_security_profile_em`) | The fallback is the documented primary today. No Tushare token. |
| Fundamentals (ROE/EPS/BPS/leverage time series) | `scripts/akshare_hk_valuation.py fundamentals` (AKShare `stock_financial_hk_analysis_indicator_em`) | — | Period: `年度` / `中报` / `季报`. |
| Chinese short name | AKShare row `简称` | `references/hk-ticker-name.json` (~30 majors) → `''` | `akshare_hk_valuation.py` automatically falls through this chain; emits `name_source: akshare \| local_dict \| unknown`. Local-only lookup also exposed as `... name --ts-code <code>`. |
| Stock Connect (港股通) universe | `tushare.pro.hk_hold(trade_date='YYYYMMDD')` | — | Search backward for trading days; `scripts/hk_connect_universe.py` automates this. |
| Stock Connect flow | `tushare.pro.ggt_top10` / `pro.ggt_daily` / `pro.moneyflow_hsgt` | — | |
| HSI benchmark | `tushare.pro.index_daily` | `tushare.pro.index_global` → `tushare.pro.hk_daily` → AKShare `stock_hk_index_daily_sina` | `dexter_memory_log auto-resolve` walks this chain automatically. |
| Stock Connect screening | `scripts/screen_hk_connect.py` (only when 港股通 is explicitly requested) | — | |
| Technical indicators | `scripts/technical_indicators.py` (auto-routes via `pro.hk_daily`) | — | |
| Operating segments | (no free structured API) | `read_filings` / Brave MCP on the annual report's "Segment Information" note | `scripts/segments.py --ts-code <code>.HK` short-circuits to `no_data` (exit 4) with a `hint` pointing here. |
| Web context | Brave MCP | Bailian WebSearch MCP | |

**Auth:** `TUSHARE_TOKEN` for Tushare endpoints; AKShare needs no token. Missing token → `auth_missing` (exit 2) when the call is Tushare-only.

**Bank-specific note.** For HSBC / Standard Chartered / mainland banks listed in HK, prefer the AKShare fundamentals path for RoTE / RoE / leverage; DCF is the wrong primary frame. See `SKILL.md` §10 and `references/hsbc-hk-bank-research-test-20260429.md`.

## US (bare ticker, no suffix)

| Data type | Primary | Fallback | Notes |
|---|---|---|---|
| Daily OHLCV | `yfinance.download` (lazy-imported, optional `us` extra) | — | Used by `scripts/technical_indicators.py` and `scripts/dexter_memory_log.py compute-returns` for US tickers. |
| SPY benchmark | `yfinance.download('SPY')` | — | `dexter_memory_log auto-resolve` benchmark. |
| Fundamentals | yfinance `Ticker(...).financials` (not currently used by daisy scripts) | — | If you need it, prefer pulling structured data from filings via Brave MCP search → SEC links. |
| Technical indicators | `scripts/technical_indicators.py` | — | |
| Operating segments | (no free structured API) | `read_filings` / Brave MCP on the latest 10-K Note "Segment Reporting" | `scripts/segments.py --ts-code AAPL` short-circuits to `no_data` (exit 4) with a `hint` pointing here. |
| Web context | Brave MCP | (Bailian is China-tilted, less useful for US news) | |

**Auth:** none for yfinance. yfinance is rate-limited; reuse cached data when possible.

## Long tail / out-of-scope

These are explicitly **not** routed today; flag the gap and ask the user before reaching for them:

- **Crypto** — out of scope.
- **Options / futures / derivatives** — out of scope.
- **Intraday tick data** — out of scope; daisy is research-grade, not execution-grade.
- **Alternative data (satellite / shipping / sentiment proper)** — out of scope.
- **OpenBB SDK** — would conflict with the committed Tushare + Brave + Bailian stack; do not add without user sign-off.

## Failure modes and what they mean

| Symptom | Likely cause | Action |
|---|---|---|
| `请指定正确的接口名` from `pro.hk_daily_basic` | Tushare plan does not include the HK valuation interface | Use `akshare_hk_valuation.py valuation` instead. |
| `auth_missing` (exit 2) on any Tushare call | `TUSHARE_TOKEN` env var unset or expired | Surface to user; do not retry. |
| `no_data` (exit 4) with `retryable: true` | Empty result after filters / lookback exhausted | Loosen filters, extend `--lookback-days`, check ticker. |
| `dependency_missing` (exit 5) | Optional dep not installed (akshare / yfinance / stockstats) | Run `uv sync --extra <ta\|us\|akshare\|all-extras>`. |
| Empty `name` after AKShare valuation call | Network glitch or unusual ticker | Local dict at `references/hk-ticker-name.json` covers the ~30 majors automatically; `name_source` on the response tells you which path won. |

## How agents should use this doc

1. Read **before** the plan step, not during. The agent's plan should already reference the right primary call by the time it gets to data-gathering.
2. When a primary fails, *do not retry the same endpoint*; jump to the documented fallback. This is the soft-loop-limit pattern in `SKILL.md` §4.
3. If a market × data-type cell is empty, that's the documented gap. Either propose an alternative analysis or escalate to the user — don't fabricate.

## Source

Routing pattern adapted from `hsliuping/TradingAgents-CN:tradingagents/dataflows/providers/{china,hk,us}/` (Apache-2.0 portion). HK ticker→name dict ported from the same repo's `providers/hk/improved_hk.py`. Daisy's adaptation strips the MongoDB cache layer, BaoStock provider, and database-driven priority configs — those assume hosted infrastructure that a portable skill should not require.
