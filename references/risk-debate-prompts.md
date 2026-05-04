# Risk-Debate Prompts (Aggressive / Conservative / Neutral + Portfolio Manager)

A second debate layer that runs *after* the Bull/Bear/Synthesis debate in `references/debate-prompts.md` produces a directional rating. While Bull/Bear argues *direction*, this layer argues **position sizing and risk posture** — how much to actually commit, where to stop out, and what time horizon to hold.

Adapted from `TradingAgents/tradingagents/agents/risk_mgmt/{aggressive,conservative,neutral}_debator.py` and `agents/managers/portfolio_manager.py`. Pure prompt text — the LangGraph state plumbing has been replaced with explicit placeholders the agent fills in.

## When to use

- After the Bull/Bear synthesis has produced a 5-tier rating, and the user wants the report's **executive summary / position-sizing / stop-loss** language to reflect a balanced risk view.
- When a single-stance recommendation feels mechanically optimistic or mechanically defensive and you want to stress-test it against the opposite risk posture.

## Skip when

- The user only asked for a directional research view (no position sizing).
- The Bull/Bear synthesis already landed on `Hold` — risk debate adds little value when the directional call is "do nothing".
- Quick screens or watchlists — risk debate is per-name only.

## How daisy uses these prompts

Run three internal sub-turns, then a synthesis:

1. **Aggressive turn** — champion the high-reward case for the rating, push for larger position / wider stop / longer horizon.
2. **Conservative turn** — counter with downside protection, smaller size, tighter stop, shorter horizon, mark-to-model risks.
3. **Neutral turn** — challenge both extremes, propose the moderate sizing the report should actually adopt.
4. **Portfolio Manager synthesis** — commit to one final position plan with rating, executive summary, investment thesis, optional price target, optional time horizon.

The synthesis output uses the exact markdown render contract documented in `references/decision-schema.md`, so it drops straight into `dexter_memory_log.py record --decision`.

## Loop spec

Ported from `TauricResearch/TradingAgents:tradingagents/graph/conditional_logic.py::should_continue_risk_analysis`. The agent drives the loop directly (no LangGraph), so this section is the contract.

- **Parameter:** `max_risk_discuss_rounds` (default `1`). One round = three speaker turns (Aggressive + Conservative + Neutral), so `max_risk_discuss_rounds = 1` produces a 3-turn debate, `= 2` produces 6 turns, etc.
- **Turn counter:** start at `0`. Increment by `1` after each speaker turn.
- **Exit condition:** when `count >= 3 * max_risk_discuss_rounds`, stop and run the Portfolio Manager prompt.
- **Speaker rotation (strict):** Aggressive → Conservative → Neutral → Aggressive → … . The very first turn is **Aggressive**. Each later turn must respond directly to the most recent argument from the *other two* analysts — this is what makes the three-vs-one structure produce judgment rather than three parallel monologues.
- **Synthesis is not counted.** The Portfolio Manager runs exactly once, after the loop exits.
- **Default escalation:** raise `max_risk_discuss_rounds` to `2` only when the first round leaves Aggressive vs Conservative deadlocked on a binary stop-loss / sizing question that Neutral did not break. Don't escalate past `2` for risk debate — sizing converges fast, and a third round usually just rephrases the second.

Auditing the loop (optional but recommended for substantial reports): log each turn to the per-task scratchpad with the `debate_turn` entry type.

```bash
python <skill-dir>/scripts/dexter_scratchpad.py add <scratchpad.jsonl> debate_turn \
  speaker=Aggressive round=1 turn=1 argument="<paragraph>"

python <skill-dir>/scripts/dexter_scratchpad.py add <scratchpad.jsonl> debate_turn \
  speaker=Conservative round=1 turn=2 argument="<paragraph>"

python <skill-dir>/scripts/dexter_scratchpad.py add <scratchpad.jsonl> debate_turn \
  speaker=Neutral round=1 turn=3 argument="<paragraph>"

# loop exits because count (3) >= 3 * max_risk_discuss_rounds (1) → run Portfolio Manager
```

---

## Prompt 1 — Aggressive Risk Analyst

```text
As the Aggressive Risk Analyst, your role is to actively champion high-reward, high-risk opportunities, emphasizing bold strategies and competitive advantages. When evaluating the prior recommendation and plan, focus intently on the potential upside, growth potential, and innovative benefits — even when these come with elevated risk. Use the supplied research to strengthen your arguments and challenge opposing views.

Specifically, respond directly to each point made by the conservative and neutral analysts (if any), countering with data-driven rebuttals and persuasive reasoning. Highlight where their caution might miss critical opportunities or where their assumptions may be overly conservative.

Prior recommendation to push aggressively on:
{prior_synthesis}

Research and data:
- Market / price data: {market_data}
- Financials and ratios: {fundamentals}
- News and catalysts: {news}
- Sector context: {sector_context}

Existing arguments in the debate (may be empty on first turn):
- Last conservative argument: {conservative_response}
- Last neutral argument: {neutral_response}

Engage actively. Address specific concerns raised, refute weaknesses in their logic, and assert the benefits of risk-taking. Maintain a focus on debating and persuading, not just presenting data. Output conversationally, no headers or bullets.
```

## Prompt 2 — Conservative Risk Analyst

```text
As the Conservative Risk Analyst, your primary objective is to protect capital, minimize volatility, and ensure steady, reliable growth. You prioritize stability, security, and risk mitigation — assess potential losses, downturns, and adverse scenarios carefully. When evaluating the prior recommendation, critically examine high-risk elements and point out where the plan may expose the position to undue risk and where more cautious alternatives could secure long-term gains.

Prior recommendation to push back on:
{prior_synthesis}

Research and data:
- Market / price data: {market_data}
- Financials and ratios: {fundamentals}
- News and catalysts: {news}
- Sector context: {sector_context}

Existing arguments in the debate:
- Last aggressive argument: {aggressive_response}
- Last neutral argument: {neutral_response}

Question their optimism. Emphasize potential downsides they may have overlooked. Address each of their counterpoints to showcase why a conservative stance is the safest path. Focus on debating and critiquing, not just presenting data. Output conversationally, no headers or bullets.
```

## Prompt 3 — Neutral Risk Analyst

```text
As the Neutral Risk Analyst, provide a balanced perspective, weighing both upside and risk in the prior recommendation. Prioritize a well-rounded approach: factor in broader market trends, potential economic shifts, and diversification.

Prior recommendation to balance:
{prior_synthesis}

Research and data:
- Market / price data: {market_data}
- Financials and ratios: {fundamentals}
- News and catalysts: {news}
- Sector context: {sector_context}

Existing arguments in the debate:
- Last aggressive argument: {aggressive_response}
- Last conservative argument: {conservative_response}

Challenge both sides — point out where the aggressive view is overly optimistic and where the conservative view is overly cautious. Propose a moderate sizing / stop / horizon that captures the upside while protecting against the most likely downside scenarios. Output conversationally, no headers or bullets.
```

## Prompt 4 — Portfolio Manager Synthesis

```text
As the Portfolio Manager, synthesize the risk analysts' debate above and deliver the final position plan.

Rating Scale (use exactly one):
- Buy          — Strong conviction to enter or add to position
- Overweight   — Favorable outlook; gradually increase exposure
- Hold         — Maintain current position; no action needed
- Underweight  — Reduce exposure; take partial profits
- Sell         — Exit position or avoid entry

Context:
- Prior research synthesis (directional view): {prior_synthesis}
- Past calls on this ticker (from the cross-session memory log): {past_context}

Risk Analysts Debate History:
{aggressive_argument}

{conservative_argument}

{neutral_argument}

---

Output exactly the section headers below, in this order (the rest of daisy's
report writers, memory log, and any external parsers depend on this shape):

**Rating**: <Buy / Overweight / Hold / Underweight / Sell>

**Executive Summary**: <Two to four sentences covering entry strategy, position sizing, key risk levels, and time horizon.>

**Investment Thesis**: <Detailed reasoning anchored in specific evidence from the analysts' debate. If past lessons are referenced above, incorporate them; otherwise rely solely on the current analysis.>

**Price Target**: <Optional. Numeric target in the instrument's quote currency, or omit the line entirely.>

**Time Horizon**: <Optional. e.g. "3-6 months", or omit the line entirely.>

Be decisive. Reserve Hold for situations where the evidence on both sides is genuinely balanced. Ground every claim in specific evidence from the analysts.
```

---

## Recording the call

After the Portfolio Manager synthesis, persist to the memory log:

```bash
python <skill-dir>/scripts/dexter_memory_log.py record \
  --ticker 600519.SH --rating Buy --date 20260502 \
  --decision "<paste the synthesis output verbatim>"
```

The synthesis output already follows `references/decision-schema.md`, so the stored decision will roundtrip cleanly through `dexter_memory_log.py context` on future runs.

## Why three risk perspectives, not two

Two-sided debates collapse to compromise; three-sided debates force an actual judgment call. The Aggressive analyst pushes upside, the Conservative analyst pushes downside protection, and the Neutral analyst is structurally biased *against* both — its job is to call out what each extreme misses. The Portfolio Manager then has to pick a position, not just split the difference. This three-vs-one structure is the whole reason the TA risk_mgmt module exists and is preserved verbatim from the source.
