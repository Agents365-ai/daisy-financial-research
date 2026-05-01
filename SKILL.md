---
name: dexter-financial-research
description: Dexter-inspired autonomous financial research workflow for Hermes. Use when the user asks for deep stock/company/sector research, DCF or valuation, financial comparison, market-catalyst analysis, or wants an agentic finance analyst that plans, gathers data, validates, and produces a sourced answer.
version: 1.0.0
author: Hermes Agent, adapted from virattt/dexter key design patterns
license: MIT
metadata:
  hermes:
    tags: [finance, research, stocks, valuation, dcf, tushare, agent-workflow]
    related_skills: [tushare]
---

# Dexter Financial Research

This skill ports the useful parts of `virattt/dexter` into a Hermes skill rather than a separate TypeScript CLI agent.

Dexter’s key ideas:

1. Treat financial research as an iterative agent loop, not a one-shot answer.
2. First create a compact research plan, then execute data-gathering steps.
3. Use a scratchpad as the single source of truth for tool calls, results, assumptions, and partial conclusions.
4. Prefer high-level meta-queries to finance data tools, but fall back to specific interfaces when needed.
5. Use soft loop limits and repeat-query detection to avoid runaway tool use.
6. Validate numerical answers before finalizing.
7. For valuation, use an explicit DCF workflow with sensitivity analysis and sanity checks.
8. Output concise, sourced analysis with caveats; never present investment advice as certainty.

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

### 0. State scope and assumptions briefly

Infer obvious defaults instead of asking:

- “recent” = last 60 calendar days / ~40 trading days
- “financial trend” = last 8 quarters or last 5 annual periods when available
- “valuation” = DCF + multiple sanity check
- “A股” = Tushare first; US stocks = web/search or available market APIs first

Ask only if ambiguity changes the analysis materially.

### 1. Create a research scratchpad

For any non-trivial finance task, keep a local scratchpad file under:

`~/.hermes/reports/dexter-scratchpad/`

Use the helper script in this skill when useful:

```bash
python ~/.hermes/skills/research/dexter-financial-research/scripts/dexter_scratchpad.py init "original query"
python ~/.hermes/skills/research/dexter-financial-research/scripts/dexter_scratchpad.py add /path/to/file.jsonl tool_result tool_name='tushare.daily' args='...' result='...'
```

If not using the helper, still preserve internally:

- original query
- plan
- each data source/tool/interface used
- parameters/date ranges
- raw key data and transformed metrics
- errors/empty results/permission issues
- assumptions and interim conclusions

### 2. Plan before tools

Write a 3–7 item plan. Keep it tactical:

- identify company/ticker/universe
- collect price/market data
- collect financials/ratios/estimates/filings/news as relevant
- compute metrics or valuation
- validate numbers and sources
- synthesize concise answer

### 3. Tool/data routing policy

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
python ~/.hermes/skills/research/dexter-financial-research/scripts/hk_connect_universe.py --date YYYYMMDD --top 20
```

- The helper searches backward when the requested date has no data and writes a CSV under `~/.hermes/reports/financial-research/YYYYMMDD_hk-connect-universe.csv`.
- For 港股通 flow/capital attention, optionally use `pro.ggt_top10(...)`, `pro.ggt_daily(...)`, and `pro.moneyflow_hsgt(...)`.
- Do not assume every advertised HK interface works in the installed Tushare version; in this environment `pro.hk_daily_basic(...)` returned `请指定正确的接口名`, so treat it as unavailable unless re-tested.
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

### 4. Soft loop limits

Avoid repetitive tool calls:

- Suggested max per tool/interface: 3 attempts per query.
- If a query/interface fails twice, change strategy: different endpoint, broader/narrower date range, web fallback, or explain limitation.
- Do not keep calling the same endpoint with near-identical parameters.
- If data is incomplete, proceed with caveated analysis rather than fabricating.

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

`~/.hermes/reports/financial-research/`

Preferred report stack:

1. Markdown source (`.md`) as the canonical editable record.
2. HTML report (`.html`) as the primary polished output.
3. PDF (`.pdf`) only when the user asks for a printable/shareable file, or when HTML-to-PDF tooling is available and stable.

Default behavior:

- For quick answers: reply in chat only, optionally with scratchpad path.
- For medium/deep research: create both `.md` and `.html`.
- For formal deliverables: create `.md`, `.html`, and `.pdf` if possible.

Use the bundled report generator:

```bash
# medium/deep research: Markdown + HTML
python ~/.hermes/skills/research/dexter-financial-research/scripts/financial_report.py report.md --title "Company Research Report" --slug company-research

# formal deliverable: Markdown + HTML + PDF
python ~/.hermes/skills/research/dexter-financial-research/scripts/financial_report.py report.md --title "Company Research Report" --slug company-research --pdf
```

The generator copies the Markdown source and renders the report to:

`~/.hermes/reports/financial-research/YYYYMMDD-HHMMSS_slug.{md,html,pdf}`

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
4. Price and valuation snapshot.
5. Financial performance and key drivers.
6. News/catalyst review.
7. Bull/base/bear scenarios.
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
~/.hermes/venv/bin/python ~/.hermes/skills/research/dexter-financial-research/scripts/screen_a_share.py --preset a_dividend_quality --top 50 --report

# A-share value watchlist
~/.hermes/venv/bin/python ~/.hermes/skills/research/dexter-financial-research/scripts/screen_a_share.py --preset a_value --top 50 --report

# 港股通 watchlist only when explicitly requested
~/.hermes/venv/bin/python ~/.hermes/skills/research/dexter-financial-research/scripts/screen_hk_connect.py --top 50 --with-momentum

# Turn generated Markdown into the three-layer report stack
~/.hermes/venv/bin/python ~/.hermes/skills/research/dexter-financial-research/scripts/financial_report.py report.md --title "Watchlist Report" --slug watchlist --pdf
```

Watchlist outputs go under:

`~/.hermes/reports/financial-research/watchlists/`

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
