# Stock Screening Presets

Use these as explicit presets, not as investment advice. Output is a research watchlist.

## Preset: a_dividend_quality

Purpose: A-share stable dividend / income watchlist.

Universe:
- A-share listed stocks.
- Exclude ST/*ST, Beijing board if liquidity is too low, newly listed < 2 years.

Hard filters:
- total market cap >= 10000 million CNY.
- PE > 0 and PE <= 30.
- PB > 0 and PB <= 5.
- dividend yield TTM (`dv_ttm`) >= 2% when available.
- daily turnover rate > 0, non-suspended.

Default score:
- shareholder_return 35%: higher `dv_ttm`.
- valuation 25%: lower PE and PB.
- size/liquidity 15%: larger market cap and turnover sanity.
- momentum 15%: optional 3M/6M return when price history is fetched.
- risk penalty 10%: ST/new listing/extreme valuation/missing critical data.

## Preset: a_value_momentum

Purpose: A-share low valuation + recent strength watchlist.

Hard filters:
- market cap >= 10000 million CNY.
- PE 3–35.
- PB 0.3–4.
- exclude ST/*ST.

Default score:
- valuation 40%: lower PE/PB.
- momentum 30%: positive 3M/6M return.
- liquidity/size 20%.
- dividend 10%.

## Preset: hk_dividend_quality

Purpose: HK listed high dividend / quality candidates.

Universe:
- HK main board, H-share, Hang Seng indexes, 港股通 only when explicitly requested.

Data route:
- Tushare `hk_basic`, `hk_daily`, `hk_hold` when 港股通 requested.
- Use Brave/Bailian for dividend, buyback, earnings and red-flag verification.

Hard filters:
- adequate liquidity.
- avoid obvious shell/liquidity-trap names.
- require independent verification for dividend sustainability.

## Preset: bank_insurance

Purpose: bank/insurance watchlist.

Avoid generic DCF as primary method.

Bank factors:
- ROE/RoTE.
- CET1/capital adequacy.
- NIM/NII trend.
- credit cost/NPL.
- PB/PE.
- dividend payout/yield.
- buyback capacity.

Insurance factors:
- solvency ratio.
- VNB/new business value.
- embedded value.
- investment yield.
- combined ratio for P&C.
- dividend and buyback.

## Preset: quality_growth

Purpose: quality compounder watchlist.

Factors:
- ROE/ROIC.
- revenue and net profit growth.
- margin stability.
- cash-flow conversion.
- moderate leverage.
- valuation sanity.

## Standard watchlist fields

Recommended columns:

- rank
- ts_code / ticker
- name
- market
- industry
- trade_date
- close
- total_mv
- pe
- pb
- dividend_yield
- score_total
- score_quality
- score_valuation
- score_growth
- score_shareholder_return
- score_momentum
- reason_selected
- red_flags
- next_check
- source_snapshot
