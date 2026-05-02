# Daisy Financial Research — Autonomous Stock / Company Research Skill

[中文](README.md) | [GitHub](https://github.com/Agents365-ai/daisy-financial-research)

## What it does

A multi-platform agent skill for finance research. Given a stock/company/sector topic, it plans the research, pulls structured data from Tushare, searches the web via Brave / Bailian MCP, runs Python for math and valuation, and produces a sourced, reproducible Markdown + HTML (+ optional PDF) report.

Design borrows from `virattt/dexter` — iterative agent loop (plan → gather → validate → answer) — but packaged as a cross-platform skill, no separate CLI.

**Key capabilities:**
- Plan-first workflow with JSONL scratchpad recording every tool call, params, result, assumption
- DCF valuation with sensitivity matrix and sanity checks
- Bank / financial-sector valuation override (RoTE / CET1 / NIM / P/B / payout) instead of forcing DCF on the wrong frame
- A-share + Hong Kong Stock Connect screening presets (dividend-quality, value, momentum, etc.)
- Three-layer report output (md → html → optional pdf), CSS already handles CN/EN font fallback
- Brave MCP + Bailian WebSearch MCP for web context

## Multi-Platform Support

| Platform | Status | Notes |
|---|---|---|
| **Claude Code** | ✅ Full | Native SKILL.md format |
| **Opencode** | ✅ Full | Reads `~/.claude/skills/` automatically |
| **OpenClaw / ClawHub** | ✅ Full | `metadata.openclaw` namespace, dependency gating |
| **Hermes Agent** | ✅ Full | `metadata.hermes` namespace |
| **OpenAI Codex** | ✅ Full | `agents/openai.yaml` sidecar |
| **SkillsMP** | ✅ Indexed | GitHub topics configured |

## Prerequisites

```bash
# Python 3.9+
pip install tushare pandas requests
# Optional: PDF output
brew install pandoc
brew install --cask mactex      # or basictex for a smaller install
```

Environment:
```bash
export TUSHARE_TOKEN=xxxxxxxx   # required for any Tushare call
```

## Installation

| Platform | Global | Project |
|---|---|---|
| Claude Code | `git clone https://github.com/Agents365-ai/daisy-financial-research.git ~/.claude/skills/daisy-financial-research` | `git clone ... .claude/skills/daisy-financial-research` |
| Opencode | `git clone ... ~/.config/opencode/skills/daisy-financial-research` | `git clone ... .opencode/skills/daisy-financial-research` |
| OpenClaw | `clawhub install daisy-financial-research` or `git clone ... ~/.openclaw/skills/daisy-financial-research` | `git clone ... skills/daisy-financial-research` |
| Hermes | `git clone ... ~/.hermes/skills/research/daisy-financial-research` | via `external_dirs` in `~/.hermes/config.yaml` |
| OpenAI Codex | `git clone ... ~/.agents/skills/daisy-financial-research` | `git clone ... .agents/skills/daisy-financial-research` |
| SkillsMP | `skills install daisy-financial-research` | — |

## Quick Start

```bash
# A-share dividend-quality watchlist + Markdown report draft
python <skill-dir>/scripts/screen_a_share.py --preset a_dividend_quality --top 50 --report

# Render the Markdown draft into the three-layer report
python <skill-dir>/scripts/financial_report.py ./financial-research/reports/<TIMESTAMP>_a-share-a_dividend_quality-screen.md \
    --title "A-share dividend watchlist" --slug a-div-quality --pdf
```

All output lands under `./financial-research/{reports,watchlists,scratchpad,universes}/` in your cwd by default.

## Output paths

| Script | Default subdir |
|---|---|
| `dexter_scratchpad.py` | `./financial-research/scratchpad/` |
| `financial_report.py` | `./financial-research/reports/` |
| `screen_a_share.py` | `./financial-research/watchlists/` (and `reports/` when `--report`) |
| `screen_hk_connect.py` | `./financial-research/watchlists/` |
| `hk_connect_universe.py` | `./financial-research/universes/` |

Every script accepts `--out-dir <root>` to override the root; the subdir is appended automatically.

**Hermes users:** to keep the legacy `~/.hermes/reports/financial-research/<subdir>/` layout, pass `--out-dir ~/.hermes/reports/financial-research` to every script.

## Auto-update

The skill checks `<skill-dir>/.last_update` on first use per conversation. If older than 24 hours, it silently runs `git pull --ff-only`. Failures (offline, conflict, not a git checkout) are ignored without interrupting the workflow.

Manual update:
```bash
cd <skill-dir> && git pull
```

## vs no skill

| Capability | Native agent | This skill |
|---|---|---|
| Plan-first + scratchpad | Sometimes | Always (JSONL on disk) |
| Numerical validation checklist | No | Yes (units / currency / period / scale) |
| Bank valuation: skip DCF | Hit-or-miss | Default override to RoTE / CET1 / NIM / P/B |
| Tushare routing + known-bad-interface avoidance | No | Built-in gotchas list |
| Multi-preset stock screening | No | Yes (`a_dividend_quality`, `a_value`, HK Connect) |
| Three-layer report (md+html+pdf) | Manual | One command |
| HK Connect universe export | No | Yes (with date back-fill) |
| Soft loop limits + repeat-query detection | No | Yes (prevents runaway tool use) |

## Disclaimer

This skill produces data analysis and research records, not investment advice. All conclusions require independent judgement against the latest public information.
