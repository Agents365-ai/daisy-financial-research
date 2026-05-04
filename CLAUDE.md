# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A multi-platform agent skill (`daisy-financial-research`) for stock / company / sector research, DCF valuation, and stock screening. The local working directory is `dexter-financial-research/` (legacy name retained); the published name and the GitHub repo are `daisy-financial-research`. Loaded by Claude Code, Opencode, OpenClaw / ClawHub, Hermes, OpenAI Codex, and SkillsMP.

The canonical user-facing contract is `SKILL.md`; everything else (`scripts/`, `references/`, `templates/`, `agents/openai.yaml`) supports it. When changing behavior the user will see, update `SKILL.md` in lockstep with the scripts — they reference each other by exact path and CLI flag.

## Runtime layout

By default, every script writes under the user's current working directory:

- `./financial-research/reports/` — final reports (md/html/pdf).
- `./financial-research/watchlists/` — screener outputs.
- `./financial-research/scratchpad/` — per-task JSONL scratchpads.
- `./financial-research/universes/` — HK Connect universe exports.

Each script accepts `--out-dir <root>` to redirect the root; subdirs are appended automatically. Hermes users who want the legacy `~/.hermes/reports/financial-research/<subdir>/` layout pass `--out-dir ~/.hermes/reports/financial-research`.

`TUSHARE_TOKEN` env var is required for any `screen_*`, `hk_connect_universe.py`, or other Tushare-backed call.

`SKILL.md` examples use a bare `python` placeholder; the caller substitutes whichever interpreter has `tushare`, `pandas`, `requests` installed (system `python3`, `~/.hermes/venv/bin/python`, `~\.hermes\venv\Scripts\python.exe`, conda, `uv run python`, pyenv...). Do not reintroduce hardcoded interpreter paths — the convention is documented in `SKILL.md` under "Python interpreter convention".

`SKILL.md` references scripts by the placeholder `<this-skill-dir>/scripts/X.py` (the same placeholder convention drawio-skill uses). The agent runtime substitutes the actual install dir. Do not reintroduce hardcoded `~/.<platform>/skills/.../scripts/` paths in SKILL.md.

Renaming a script is a breaking change to the skill contract.

## Scripts

- `scripts/dexter_scratchpad.py` — `init` / `add` / `show` subcommands; appends JSONL records of tool calls and results. Default output: `./financial-research/scratchpad/`. Per-task only.
- `scripts/dexter_memory_log.py` — cross-session, cross-ticker decision log. Subcommands: `record` (append pending entry, idempotent on (date, ticker)), `resolve` (replace pending tag with realized returns + append REFLECTION via atomic rewrite), `list` / `context` / `stats`, `backtest` (risk-adjusted decision-level metrics across a `--from`/`--to` window: per-rating mean / hit-rate / `alpha_t_stat` / `annualized_alpha_pct` / `annualized_alpha_sortino_like` plus a cumulative-alpha curve and its max drawdown — explicitly *not* a portfolio Sharpe ratio since daisy logs decisions, not a continuous NAV), plus `compute-returns` (fetch close[decision]/close[as_of]/benchmark, compute raw + alpha; no log mutation) and `auto-resolve` (compute + resolve in one call — closes the resolve loop). Benchmark routing by ticker suffix: `*.SH/SZ/BJ` → 000300.SH (CSI 300) via `pro.index_daily`; `*.HK` → HSI via `pro.index_daily` / `pro.index_global` / `pro.hk_daily` fallback chain, with AKShare `stock_hk_index_daily_sina` as final fallback (lazy-imported); US tickers → SPY via `yfinance` (lazy-imported). Single Markdown file at `./financial-research/memory/decision-log.md`. Format ported from `TradingAgents/tradingagents/agents/utils/memory.py`: `<!-- ENTRY_END -->` separator, tag-line `[date | ticker | rating | …]`, `DECISION:` / `REFLECTION:` body sections. Rating enum: Buy / Overweight / Hold / Underweight / Sell.
- `scripts/financial_report.py` — copies a Markdown source to the reports dir and renders HTML; `--pdf` adds PDF (best-effort, may no-op if no HTML→PDF tool is available). Default output: `./financial-research/reports/`.
- `scripts/hk_connect_universe.py` — `pro.hk_hold(...)` based HK Stock Connect (港股通) universe export. Searches backward when the requested date has no data. Default output: `./financial-research/universes/`.
- `scripts/screen_a_share.py` — A-share screener with named presets (see `references/stock-screening-presets.md`); `--report` emits a Markdown source that `financial_report.py` can render. Default outputs: `./financial-research/watchlists/` (csv/json) and `./financial-research/reports/` (when `--report`).
- `scripts/screen_hk_connect.py` — HK Stock Connect screener; only used when 港股通 is explicitly requested. Default output: `./financial-research/watchlists/`.
- `scripts/akshare_hk_valuation.py` — HK valuation + fundamentals fallback via AKShare. Subcommands `valuation` (PE/PB/PS snapshot + Stock Connect eligibility via `stock_hk_valuation_comparison_em` + `stock_hk_security_profile_em`), `fundamentals` (ROE/EPS/BPS/leverage time series via `stock_financial_hk_analysis_indicator_em`), and `name` (local-dict-only Chinese-name lookup, no API call). No Tushare token. Closes the documented `pro.hk_daily_basic` gap. AKShare is lazy-imported, so `--help` / `--schema` / `--dry-run` work without the optional dep installed; live calls return `dependency_missing` (exit=5) when akshare is absent. The `valuation` subcommand falls through `akshare row.简称 → references/hk-ticker-name.json → ''` for the Chinese name and reports the winning leg as `name_source`.
- `scripts/technical_indicators.py` — point-in-time technical-indicator calculator (SMA/EMA/MACD/RSI/Bollinger/ATR/VWMA via `stockstats`). Auto-routes by ts_code suffix: `*.SH/SZ/BJ` → `pro.daily`, `*.HK` → `pro.hk_daily`, bare → `yfinance.download`. Look-ahead-bias guard filters rows by `Date <= --as-of` before stockstats runs, so backtests cannot see future bars. Default indicators are the 8 from `references/technical-indicator-cheatsheet.md` "Worked picking example". `tushare`/`yfinance`/`stockstats` are all lazy-imported; `--help`/`--schema`/`--dry-run` work without any of them. Read-only (no file output, no `--out-dir`). Design ported from `TradingAgents/tradingagents/dataflows/stockstats_utils.py`, refactored for batch indicator output and multi-market routing.

All mutating scripts accept `--out-dir <root>`; subdirs are appended automatically. `technical_indicators.py` is read-only and has no `--out-dir`.

## Agent-native CLI contract

Every script under `scripts/` shares a uniform contract enforced via `scripts/_envelope.py`:

- `--format json|table` — auto-JSON when stdout is not a TTY, else table (legacy prose). Set `DAISY_FORCE_JSON=1` to force JSON regardless of TTY.
- `--schema` — emits the script's full parameter/output/error schema as a JSON envelope. Add new params *and* update `SCHEMA` in the same script in lockstep — `--schema` is the agent's primary discovery surface, not `--help`.
- `--dry-run` — preview the request shape; never call upstream APIs or write files. Implemented on every mutating script.
- Exit codes: `0` ok · `1` runtime · `2` auth · `3` validation · `4` no_data · `5` dependency. Documented in each `--help` epilog.
- Success envelope: `{"ok": true, "data": ..., "meta": {schema_version, request_id, latency_ms}}`.
- Error envelope: `{"ok": false, "error": {code, message, retryable, context}, "meta": {...}}`.
- `_envelope.emit_progress(event, **fields)` writes one NDJSON line to stderr — used by long-running operations (`screen_hk_connect.py --with-momentum`, `financial_report.py`) so agents can detect liveness.

When adding a new script: import from `_envelope`, define a `SCHEMA` dict, call `add_common_args(parser)`, and route success/error through `emit_success` / `emit_failure`. Keep the human table render as a `table_render` callback so `--format table` users see no regression.

`scripts/_envelope.py::SCHEMA_VERSION` is the contract version exposed to agents in every `meta` block. Bump it (semver) when the envelope shape changes in a way that breaks downstream parsers.

## Tests (local-only, not in the published artifact)

`tests/` is gitignored — the contract suite lives on disk for local development but is not part of the published skill that users `git clone` into their `.claude/skills/` (or equivalent) directory. To run it locally:

```bash
uv sync --all-extras
uv run pytest tests/        # 74 tests, ~11 s, no Tushare token, no network
```

Coverage: `--help` / `--schema` / `--dry-run` invariants across all 8 scripts, validation/no_data error envelopes, `DAISY_FORCE_JSON` override, full memory-log lifecycle (record idempotency → resolve atomic rewrite → list/context/stats/backtest), on-disk format wire-compatibility with TradingAgents `memory.py`, plus `compute-returns` / `auto-resolve` dry-run + validation paths, `technical_indicators` market-routing dry-run, `akshare_hk_valuation name` local-dict lookup, `backtest` aggregate math (mean alpha, hit rate, t-stat, annualized alpha, cumulative-alpha drawdown, window/rating filters), and `record --rating` tolerant extraction (canonical word, markdown bold, lowercase, full synthesis paragraph, plus rejection of input with no 5-tier word). See `tests/README.md`. Run before committing any change to `scripts/`.

## Tushare gotchas (verified in this env)

- `pro.hk_daily_basic(...)` returns `请指定正确的接口名` — treat as unavailable. Fallback: `scripts/akshare_hk_valuation.py valuation --ts-code <code>` covers PE/PB/PS snapshot; `... fundamentals --ts-code <code>` covers ROE/EPS/BPS time series.
- `pro.hk_basic`, `pro.hk_daily`, `pro.hk_hold`, `pro.ggt_top10`, `pro.ggt_daily`, `pro.moneyflow_hsgt` are known-working.
- Date format is `YYYYMMDD` strings (not `YYYY-MM-DD`), ts_codes are `000001.SZ` / `600000.SH` / `00005.HK`.

The full per-market routing table (A-share / HK / US, primary + documented fallback chain for each data type) lives at `references/data-source-routing.md`. New scripts that route across markets should reference it from their `--help` epilog or `SCHEMA["data_sources"]` block instead of duplicating the routing rules.

## Search routing (do not change without user sign-off)

The skill commits to a specific finance-search stack: Tushare for structured data, **Brave MCP** as primary web search, **Bailian WebSearch MCP** as Chinese/China-market supplement, Python for math, browser only for dynamic pages. Asta/Semantic Scholar is explicitly **not** part of the finance route.

## Bank/financial-sector valuation

For banks (HSBC etc.), DCF is the wrong primary frame. Use RoTE/ROE, CET1, payout/yield, NIM/NII, credit cost, P/B or P/E, buyback capacity. The HSBC test workflow and pitfalls are recorded in `references/hsbc-hk-bank-research-test-20260429.md` — consult before changing bank-related logic.
