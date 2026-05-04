# Technical Indicator Cheatsheet

A curated 11-indicator list with usage notes and selection rules, adapted from `TradingAgents/tradingagents/agents/analysts/market_analyst.py` (system message). Use as scaffolding when daisy's report writer is filling §5 ("Financial performance and key drivers") or §4 ("Price and valuation snapshot") and the report needs a TA layer.

## When to use

- The user explicitly asked for technical analysis or chart context.
- The report's directional view depends on momentum / mean reversion / breakout logic, not just fundamentals.
- Pairing TA with the bull/bear debate prompts to harden the "Sources of evidence" claims.

## Skip when

- Pure fundamental research (DCF, dividend, balance sheet) — TA adds noise.
- Banks / insurers — leading TA on a financial-sector name is misleading; use RoTE / CET1 / NIM / payout instead (see `SKILL.md` §10).
- Long-horizon (multi-year) views — short-horizon TA signals don't matter on that scale.

## Selection rule

Pick **up to 8** indicators that give *complementary* information. Avoid redundancy — do not select two indicators that measure the same thing (e.g. RSI + StochRSI, or two SMAs of similar length). Briefly explain why each pick suits the current market regime.

---

## Moving Averages

| Indicator | What it tells you | Usage | Caveats |
|---|---|---|---|
| **close_50_sma** | Medium-term trend | Identify trend direction; dynamic support/resistance | Lags price; combine with faster indicators for timely entries |
| **close_200_sma** | Long-term trend benchmark | Confirm overall market trend; spot golden / death cross | Reacts slowly; for strategic confirmation, not frequent trades |
| **close_10_ema** | Responsive short-term momentum | Capture quick momentum shifts; potential entry points | Prone to noise in choppy markets; combine with longer averages to filter |

## MACD

| Indicator | What it tells you | Usage | Caveats |
|---|---|---|---|
| **macd** | Momentum via EMA differences | Crossovers and divergence = potential trend changes | In low-volatility / sideways tape, confirm with another indicator |
| **macds** | EMA smoothing of MACD line | Crossovers between MACD and signal trigger trades | False positives common — use as part of a broader strategy |
| **macdh** | Gap between MACD and signal | Visualize momentum strength; spot divergence early | Volatile; complement with additional filters in fast tape |

## Momentum

| Indicator | What it tells you | Usage | Caveats |
|---|---|---|---|
| **rsi** | Overbought / oversold momentum | 70 / 30 thresholds; watch for divergence | In strong trends, RSI stays extreme — always cross-check trend |

## Volatility

| Indicator | What it tells you | Usage | Caveats |
|---|---|---|---|
| **boll** | Bollinger middle (20 SMA) | Dynamic price benchmark | Use *with* the bands, not alone |
| **boll_ub** | Upper band (~2σ above middle) | Overbought zone; breakout signal | Prices can ride the band in strong trends |
| **boll_lb** | Lower band (~2σ below middle) | Oversold zone; potential reversal | Confirm with other tools before assuming reversal |
| **atr** | Average true range | Set stop-loss levels; size positions to volatility | Reactive — use as part of risk management, not entry timing |

## Volume

| Indicator | What it tells you | Usage | Caveats |
|---|---|---|---|
| **vwma** | Volume-weighted moving average | Confirm trends by combining price action with volume | Volume spikes can skew it; cross-check with raw volume |

---

## Worked picking example

**Context:** medium-term trend-following on a large-cap A-share, current tape is sideways / mildly bullish.

Pick: `close_50_sma` + `close_200_sma` + `macd` + `macdh` + `rsi` + `boll` + `atr` + `vwma` (8 total).

Rationale: two SMAs for trend frame; MACD line + histogram for momentum and divergence; RSI for overbought / oversold; Bollinger middle for mean-reversion benchmark; ATR for stop sizing in the sideways tape; VWMA to validate that the trend has volume behind it.

Avoid here: `close_10_ema` (redundant with `boll` middle on the short end), `boll_ub` / `boll_lb` (covered by interpreting price relative to `boll` + ATR rather than tracking both bands), `macds` (the histogram already captures the MACD-vs-signal information).

## Computing these

The indicator names above match the [`stockstats`](https://github.com/jealous/stockstats) column convention, so they can be computed directly via the bundled helper:

```bash
# Default 8 indicators (the worked picking example above), single value at as-of
python <skill-dir>/scripts/technical_indicators.py --ts-code 600519.SH

# Custom subset, last 5 trading days at-or-before 2026-04-15
python <skill-dir>/scripts/technical_indicators.py \
  --ts-code 00005.HK --indicators rsi,macd,boll \
  --as-of 20260415 --history 5

# US ticker (auto-routes to yfinance, no Tushare token needed)
python <skill-dir>/scripts/technical_indicators.py --ts-code AAPL
```

Routing is automatic by ts_code suffix:

- `*.SH` / `*.SZ` / `*.BJ` → Tushare `pro.daily` (needs `TUSHARE_TOKEN`)
- `*.HK` → Tushare `pro.hk_daily` (needs `TUSHARE_TOKEN`)
- bare ticker (no suffix) → `yfinance.download` (no token, optional dep)

The script applies a strict **look-ahead-bias guard** before stockstats runs: any OHLCV row with `Date > --as-of` is dropped, so a backtest at `--as-of 20240315` cannot accidentally use 16 March data. Optional deps (`tushare`, `yfinance`, `stockstats`) are lazy-imported, so `--help` / `--schema` / `--dry-run` work without any of them installed.

## Source

`TradingAgents/tradingagents/agents/analysts/market_analyst.py` lines 23–47 (system message, indicator block + selection rule). Daisy's adaptation drops the `get_stock_data` / `get_indicators` LangChain tool wiring — those are not part of the prompt itself. The computation helper in `scripts/technical_indicators.py` is a refactor of `TradingAgents/tradingagents/dataflows/stockstats_utils.py` for batch-indicator output and multi-market routing under the daisy envelope contract.
