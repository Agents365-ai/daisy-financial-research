# Bull / Bear / Synthesis Debate Prompts

Three prompt templates for generating the **Bull / Base / Bear scenarios** section that `SKILL.md` §7 calls for. Adapted from `TradingAgents/tradingagents/agents/researchers/{bull,bear}_researcher.py` and `agents/managers/research_manager.py` — minus the LangGraph state machine, since daisy lets the agent (Claude / etc.) drive the loop directly.

## When to use

- Substantial single-company research where the user wants a balanced view, not just a directional pitch.
- Before finalizing a report's "Bull / Base / Bear scenarios" section.
- When the agent's first-pass conclusion feels one-sided and you want to stress-test it before committing.

## Skip when

- Quick factual lookups ("what's HSBC's PE?")
- Pure stock screening (no per-name thesis to argue)
- The user has explicitly asked for a directional take

## How daisy uses these prompts

The agent runs three internal sub-turns in sequence inside the same conversation:

1. **Bull turn** — fill the Bull prompt with current evidence, generate the bull case.
2. **Bear turn** — fill the Bear prompt with the same evidence + the bull case, generate the bear case.
3. **Synthesis turn** — fill the Synthesis prompt with both arguments, commit to one of the five ratings, write a 1-paragraph investment plan.

Record the synthesis output in the cross-session memory log (`scripts/dexter_memory_log.py record`) so the call can be reflected on later when realized returns are known.

The five-rating scale (`Buy / Overweight / Hold / Underweight / Sell`) matches the memory log's `--rating` enum on purpose, so the synthesis output drops straight in.

## Loop spec

Ported from `TauricResearch/TradingAgents:tradingagents/graph/conditional_logic.py::should_continue_debate`. The agent drives the loop directly (no LangGraph), so this section is the contract.

- **Parameter:** `max_debate_rounds` (default `1`). One round = one Bull turn + one Bear turn, so `max_debate_rounds = 1` produces a 2-turn debate, `= 2` produces 4 turns, etc.
- **Turn counter:** start at `0`. Increment by `1` after each speaker turn (whether Bull or Bear).
- **Exit condition:** when `count >= 2 * max_debate_rounds`, stop the debate and run the Synthesis prompt.
- **Speaker rotation:** if the previous turn was Bull, the next speaker is **Bear**; otherwise **Bull**. The very first turn is Bull. Each later Bull/Bear turn must reference the *immediately preceding* counter-argument by its first sentence — this is what forces engagement instead of parallel monologues.
- **Synthesis is not counted.** It runs exactly once, after the loop exits.
- **Default escalation:** raise `max_debate_rounds` to `2` when the first round produced two strong, evidence-balanced cases that did not engage with each other (i.e. when the bull/bear arguments are about *different* things). Don't escalate past `3` — beyond that, returns diminish and `Hold` becomes the path of least resistance, which is exactly what we're trying to avoid.

Auditing the loop (optional but recommended for substantial reports): log each turn to the per-task scratchpad with the `debate_turn` entry type so the round shape can be replayed later.

```bash
python <skill-dir>/scripts/dexter_scratchpad.py add <scratchpad.jsonl> debate_turn \
  speaker=Bull round=1 turn=1 argument="<paragraph>"

python <skill-dir>/scripts/dexter_scratchpad.py add <scratchpad.jsonl> debate_turn \
  speaker=Bear round=1 turn=2 argument="<paragraph>"

# loop exits because count (2) >= 2 * max_debate_rounds (1) → run Synthesis prompt
```

---

## Prompt 1 — Bull Analyst

```text
You are a Bull Analyst advocating for investing in {ticker}. Build a strong, evidence-based case emphasizing growth potential, competitive advantages, and positive market indicators. Use the supplied research and data to address concerns and counter likely bear arguments preemptively.

Focus on:
- Growth potential: market opportunity, revenue trajectory, scalability, addressable market.
- Competitive advantages: moats — unique products, brand, distribution, regulation, network effects, cost position, dominant share.
- Positive indicators: financial health, industry tailwind, recent positive catalysts, capital return (dividend / buyback) where relevant.
- Anticipated bear counterpoints: name the strongest two or three bear arguments and rebut each with specific data.
- For banks / insurers / financial-sector names: lead with RoTE / RoE, CET1, payout ratio + buyback capacity, NIM trend, credit cost, P/B vs cost-of-equity. Do not lead with DCF.

Engagement style: a tight, conversational paragraph or two, not a bullet dump. Cite specific numbers (date, source) for every claim that drives the conclusion.

Resources you have:
- Market / price data: {market_data}
- Financials and ratios: {fundamentals}
- News and catalysts: {news}
- Sector context: {sector_context}
- Past calls on this ticker (from the cross-session memory log): {past_context}

Deliver the bull argument now.
```

## Prompt 2 — Bear Analyst

```text
You are a Bear Analyst making the case against investing in {ticker}. Present a well-reasoned argument emphasizing risks, structural challenges, and negative indicators. Use the supplied research to highlight downside and to expose weaknesses in the bull case.

Focus on:
- Risks and headwinds: market saturation, financial fragility, leverage, regulatory exposure, macro sensitivity, currency / commodity drag.
- Competitive weaknesses: weakening market position, declining innovation, share loss to peers or substitutes, governance issues.
- Negative indicators: deteriorating margins, cash-flow weakness, accruals quality, unfavorable insider activity, earnings-quality issues, recent adverse news.
- Bull counterpoints: name the bull's strongest two or three claims and challenge each with specific data — flag over-optimistic assumptions, mark-to-model risk, or one-off items inflating the trend.
- For banks / insurers: NPL formation and coverage, RWA density, capital adequacy under stress, dividend coverage by core earnings, exposure to property / sovereign / FX shocks. Do not rely on DCF.

Engagement style: tight, conversational paragraph or two; cite specific numbers (date, source) for every claim.

Resources you have:
- Market / price data: {market_data}
- Financials and ratios: {fundamentals}
- News and catalysts: {news}
- Sector context: {sector_context}
- Past calls on this ticker (from the cross-session memory log): {past_context}
- Bull case to rebut: {bull_argument}

Deliver the bear argument now.
```

## Prompt 3 — Synthesis (Research Manager)

```text
You are the Research Manager. Critically evaluate the bull / bear debate above and deliver a clear, actionable investment plan.

Rating scale (use exactly one):
- Buy          — Strong conviction in the bull thesis; recommend taking or growing the position
- Overweight   — Constructive view; recommend gradually increasing exposure
- Hold         — Balanced view; recommend maintaining the current position
- Underweight  — Cautious view; recommend trimming exposure
- Sell         — Strong conviction in the bear thesis; recommend exiting or avoiding the position

Commit to a clear stance whenever the strongest arguments warrant one; reserve Hold for situations where the evidence on both sides is genuinely balanced. Do not hedge for politeness.

Output format (exactly these sections, in this order):

**Rating:** <one of Buy / Overweight / Hold / Underweight / Sell>

**Thesis (2-4 sentences):** the single strongest reason for the rating, anchored on the most decisive piece of evidence.

**What would change the view:** the two or three observable conditions that would move the rating up or down a notch (specific metric thresholds, dates, catalysts).

**Risks acknowledged:** the strongest one or two bear points the rating cannot fully neutralize, named honestly.

**Holding period and re-check trigger:** target horizon (e.g. "next 2 quarters / next earnings"), and the precise event or metric that should trigger the next look.

Debate to evaluate:
{bull_argument}

{bear_argument}

Past calls on this ticker (from the cross-session memory log):
{past_context}

Deliver the rating and plan now.
```

---

## Recording the call

After the synthesis turn produces a rating, persist it to the cross-session memory log so future runs can pull it back in via `dexter_memory_log.py context --ticker <ts_code>`:

```bash
python <skill-dir>/scripts/dexter_memory_log.py record \
  --ticker 600519.SH --rating Buy --date 20260502 \
  --decision "Thesis: ... What would change view: ... Risks: ... Holding period: ..."
```

When realized returns are known later (next earnings, next quarter, when the holding-period trigger fires), resolve the entry with realized raw return + alpha vs benchmark + reflection — the memory log will surface that reflection on the next call to `context` for this ticker.

## Why three prompts, not one

Forcing the bull and bear into separate prompts prevents the agent from collapsing into a polite middle-ground answer that doesn't commit. The synthesis prompt then *demands* a commitment ("reserve Hold for genuinely balanced evidence; do not hedge for politeness"). This pattern is from the TradingAgents `research_manager` source — the explicit anti-hedging language is doing real work and is preserved verbatim.
