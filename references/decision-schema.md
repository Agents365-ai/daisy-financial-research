# Decision Schema — Rating Vocabulary and Markdown Render Contract

A docs-only port of the Pydantic schemas in `TradingAgents/tradingagents/agents/schemas.py`. Daisy does not run the runtime validation (no Pydantic dependency); instead, the prompts in `references/debate-prompts.md` and `references/risk-debate-prompts.md` ask the LLM to emit exactly the markdown shape documented here, and `dexter_memory_log.py record --rating` enforces the rating enum at the CLI boundary.

The shape here is **load-bearing**: report writers (`scripts/financial_report.py`), the memory log file format, and any external code that greps the saved reports all read this exact set of section headers.

---

## Rating vocabulary

### 5-tier directional rating (Research Manager / Portfolio Manager)

The same vocabulary `dexter_memory_log.py` enforces via `--rating`.

| Rating | Meaning |
|---|---|
| **Buy** | Strong conviction in the bull thesis; recommend taking or growing the position |
| **Overweight** | Constructive view; recommend gradually increasing exposure |
| **Hold** | Balanced view; recommend maintaining the current position |
| **Underweight** | Cautious view; recommend trimming exposure |
| **Sell** | Strong conviction in the bear thesis; recommend exiting or avoiding the position |

**Picking rule:** reserve `Hold` for cases where the evidence on both sides is genuinely balanced. Otherwise commit to the side with the stronger argument. Do not hedge for politeness.

### 3-tier transaction action (Trader)

For workflows that produce an actionable transaction proposal on top of the directional view:

| Action | Meaning |
|---|---|
| **Buy** | Execute a buy this round |
| **Hold** | Do nothing this round |
| **Sell** | Execute a sell / exit this round |

The 5-tier rating expresses *position vs target*; the 3-tier action expresses *what the desk does this round*. They can diverge — e.g. an `Overweight` rating with a `Hold` action means "favorable view but the entry has already been made and we're not adding more today."

---

## Markdown render contracts

### A. Research Plan (output of Bull/Bear synthesis)

Used by `references/debate-prompts.md` Prompt 3.

```markdown
**Rating**: <one of the 5-tier values>

**Thesis (2-4 sentences)**: <single strongest reason for the rating, anchored on the most decisive evidence>

**What would change the view**: <2-3 observable conditions that would move the rating up or down a notch (specific metric thresholds, dates, catalysts)>

**Risks acknowledged**: <strongest 1-2 bear points the rating cannot fully neutralize>

**Holding period and re-check trigger**: <target horizon (e.g. "next 2 quarters / next earnings"), and the precise event or metric that should trigger the next look>
```

### B. Trader Proposal (optional — for trade-execution workflows)

```markdown
**Action**: <Buy / Hold / Sell>

**Reasoning**: <2-4 sentences anchored in the analysts' reports and the research plan>

**Entry Price**: <optional numeric target in quote currency>

**Stop Loss**: <optional numeric level in quote currency>

**Position Sizing**: <optional sizing guidance, e.g. "5% of portfolio">

FINAL TRANSACTION PROPOSAL: **BUY** | **HOLD** | **SELL**
```

The trailing `FINAL TRANSACTION PROPOSAL: **...**` line is the canonical sentinel. External tooling that scans for end-of-debate markers reads it; preserve it verbatim.

### C. Portfolio Decision (output of Risk Debate synthesis)

Used by `references/risk-debate-prompts.md` Prompt 4.

```markdown
**Rating**: <one of the 5-tier values>

**Executive Summary**: <2-4 sentences covering entry strategy, position sizing, key risk levels, time horizon>

**Investment Thesis**: <detailed reasoning anchored in specific evidence from the risk debate; incorporate prior lessons if present in the prompt context>

**Price Target**: <optional numeric target in quote currency, or omit the line entirely>

**Time Horizon**: <optional, e.g. "3-6 months", or omit the line entirely>
```

---

## JSON Schema (for agents that want to self-validate)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "PortfolioDecision",
  "type": "object",
  "required": ["rating", "executive_summary", "investment_thesis"],
  "properties": {
    "rating": {
      "type": "string",
      "enum": ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
    },
    "executive_summary": {
      "type": "string",
      "description": "2-4 sentences covering entry strategy, sizing, risk levels, time horizon"
    },
    "investment_thesis": {
      "type": "string",
      "description": "Detailed reasoning anchored in specific evidence"
    },
    "price_target": {
      "type": ["number", "null"],
      "description": "Optional target price in the instrument's quote currency"
    },
    "time_horizon": {
      "type": ["string", "null"],
      "description": "Optional recommended holding period"
    }
  }
}
```

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "ResearchPlan",
  "type": "object",
  "required": ["rating", "thesis", "what_would_change_the_view", "risks_acknowledged", "holding_period_and_recheck"],
  "properties": {
    "rating": {
      "type": "string",
      "enum": ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
    },
    "thesis": {"type": "string", "description": "2-4 sentences"},
    "what_would_change_the_view": {"type": "string"},
    "risks_acknowledged": {"type": "string"},
    "holding_period_and_recheck": {"type": "string"}
  }
}
```

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "TraderProposal",
  "type": "object",
  "required": ["action", "reasoning"],
  "properties": {
    "action": {"type": "string", "enum": ["Buy", "Hold", "Sell"]},
    "reasoning": {"type": "string", "description": "2-4 sentences"},
    "entry_price": {"type": ["number", "null"]},
    "stop_loss": {"type": ["number", "null"]},
    "position_sizing": {"type": ["string", "null"]}
  }
}
```

---

## Why no runtime validation

A skill is consumed by an external LLM-driven agent harness. The agent's own structured-output mode (json_schema for OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic) does the runtime validation against the JSON Schemas above. Re-implementing validation inside daisy would either duplicate effort, force a Pydantic dependency on a deliberately-light skill, or both. The CLI does enforce the 5-tier `--rating` enum at `dexter_memory_log.py record` time, which is the single integration point where invalid output would corrupt durable state.

## Source

`TradingAgents/tradingagents/agents/schemas.py` (`PortfolioRating`, `TraderAction`, `ResearchPlan`, `TraderProposal`, `PortfolioDecision`, plus the `render_*` helpers). The schema field descriptions are condensed; the markdown shape is preserved verbatim.
