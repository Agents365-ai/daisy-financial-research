# Analysis of virattt/dexter key points

Source repo inspected: https://github.com/virattt/dexter.git
Commit inspected: `beb5f36` (Add LangSearch as web search provider; HEAD on 2026-05-13)
Prior pin was `0e5d805` (Update earnings tool).

## Delta since the prior pin — what was imported

- **`segments.py`** — ports the idea of dexter's `getFinancialSegments` tool
  (commit `8819ad7`) to a free-tier path: A-share via AKShare `stock_zygc_em`,
  HK/US emit `no_data` with a pointer to filings. Different geography than
  dexter (which is US-only via the paid Financial Datasets API), but the
  segment-level analytical frame is now available to the agent.
- **Broad-market news routing** — dexter's commit `745687e` taught the
  `get_company_news` tool to take a `ticker?` (omit for macro). Equivalent
  guidance is now in `SKILL.md` §3 as a Brave / Bailian MCP routing rule for
  no-ticker macro queries; no script needed because the MCPs handle the
  query directly.

## Explicitly not imported (and why)

- **LangSearch web-search provider** (commit `beb5f36`) — the skill commits
  to Brave + Bailian MCPs (`SKILL.md` §3, "Search routing"). LangSearch is
  redundant. Documented in routing notes only.
- **Memory keyword-search fallback** (commit `a4c511a`) — daisy's memory log
  is a single Markdown file with no embeddings, so there's no degradation
  path to add.
- **Research-rules prompt fix** (commit `aadaef1`) — daisy has no
  `.dexter/RULES.md` concept; not applicable.
- **CLI/UI commits** (cron z.union, model selector, flicker, duration
  counter, disclaimer, approval flow) — runtime UX, not relevant to a
  Python skill.
- **DCF simplification — drop analyst-estimates step** (commit `4c355d4`) —
  the current SKILL.md DCF section does not lean on analyst EPS estimates
  as a hard input, so no edit was needed.

## What Dexter is

Dexter is a TypeScript/Bun CLI financial research agent using LangChain, Ink UI, Financial Datasets API, search tools, browser scraping, skills, memory, cron, WhatsApp gateway, and evaluation datasets.

## Main architectural ideas worth porting into Hermes

1. Agentic finance loop
   - Decompose a financial question into research steps.
   - Iterate with tool calls until sufficient evidence exists.
   - Stop at max iterations instead of running forever.

2. Scratchpad as single source of truth
   - Each run writes JSONL entries containing init, tool_result, thinking.
   - Useful for auditability, debugging, and continuing long analyses.

3. Meta-tools for finance routing
   - Dexter exposes high-level tools such as get_financials and get_market_data.
   - Internally, those tools call an LLM router to choose specific sub-tools.
   - For Hermes, this is best approximated by skill instructions + Tushare/web/Python routing, unless a full plugin is built.

4. Rich tool-use policy
   - Explicit “when to use / when not to use”.
   - Prefer one complete natural-language finance query to many fragmented calls when using a meta-tool.
   - Avoid breaking comparisons into unnecessary repeated calls.

5. Concurrent read-only tools
   - Dexter safely batches read-only tool calls in parallel.
   - Hermes already has parallel/delegation patterns, so the skill tells the agent to gather independent evidence efficiently.

6. Soft loop limits and retry warnings
   - Dexter tracks call counts and similar query repetition.
   - It warns but does not hard-block repeated calls.
   - The skill ports this as a max-three-attempt heuristic.

7. Context management and compaction
   - Dexter caps large tool results, persists overflow to files, and compacts long sessions while preserving key numbers.
   - Hermes already has persisted tool outputs and file tools; the skill emphasizes scratchpad and numeric preservation.

8. DCF skill
   - Dexter includes a DCF valuation skill with a clear checklist, growth/WACC assumptions, terminal value, sensitivity matrix, and sanity checks.
   - This is directly ported into the Hermes skill.

9. Evaluation mindset
   - Dexter has a finance QA eval dataset and LangSmith judge workflow.
   - Future Hermes plugin could add a finance-eval runner, but the skill focuses on operational workflow.

## Skill vs plugin decision

A Hermes skill is the right first implementation because:

- The core Dexter advantage is workflow/prompt/tool policy, not a unique UI.
- Hermes already has memory, tools, browser, terminal, cron, and skills.
- The user already has Tushare configured, which is better for Chinese-market data than Dexter’s US-focused Financial Datasets API.
- A plugin would only be necessary if we want native new tool functions like get_financials(query) or get_market_data(query) backed by Financial Datasets API.

## Future plugin option

If building a Hermes plugin later, implement:

- financial_research(query): high-level router
- tushare_query(interface, params): structured Tushare wrapper
- dcf_valuation(ticker, market): calculation wrapper
- finance_scratchpad(action, path, payload): JSONL run log
- finance_eval(dataset_path): batch evaluation runner

Keep the skill as the policy layer even if plugin tools are added.
