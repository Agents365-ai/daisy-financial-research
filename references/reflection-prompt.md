# Reflection Prompt for Memory Log Resolve

A standardized prompt for the lesson the agent passes to `dexter_memory_log.py resolve --reflection`. Adapted verbatim from `TradingAgents/tradingagents/graph/reflection.py::Reflector._get_log_reflection_prompt`.

## When to use

- Whenever an agent calls `dexter_memory_log.py resolve` and needs to write the `--reflection` lesson.
- Before persisting a resolution: have the LLM write the lesson using this prompt, then pass the resulting text as `--reflection`.

## Why a fixed prompt

Without one, lesson lengths drift across runs (some 1-line, some 5-paragraph), formatting drifts (some bulleted, some prose), and content drifts (some pure narrative, some pure numbers). Stable shape matters because:

- Lessons are re-injected into future agent prompts via `dexter_memory_log.py context`. A bloated lesson burns context tokens on every subsequent call for that ticker.
- The cross-ticker `context` block lists ~3 recent lessons; if any of them is a 200-word paragraph, the block becomes too noisy to read.
- The `stats` aggregation reads tag lines, not lessons — but a human auditing the log expects a consistent shape.

The TA prompt's "exactly 2-4 sentences of plain prose" constraint is doing real work; preserve it.

## The prompt

```text
You are a trading analyst reviewing your own past decision now that the outcome is known.

Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).

Cover in order:
1. Was the directional call correct? (cite the alpha figure)
2. Which part of the investment thesis held or failed?
3. One concrete lesson to apply to the next similar analysis.

Be specific and terse. Your output will be stored verbatim in a decision log and re-read by future analysts, so every word must earn its place.

---

Inputs:
Raw return: {raw_return:+.1%}
Alpha vs benchmark: {alpha_return:+.1%}
Holding days: {holding_days}d
Benchmark used: {benchmark}

Prior decision text (the thesis being reviewed):
{decision_text}

Write the reflection now.
```

## Workflow

```bash
# 1. Compute raw_return and alpha (manually, or via the auto-resolve helper when shipped)
RAW=4.8
ALPHA=1.2
DAYS=17

# 2. Have the LLM write the lesson using the prompt above. Capture as $REFL.

# 3. Persist
python <skill-dir>/scripts/dexter_memory_log.py resolve \
  --ticker 600519.SH --date 20260415 \
  --raw-return "$RAW" --alpha-return "$ALPHA" --holding-days "$DAYS" \
  --reflection "$REFL"
```

## Anti-pattern

Don't paste the entire research report or the synthesis output as the reflection — that's what `--decision` is for at `record` time. The reflection is *only* the post-hoc lesson, written *after* the outcome is known. If the reflection ends up longer than the original decision, it's wrong.
