---
name: daisy-financial-research
description: Use when user asks for stock / company / sector deep-dive research, DCF or valuation, financial comparison, market-catalyst analysis, or stock screening across A-share, Hong Kong, or US markets. Plans, gathers data via Tushare and web search, validates numbers, and produces a sourced report.
license: MIT
homepage: https://github.com/Agents365-ai/daisy-financial-research
compatibility: Requires Python 3.9+ with `tushare`, `pandas`, `requests` for screening / Tushare scripts. TUSHARE_TOKEN env var required for any Tushare call. No external CLI tools needed for the core analysis workflow.
platforms: [macos, linux, windows]
metadata: {"openclaw":{"requires":{"bins":["python3"]},"emoji":"📈","os":["darwin","linux","win32"]},"hermes":{"tags":["finance","research","stocks","valuation","dcf","tushare","agent-workflow","screening"],"category":"research","related_skills":["tushare"]},"author":"https://space.bilibili.com/1107534197","version":"2.6.0"}
---

# Daisy Financial Research

Autonomous stock / company / sector research workflow — plan, gather data, validate numbers, produce a sourced report. Inspired by the `virattt/dexter` design patterns (iterative agent loop, scratchpad, soft loop limits, numerical validation), packaged as a multi-platform skill.

Dexter’s key ideas:

1. Treat financial research as an iterative agent loop, not a one-shot answer.
2. First create a compact research plan, then execute data-gathering steps.
3. Use a scratchpad as the single source of truth for tool calls, results, assumptions, and partial conclusions.
4. Prefer high-level meta-queries to finance data tools, but fall back to specific interfaces when needed.
5. Use soft loop limits and repeat-query detection to avoid runaway tool use.
6. Validate numerical answers before finalizing.
7. For valuation, use an explicit DCF workflow with sensitivity analysis and sanity checks.
8. Output concise, sourced analysis with caveats; never present investment advice as certainty.

## Python interpreter convention

Command examples in this skill use a bare `python`. Substitute it with whichever interpreter in the caller's environment has `tushare`, `pandas`, and `requests` installed — for example `python3`, `~/.hermes/venv/bin/python`, `~\.hermes\venv\Scripts\python.exe`, a conda env, `uv run python`, or a pyenv-managed version. The skill does not assume any specific install location and works on macOS, Linux, and Windows.

## Agent-native CLI conventions

All scripts under `scripts/` follow a uniform agent-native contract so an LLM agent can call them without parsing prose:

- **Output format auto-detection.** When stdout is not a TTY (e.g. captured by `subprocess.run`), scripts emit a single JSON envelope on stdout. When stdout is a TTY, scripts emit the legacy human table. Override with `--format json|table`.
- **Stable success envelope:** `{"ok": true, "data": {...}, "meta": {"schema_version", "request_id", "latency_ms"}}`.
- **Stable error envelope:** `{"ok": false, "error": {"code", "message", "retryable", "context"}, "meta": {...}}`. Error messages stay on stderr in table mode.
- **Schema introspection.** `python <this-skill-dir>/scripts/<name>.py --schema` returns parameter types, preset registries, upstream interfaces, and error codes as JSON. Agents should prefer `--schema` over parsing `--help`.
- **Dry-run preview.** `--dry-run` echoes the request shape (would_call, would_write, filters, search_window) without making upstream API calls or writing files. Available on all mutating scripts.
- **Documented exit codes:** `0` ok · `1` runtime · `2` auth · `3` validation · `4` no_data · `5` dependency.
- **Long-running progress.** `screen_hk_connect.py --with-momentum` and `financial_report.py` emit NDJSON progress events on stderr (one JSON per line) so agents can detect liveness during multi-second runs.
- **Idempotency.** Output files are date-stamped (`YYYYMMDD_*` or `YYYYMMDD-HHMMSS_*`); re-runs are deterministic and overwrite the same path.

Agents calling these scripts should:

1. Run `--schema` once per script to learn parameters/presets, instead of parsing `--help`.
2. Capture stdout as JSON (auto-detected when piped) and branch on `data.ok`.
3. Read `error.code` (not `error.message`) to decide retry vs. escalate. `retryable: true` + a `no_data` code typically means "loosen filters or extend `--lookback-days`".

## Trigger conditions

Use this skill for:

- “研究一下 / analyze / deep dive” on a stock, company, ETF, index, sector, or market theme
- “DCF / intrinsic value / fair value / undervalued / overvalued / price target”
- Financial statement trend analysis, valuation comparison, earnings/catalyst analysis
- “Compare A vs B” for companies/sectors
- “Why did this stock move?” or “what changed recently?”
- Chinese A-share/HK/ETF queries where Tushare can provide data
- Stock screening / watchlist construction across A-share or Hong Kong markets, including dividend, quality, valuation, growth, momentum, and risk filters

Do not use for:

- Direct buy/sell/order execution
- Personalized portfolio advice without explicit risk/timeline context
- Unverifiable rumors
- Questions that can be answered from stable definitions without external data

## Mandatory workflow

### Step 0. Update check (notify, don't pull) — first use per conversation

Throttle to one check per 24 hours per installation; never mutate the skill directory without explicit user consent.

1. If `<this-skill-dir>/.last_update` exists and is less than 24 hours old, skip this step entirely.

2. Otherwise, fetch the latest tag from upstream:

   ```bash
   git -C <this-skill-dir> ls-remote --tags origin 'v*' 2>/dev/null \
     | awk '{print $2}' | sed 's|refs/tags/||' \
     | sort -V | tail -1
   ```

3. Compare with this skill's `metadata.version` from the frontmatter. If the upstream tag is strictly newer (semver), tell the user one line and ask:

   > "A newer version of this skill is available: vX.Y.Z → vA.B.C. Want me to `git pull`?"

   If they say yes, run `git -C <this-skill-dir> pull --ff-only`. Refresh `.last_update` either way so the prompt doesn't repeat for 24 hours.

4. If upstream is the same or older, refresh `.last_update` silently and continue.

5. On any failure (offline, not a git checkout — e.g. ClawHub-installed copy, read-only path, no permission), swallow the error silently and continue with the user's task. Do not mention the failure.

### 0. State scope and assumptions briefly

Infer obvious defaults instead of asking:

- “recent” = last 60 calendar days / ~40 trading days
- “financial trend” = last 8 quarters or last 5 annual periods when available
- “valuation” = DCF + multiple sanity check
- “A股” = Tushare first; US stocks = web/search or available market APIs first

Ask only if ambiguity changes the analysis materially.

### 1. Create a research scratchpad

For any non-trivial finance task, keep a local scratchpad file under:

`./financial-research/scratchpad/`

Use the helper script in this skill when useful:

```bash
python <this-skill-dir>/scripts/dexter_scratchpad.py init "original query"
python <this-skill-dir>/scripts/dexter_scratchpad.py add /path/to/file.jsonl tool_result tool_name='tushare.daily' args='...' result='...'
```

If not using the helper, still preserve internally:

- original query
- plan
- each data source/tool/interface used
- parameters/date ranges
- raw key data and transformed metrics
- errors/empty results/permission issues
- assumptions and interim conclusions

### 1b. Pull cross-session decision memory (optional but recommended)

The scratchpad is per-task. For learning across sessions and tickers, use the decision-log helper to read past calls before the plan step and to record the new call after the final answer:

```bash
# At plan step: pull recent same-ticker analyses + cross-ticker lessons
python <this-skill-dir>/scripts/dexter_memory_log.py context --ticker 600519.SH

# After final answer: record a pending decision
python <this-skill-dir>/scripts/dexter_memory_log.py record \
  --ticker 600519.SH --rating Buy --date 20260502 \
  --decision "Thesis: PE22, ROE30, dividend stable, demand resilient. Plan: re-check at next earnings."

# Later, when realized returns are known: resolve the pending entry.
# Recommended path — let daisy fetch close prices and benchmark automatically:
python <this-skill-dir>/scripts/dexter_memory_log.py auto-resolve \
  --ticker 600519.SH --date 20260502 \
  --reflection "Held 17d, raw +4.8% vs CSI300 +3.6%, alpha +1.2%. Dividend+ROE thesis worked."

# Or if you've already computed the numbers yourself:
python <this-skill-dir>/scripts/dexter_memory_log.py resolve \
  --ticker 600519.SH --date 20260502 \
  --raw-return 4.8 --alpha-return 1.2 --holding-days 17 \
  --reflection "..."
```

`auto-resolve` is the recommended path. It fetches `close[decision_date]` and `close[as_of_date]` for the ticker, walks forward / backward to the nearest trading day, fetches the right benchmark by ticker suffix (CSI 300 for `*.SH/SZ/BJ`, HSI for `*.HK`, SPY for US tickers), computes raw + alpha + holding days, then runs the same atomic-rewrite resolve logic as the manual path. For HK names, when Tushare's HK index endpoints aren't available in the user's plan, the helper falls through to AKShare's `stock_hk_index_daily_sina` for HSI (requires `pip install akshare`).

Use `dexter_memory_log.py compute-returns` to inspect the numbers without persisting:

```bash
python <this-skill-dir>/scripts/dexter_memory_log.py compute-returns \
  --ticker 600519.SH --date 20260415
# → JSON envelope with raw_return_pct / alpha_return_pct / benchmark_return_pct / holding_days
```

To audit your own track record across many resolved entries, run `backtest`:

```bash
# Auto-derived window covering every resolved entry
python <this-skill-dir>/scripts/dexter_memory_log.py backtest

# Explicit window, Buy ratings only
python <this-skill-dir>/scripts/dexter_memory_log.py backtest \
  --from 20260101 --to 20260430 --rating Buy
```

Returns per-rating count / mean alpha / `alpha_hit_rate` / `alpha_t_stat` / `annualized_alpha_pct`, plus an overall block with the cumulative-alpha drawdown. The metric names make explicit that this is decision-level — daisy logs decisions, not a continuous portfolio NAV, so a textbook Sharpe ratio doesn't apply.

When writing the `--reflection` text, follow the standard 2–4-sentence shape in `references/reflection-prompt.md` so lessons stay short enough to be re-injected on future runs.

Storage: a single Markdown file at `./financial-research/memory/decision-log.md`. Entries are separated by the HTML comment `<!-- ENTRY_END -->`. Tag lines start as `[YYYY-MM-DD | ticker | rating | pending]` and become `[YYYY-MM-DD | ticker | rating | +X.X% | +Y.Y% | Nd]` on resolve. `record` is idempotent on (date, ticker) — re-running with the same key skips silently. Ratings are constrained to `Buy / Overweight / Hold / Underweight / Sell` (see `references/decision-schema.md` for the full rating vocabulary and report markdown contract). Use `dexter_memory_log.py stats` for a hit-rate / mean-alpha summary.

### 2. Plan before tools

Write a 3–7 item plan. Keep it tactical:

- identify company/ticker/universe
- collect price/market data
- collect financials/ratios/estimates/filings/news as relevant
- compute metrics or valuation
- validate numbers and sources
- synthesize concise answer

### 3. Tool/data routing policy

The canonical per-market routing reference (A-share / HK / US, primary + documented fallback chain for each data type) lives at `references/data-source-routing.md`. Read it before the plan step; the rest of this section is the agent-facing summary.

For Chinese market / Tushare-accessible data:

- Load/use the `tushare` skill if not already loaded.
- Use `TUSHARE_TOKEN` from environment.
- Prefer Tushare for: A-share daily prices, stock_basic, daily_basic, income, balancesheet, cashflow, fina_indicator, forecast/express, moneyflow, margin, concept/index/ETF/fund/macro data.
- Use date format `YYYYMMDD` and stock code format like `000001.SZ`, `600000.SH`.

For Hong Kong stocks:

- Use Tushare HK interfaces when available before falling back to web quote sites.
- `pro.hk_basic(ts_code='00005.HK', ...)` and `pro.hk_daily(ts_code='00005.HK', start_date='YYYYMMDD', end_date='YYYYMMDD')` are known-good for HK tickers such as HSBC `00005.HK`.
- For the user's Hong Kong Stock Connect universe (港股通) preference when explicitly requested, use `pro.hk_hold(trade_date='YYYYMMDD')` as a first-pass universe identifier. It returns Southbound Stock Connect holdings with fields such as `code,trade_date,ts_code,name,vol,ratio,exchange`.
- Use the bundled helper to export the latest 港股通 universe:

```bash
python <this-skill-dir>/scripts/hk_connect_universe.py --date YYYYMMDD --top 20
```

- The helper searches backward when the requested date has no data and writes a CSV under `./financial-research/universes/YYYYMMDD_hk-connect-universe.csv`.
- For 港股通 flow/capital attention, optionally use `pro.ggt_top10(...)`, `pro.ggt_daily(...)`, and `pro.moneyflow_hsgt(...)`.
- Do not assume every advertised HK interface works in the installed Tushare version; in this environment `pro.hk_daily_basic(...)` returned `请指定正确的接口名`, so treat it as unavailable unless re-tested. **Fallback:** when an HK valuation/fundamentals call fails on Tushare, use the bundled AKShare helper (no Tushare token, no auth):

```bash
# PE-TTM / PB / PS / PCF snapshot + Stock Connect eligibility
python <this-skill-dir>/scripts/akshare_hk_valuation.py valuation --ts-code 00005.HK

# Annual or quarterly fundamentals: ROE_YEARLY, EPS_TTM, BPS, ROA, leverage
python <this-skill-dir>/scripts/akshare_hk_valuation.py fundamentals --ts-code 00005.HK --period 年度 --limit 8

# Local-dict-only Chinese name lookup (no API call) — covers ~30 HK majors
python <this-skill-dir>/scripts/akshare_hk_valuation.py name --ts-code 00700.HK
```

Sources: AKShare `stock_hk_valuation_comparison_em` + `stock_hk_security_profile_em` for valuation; `stock_financial_hk_analysis_indicator_em` for fundamentals. Optional `pip install akshare`; the helper emits `dependency_missing` (exit=5) with a clear install hint if the package is absent.
- For banks, DCF is usually the wrong primary valuation frame. Prefer RoTE/ROE, CET1, dividend payout/yield, NIM/NII guidance, credit cost, P/B or P/E, buyback capacity, and analyst target sanity checks.
- Maintain the user's preferred finance-search stack: Tushare for structured market/financial data; Brave MCP as primary web search; Bailian WebSearch MCP as Chinese/China-market supplement; Python for calculations; browser only for dynamic/interactive pages. Do not include Asta/Semantic Scholar as a default route for finance evidence.
- Session detail: see `references/hsbc-hk-bank-research-test-20260429.md` for the HSBC test workflow and pitfalls.

For web/current context:

- Prefer Brave MCP search (`brave_web_search` / `brave_local_search` when available) for current news, filings, company pages, market context, source discovery, and broad English/global web coverage.
- Use Bailian WebSearch MCP (`bailian_web_search`) as an optional/secondary search channel, especially for Chinese-language queries, China-market news, general encyclopedia-style facts, weather/news/current info, or when Brave results are sparse.
- Cross-check important claims with at least two independent sources when the answer depends on recent news, market rumors, policy, regulation, or company events.
- Use browser only when interaction, dynamic pages, paywall/login behavior, or visual inspection is needed.
- Use terminal Python for calculations and tabulation.
- If a dedicated finance API/tool is unavailable, be explicit about source limits.

Routing heuristics adapted from Dexter:

- Price / market movement / news / insider activity → market data or web search.
- Income statement / balance sheet / cash flow / ratios / estimates → financials.
- SEC filing details → filings/web sources.
- Broad market or macro news → web search.
- Screening by financial criteria → Tushare screening script or Python filtering.
- DCF / fair value → follow the DCF checklist below.
- **Revenue breakdown by product / region / segment** → A-share has a structured source (`scripts/segments.py` → AKShare `stock_zygc_em`); for HK / US there is no free segment API, so read the annual report's "Segment Information" note via filings / Brave search.

When the user asks "why is the market down today" / "今天大盘为什么跌" / "what's moving the Hang Seng" — no specific ticker — go straight to broad web search (Brave MCP for English / global, Bailian MCP for Chinese-language sources) with a market-wide query like `美股下跌 原因 YYYY-MM-DD` or `S&P 500 selloff YYYY-MM-DD`. Do **not** pick one large-cap ticker and search its news as a proxy; the intent is macro / sector-rotation / rates / geopolitical catalysts, not a company event.

### 4. Soft loop limits

Avoid repetitive tool calls:

- Suggested max per tool/interface: 3 attempts per query.
- If a query/interface fails twice, change strategy: different endpoint, broader/narrower date range, web fallback, or explain limitation.
- Do not keep calling the same endpoint with near-identical parameters.
- If data is incomplete, proceed with caveated analysis rather than fabricating.

When the scratchpad helper is active, you can ask it to flag both failure modes before a tool call:

```bash
python <this-skill-dir>/scripts/dexter_scratchpad.py can-call \
  <scratchpad.jsonl> tushare.daily 'ts_code=600519.SH start=20240101 end=20240630'
# → {allowed: true, warning: null|string, current_count: int, similar_to: [...]}
```

`allowed` is always `true` (this is a *soft* warning, not a block). React to a non-null `warning`: if `current_count >= max_calls`, change endpoint; if `similar_to` is non-empty, the tool is about to repeat a recent call — adjust the query or skip.

### 5. Numerical validation checklist

Before final answer, verify:

- Date ranges and units are stated.
- Currency/unit scale is consistent: yuan vs USD, CNY vs HKD, millions/billions.
- Growth rates use comparable periods.
- Per-share metrics use correct shares if computed manually.
- Market cap / EV / price are from a stated date.
- Any ranking/screening has universe and filters stated.
- If data is missing or permission-limited, say so.

### 6. Final answer format

Use this concise structure:

1. Scope/Data: tickers, period, sources/interfaces used.
2. Key Findings: 3–6 bullets with numbers.
3. Evidence Table: compact table when comparative/numerical.
4. Interpretation: what the data suggests, not overclaimed.
5. Risks / Missing Data / Caveats.
6. If exported: file path.

Always include: “Data analysis only, not investment advice.” when discussing securities.

### 7. Report export policy

For substantial research tasks, generate a durable report under:

`./financial-research/reports/`

Preferred report stack:

1. Markdown source (`.md`) as the canonical editable record.
2. HTML report (`.html`) as the primary polished output.
3. PDF (`.pdf`) only when the user asks for a printable/shareable file, or when HTML-to-PDF tooling is available and stable.

Default behavior:

- For quick answers: reply in chat only, optionally with scratchpad path.
- For medium/deep research: create both `.md` and `.html`.
- For formal deliverables: create `.md`, `.html`, and `.pdf` if possible.

**Hermes back-compat note.** Hermes installations that want to keep the legacy archive layout (`~/.hermes/reports/financial-research/`) can pass `--out-dir ~/.hermes/reports/financial-research` to any script — the script appends the matching subdir (`reports/`, `watchlists/`, `universes/`, `scratchpad/`) automatically.

Use the bundled report generator:

```bash
# medium/deep research: Markdown + HTML
python <this-skill-dir>/scripts/financial_report.py report.md --title "Company Research Report" --slug company-research

# formal deliverable: Markdown + HTML + PDF
python <this-skill-dir>/scripts/financial_report.py report.md --title "Company Research Report" --slug company-research --pdf
```

The generator copies the Markdown source and renders the report to:

`./financial-research/reports/YYYYMMDD-HHMMSS_slug.{md,html,pdf}`

Why HTML first:

- Easier to render tables, charts, color-coded risks, source links, and sensitivity matrices.
- More reliable than PDF generation in CLI environments.
- Can be opened directly in a browser and later printed/exported to PDF.

PDF guidance:

- Use PDF for sharing, archiving, printing, or sending to non-technical readers.
- Prefer generating PDF from the HTML report using browser print, Playwright/Chromium, or another available HTML-to-PDF tool.
- If PDF generation fails, keep the HTML and state the limitation rather than blocking the analysis.

Recommended report sections:

1. Executive summary / investment view.
2. Company and ticker scope.
3. Data sources and dates.
4. Price and valuation snapshot. When the report needs a technical-analysis layer, pick up to 8 complementary indicators from `references/technical-indicator-cheatsheet.md` and compute them via `scripts/technical_indicators.py --ts-code <code>` (auto-routes A-share/HK/US, applies a strict look-ahead-bias guard at `--as-of`). Skip TA entirely for banks / insurers — RoTE / CET1 / NIM are the right frame for those.
5. Financial performance and key drivers. For A-share names, pulling a revenue-by-segment / 主营构成 breakdown often surfaces concentration risk (one product line / one region) that the headline P&L hides:

   ```bash
   # All classifications (按产品 / 按地区 / 按行业), 4 most recent reports
   python <this-skill-dir>/scripts/segments.py --ts-code 600519.SH

   # Filter to one axis
   python <this-skill-dir>/scripts/segments.py --ts-code 000001.SZ --classification 按地区
   ```

   HK / US names: no free segment API — read the latest annual report's "Segment Information" note (10-K for US, annual report "Operating Segments" section for HK) via the filings tool or Brave search.
6. News/catalyst review. For A-share / 港股 names, pull China-market context (涨跌停 risk, 北向资金, 板块 rotation, 监管 backdrop) using the system prompt in `references/cn-market-analyst-prompts.md`.
7. Bull/base/bear scenarios. For balanced single-company research, run the three-prompt debate template in `references/debate-prompts.md` (Bull → Bear → Synthesis) instead of writing scenarios free-form. The synthesis output's 5-tier rating maps directly onto `dexter_memory_log.py record --rating`. For position-sizing follow-up after the directional rating is set, optionally run `references/risk-debate-prompts.md` (Aggressive → Conservative → Neutral → Portfolio Manager). All synthesis outputs use the markdown shape and rating vocabulary documented in `references/decision-schema.md`. Either loop can be driven mechanically by `scripts/debate_runner.py` (subcommands `init` / `next` / `synthesize`, `--type research|risk`) — the script enforces the rotation rules and exit conditions so the agent only has to write each speaker's argument; full usage in the "Programmatic loop driver" sections of the two prompt files.
8. Risks and what would change the view.
9. Evidence tables and calculations.
10. Disclaimer: data analysis only, not investment advice.

### 8. Stock screening and watchlist workflow

Use this when the user asks “怎么选股”, “筛一批股票”, “A股/港股有什么值得关注”, or wants a watchlist rather than a single-company report.

Reusable files:

- Presets/reference: `references/stock-screening-presets.md`
- Screening report template: `templates/screening_report.md`
- A-share screener: `scripts/screen_a_share.py`
- Hong Kong Stock Connect screener: `scripts/screen_hk_connect.py` (only when 港股通 is explicitly requested)
- Report generator: `scripts/financial_report.py`

Common commands:

```bash
# A-share dividend/quality watchlist + Markdown report source
python <this-skill-dir>/scripts/screen_a_share.py --preset a_dividend_quality --top 50 --report

# A-share value watchlist
python <this-skill-dir>/scripts/screen_a_share.py --preset a_value --top 50 --report

# 港股通 watchlist only when explicitly requested
python <this-skill-dir>/scripts/screen_hk_connect.py --top 50 --with-momentum

# Turn generated Markdown into the three-layer report stack
python <this-skill-dir>/scripts/financial_report.py report.md --title "Watchlist Report" --slug watchlist --pdf
```

Watchlist outputs go under:

`./financial-research/watchlists/`

Do not try to predict winners directly. Build a funnel:

1. Define universe
   - A-share: all listed stocks, index constituents, industry, market-cap band, dividend universe, or user-defined list.
   - Hong Kong: HK main board / H-share / Hang Seng indexes / Hong Kong Stock Connect (港股通, when explicitly requested) / user-defined HK tickers.
   - Exclude suspended, ST/*ST, newly listed names, illiquid names, or missing-data names unless the user explicitly wants them.

2. Choose screening style
   - Dividend/income: dividend yield, payout sustainability, ROE/ROTE, cash flow, debt, earnings stability.
   - Quality compounder: ROE/ROIC, gross/net margin, revenue/profit CAGR, low leverage, stable cash flow.
   - Value: low PE/PB/EV metrics, but require profitability and no obvious balance-sheet trap.
   - Growth: revenue/profit growth, margin trend, industry tailwind, valuation sanity.
   - Turnaround/event: earnings inflection, policy catalyst, restructuring, buyback, sector cycle.
   - Momentum: 1/3/6/12-month returns, relative strength, drawdown, volume confirmation.

3. Apply hard filters first
   - Liquidity: daily turnover or volume threshold.
   - Size: market cap threshold.
   - Financial health: positive earnings or operating cash flow, leverage not extreme.
   - Valuation: remove obvious extreme outliers unless justified.
   - Data completeness: remove rows with missing critical fields.

4. Score candidates
   - Build 4–6 factor scores rather than one magic metric.
   - Suggested default weights: quality 30%, valuation 25%, growth 20%, shareholder return 15%, momentum 10%.
   - For bank/insurance stocks, replace generic DCF/gross-margin metrics with ROE/ROTE, CET1/solvency, NIM/NII, credit cost, PB/PE, dividend and buyback capacity.

5. Produce a shortlist
   - Output 10–30 names for broad screens, then 3–8 names for deep-dive priority.
   - Include “why selected”, key metrics, red flags, and next verification step.
   - Never present the screen as a buy list; call it a research watchlist.

6. Deep-dive the finalists
   - For each finalist, run the single-company research workflow: data, news/catalysts, valuation, risks, scenario view.
   - Generate Markdown + HTML reports for substantial screens; add PDF for formal deliverables.

Suggested output tables:

- Universe and filters table.
- Top candidates table with ticker, name, industry, market cap, PE/PB, ROE/ROTE, dividend yield, growth, momentum, score, red flag.
- Priority deep-dive list: top 3–8 names and why they deserve follow-up.
- Exclusion notes: important names removed and why.

Screening caveats:

- Tushare/HK data availability varies by interface and user permissions; document missing fields.
- A cheap stock can be a value trap; require at least one quality or catalyst confirmation.
- A high dividend can be unsafe; check payout ratio, earnings stability, balance sheet and cash flow.
- Momentum screens need risk controls; do not confuse recent price strength with intrinsic value.

## DCF valuation workflow

Use when the user asks for intrinsic/fair value, DCF, price target, undervalued/overvalued.

Progress checklist:

- [ ] Gather financial data
- [ ] Calculate FCF base and historical FCF growth
- [ ] Estimate discount rate / WACC
- [ ] Project FCF for years 1–5 + terminal value
- [ ] Discount to present value and compute fair value per share
- [ ] Run sensitivity analysis
- [ ] Validate result
- [ ] Present assumptions and caveats

### Data to gather

- 5 years annual cash flow: operating cash flow, capex, free cash flow
- latest balance sheet: cash, investments, total debt, shares outstanding
- financial metrics: market cap, enterprise value, margins, ROE/ROIC, debt/equity, revenue growth
- analyst estimates if available
- latest price
- sector/industry for WACC sanity

### Assumptions

- FCF = operating cash flow - capex if not directly available
- Growth: use 5-year FCF CAGR if stable, haircut by 10–20%; cap sustained base growth at 15% unless justified
- For volatile FCF, triangulate with revenue growth, EPS estimates, and margin trend
- WACC: default 8–10% for mature companies; higher for cyclicals/small caps/high leverage; lower for stable defensives
- Terminal growth: default 2.5%; sensitivity 2.0%, 2.5%, 3.0%
- Years 1–5 growth decay: base growth × 1.00, 0.95, 0.90, 0.85, 0.80

### DCF validation

- Terminal value should usually be 50–80% of EV for mature companies; >90% is fragile.
- Calculated EV should be directionally plausible vs market EV; if >30–50% away, explain drivers.
- Cross-check fair value against FCF/share × 15–25 or sector multiple.
- Include a 3×3 sensitivity matrix: WACC base ±1% vs terminal growth 2.0/2.5/3.0%.

## A-share quick-start patterns with Tushare

Environment check:

```python
import os, tushare as ts
assert os.getenv('TUSHARE_TOKEN') or ts.get_token(), 'Missing TUSHARE_TOKEN'
pro = ts.pro_api(os.getenv('TUSHARE_TOKEN') or ts.get_token())
```

Common interfaces:

```python
# Stock list
pro.stock_basic(list_status='L', fields='ts_code,symbol,name,area,industry,list_date')

# Daily price
pro.daily(ts_code='000001.SZ', start_date='20240101', end_date='20241231')

# Daily valuation/market metrics
pro.daily_basic(ts_code='000001.SZ', start_date='20240101', end_date='20241231', fields='ts_code,trade_date,close,pe,pb,total_mv,circ_mv,turnover_rate,volume_ratio')

# Financial indicators
pro.fina_indicator(ts_code='000001.SZ', period='20231231')

# Income / balance sheet / cash flow
pro.income(ts_code='000001.SZ', period='20231231')
pro.balancesheet(ts_code='000001.SZ', period='20231231')
pro.cashflow(ts_code='000001.SZ', period='20231231')
```

## Quality bar

A good Dexter-style answer should be:

- grounded: every important number has a source/interface/date
- multi-step: shows it planned, gathered, computed, validated
- honest: says what is missing or permission-limited
- compact: useful to a finance reader, not a data dump
- reproducible: scratchpad/export path if the analysis used substantial data
