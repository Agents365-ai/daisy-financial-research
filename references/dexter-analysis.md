# Analysis of virattt/dexter key points

Source repo inspected: https://github.com/virattt/dexter.git
Commit inspected: 0e5d805 (Update earnings tool)

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
