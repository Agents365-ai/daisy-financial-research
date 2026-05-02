# HSBC HK bank research test — 2026-04-29

## Why this reference exists

Session-tested details for the `dexter-financial-research` skill when researching a Hong Kong-listed bank using Tushare + MCP search.

## User preference captured

For finance research/search workflows, do not include Asta/Semantic Scholar as a default evidence route. Use:

1. Tushare for structured market/financial data where available.
2. Brave MCP as the primary web/current search tool.
3. Bailian WebSearch MCP as a Chinese-language / China-market supplemental search tool.
4. Python for calculations and tabulation.
5. Browser only when pages need interaction, dynamic rendering, login/paywall handling, or visual inspection.

## Known-good HK Tushare probes

Environment used: `python` (whichever interpreter has `tushare`, `pandas`, `requests` installed; see SKILL.md "Python interpreter convention"), Tushare 1.4.29, token from `TUSHARE_TOKEN`.

```python
import os, tushare as ts
pro = ts.pro_api(os.getenv('TUSHARE_TOKEN') or ts.get_token())

pro.hk_basic(ts_code='00005.HK', fields='ts_code,name,list_status,list_date,delist_date')
pro.hk_daily(ts_code='00005.HK', start_date='20250101', end_date='20260429')
```

Observed for HSBC `00005.HK`:

- `hk_basic` returned one listed row: `00005.HK 汇丰控股`, list date `19800102`.
- `hk_daily` returned daily HK price rows; latest available in the test was 2026-04-28 close 140.6 HKD.
- `hk_daily_basic` failed with: `请指定正确的接口名`. Do not rely on it without re-testing.

## Useful calculation pattern

Sort `hk_daily` ascending by `trade_date`, then compute:

- latest close/date
- YTD return from first trading day >= Jan 1
- 1M/3M/6M/1Y returns from approximate trading-day offsets
- 52-week high/low from last 252 rows
- dividend yield from official USD dividend translated at approximate HKD/USD peg when no structured dividend API is available
- target-price upside from sourced analyst targets

## HSBC test facts captured

From HSBC official 2025 results pages and search cross-checks:

- 2025 revenue: 68.3B USD.
- Reported profit before tax: 29.9B USD.
- Profit before tax excluding notable items: 36.6B USD.
- RoTE: 13.3%; RoTE excluding notable items: 17.2%.
- CET1: 14.9%.
- 2025 total dividend: 0.75 USD/share.
- 2025 buybacks completed: 6B USD.
- 2026 guidance: banking NII at least 45B USD.
- 2026–2028 target: RoTE excluding notable items >=17%; target payout ratio basis 50%.
- HSBC stated further buybacks would wait until CET1 returns to/above target range after Hang Seng Bank privatization capital impact.

Tushare-derived in the session:

- Latest `hk_daily` close: 140.6 HKD on 2026-04-28.
- YTD return: about +13.1%.
- 1Y return: about +76.7%.
- 52-week high/low: about 148.0 / 80.05 HKD.
- Estimated trailing dividend yield using 0.75 USD * 7.8 / 140.6: about 4.16%.

Bailian/Brave search snippets surfaced market targets:

- Futu aggregated average target around 164.63 HKD, lowest around 143.08, highest around 180.00.
- Goldman target around 160 HKD, with caution that buyback may be more likely after Q2 than immediately after Q1.

## Bank valuation pitfall

Do not default to DCF for banks. For banks, the first-pass framework should be:

- profitability: RoTE/ROE, NIM/NII trend, fee/wealth income
- capital: CET1, target capital range, buyback capacity
- distribution: dividend per share, payout ratio, dividend yield, buyback restart timing
- risk: credit costs/ECL, loan growth, commercial real estate/China/geopolitical exposure
- valuation: P/B and P/E where sourced, plus target-price sanity checks
- catalyst: next earnings release, management guidance, buyback resumption, rate path

## Scratchpad example

The test created:

`./financial-research/scratchpad/20260429-162217_b2d5c91bf629.jsonl`

Use only as an example pattern; do not treat that file as canonical data in future sessions.
