# Daisy Financial Research — Autonomous Stock / Company Research Skill

[中文](README_CN.md) | [GitHub](https://github.com/Agents365-ai/daisy-financial-research) | [Releases](https://github.com/Agents365-ai/daisy-financial-research/releases)

## What it does

A multi-platform agent skill for finance research. Given a stock/company/sector topic, it plans the research, pulls structured data from Tushare, searches the web via Brave / Bailian MCP, runs Python for math and valuation, and produces a sourced, reproducible Markdown + HTML (+ optional PDF) report.

Design borrows from `virattt/dexter` — iterative agent loop (plan → gather → validate → answer) — but packaged as a cross-platform skill, no separate CLI.

**Key capabilities:**
- **Agent-native CLI** (v2.1.0+): every script auto-emits a stable `{ok, data, meta}` JSON envelope when stdout isn't a TTY, supports `--schema` introspection and `--dry-run`, with documented exit codes 0–5.
- **Cross-session decision memory** (v2.2.0+): append-only Markdown log with `pending → resolved` lifecycle, atomic rewrites, win-rate / mean-alpha stats. Format wire-compatible with TradingAgents' `memory.py`.
- **AKShare HK fallback** (v2.3.0+): closes the documented `pro.hk_daily_basic` Tushare gap with PE/PB/PS snapshots and ROE/EPS/BPS time series — no Tushare token, lazy-imported.
- **Borrowed prompt library** (v2.4.0+): five reference docs adapted from TradingAgents — Bull / Bear / Synthesis debate, Aggressive / Conservative / Neutral risk debate, reflection prompt, decision schema, China-market analyst framing, technical-indicator cheatsheet. See `references/`.
- **Auto-resolve workflow** (v2.5.0+): `dexter_memory_log.py auto-resolve` fetches `close[decision_date]` and `close[as_of_date]` for the ticker plus the right benchmark (CSI 300 for `*.SH/SZ/BJ`, HSI for `*.HK` with AKShare Sina fallback, SPY for US via yfinance), computes raw + alpha + holding days, then resolves the pending memory-log entry in one call.
- Plan-first workflow with JSONL scratchpad recording every tool call, params, result, assumption.
- DCF valuation with sensitivity matrix and sanity checks.
- Bank / financial-sector valuation override (RoTE / CET1 / NIM / P/B / payout) instead of forcing DCF on the wrong frame.
- A-share + Hong Kong Stock Connect screening presets (dividend-quality, value, momentum, etc.).
- Three-layer report output (md → html → optional pdf), CSS already handles CN/EN font fallback.
- Brave MCP + Bailian WebSearch MCP for web context.

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
# Optional: AKShare HK valuation/fundamentals fallback (no Tushare token needed)
pip install akshare
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

All output lands under `./financial-research/{reports,watchlists,scratchpad,universes,memory}/` in your cwd by default.

## Agent-native CLI

Every script under `scripts/` follows a uniform contract designed for both humans at a terminal and agents calling via subprocess. Same shape, six scripts:

```bash
# Discover the script's parameter and output schema (preferred over --help for agents)
python <skill-dir>/scripts/screen_a_share.py --schema

# Preview the request shape — no Tushare call, no file written
python <skill-dir>/scripts/screen_a_share.py --preset a_value --dry-run

# Force JSON regardless of TTY state
DAISY_FORCE_JSON=1 python <skill-dir>/scripts/screen_a_share.py --preset a_value
```

**Output auto-detection:** when stdout is not a TTY, scripts emit a single JSON envelope. When stdout *is* a TTY, scripts emit the legacy human table. Override with `--format json|table`.

**Success envelope:**
```json
{
  "ok": true,
  "data": { "trade_date": "20260430", "candidates": 50, "csv": "...", "preview": [...] },
  "meta": { "schema_version": "1.0.0", "request_id": "req_abc123", "latency_ms": 412 }
}
```

**Error envelope:**
```json
{
  "ok": false,
  "error": { "code": "no_data", "message": "...", "retryable": true, "context": {...} },
  "meta": { ... }
}
```

**Exit codes:** `0` ok · `1` runtime · `2` auth · `3` validation · `4` no_data · `5` dependency.

**Long-running operations** (`screen_hk_connect.py --with-momentum`, `financial_report.py`) emit NDJSON progress events on stderr, one JSON line per phase, so an agent can detect liveness without blocking on stdout.

## Output paths

| Script | Default subdir | Purpose |
|---|---|---|
| `dexter_scratchpad.py` | `./financial-research/scratchpad/` | Per-task JSONL of tool calls, params, results, assumptions |
| `dexter_memory_log.py` | `./financial-research/memory/` | Cross-session decision log; `pending → resolved` lifecycle. Subcommands include `auto-resolve` (v2.5.0+) which fetches close prices and benchmark, computes raw + alpha, and persists the resolution in one call |
| `financial_report.py` | `./financial-research/reports/` | Markdown → HTML → optional PDF report renderer |
| `screen_a_share.py` | `./financial-research/watchlists/` (+ `reports/` with `--report`) | A-share multi-factor screener (presets) |
| `screen_hk_connect.py` | `./financial-research/watchlists/` | HK Stock Connect screener (only when 港股通 explicitly requested) |
| `hk_connect_universe.py` | `./financial-research/universes/` | Southbound Stock Connect universe export |
| `akshare_hk_valuation.py` | (read-only) | HK PE/PB/PS + ROE/EPS via AKShare — closes `pro.hk_daily_basic` gap |

Every script accepts `--out-dir <root>` to override the root; the subdir is appended automatically.

**Hermes users:** to keep the legacy `~/.hermes/reports/financial-research/<subdir>/` layout, pass `--out-dir ~/.hermes/reports/financial-research` to every script.

## Development with uv

`pyproject.toml` lists the runtime dependencies. Reproduce the env locally:

```bash
uv sync                  # core: tushare / pandas / numpy / requests
uv sync --extra akshare  # also: akshare (HK valuation + HSI benchmark fallback)
uv sync --extra us       # also: yfinance (US tickers in auto-resolve)
uv sync --all-extras     # everything
```

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
| Cross-session decision memory | No | Append-only Markdown log + win-rate / mean-alpha stats |
| Agent-native CLI (JSON envelopes, schema introspection, dry-run) | Manual | Built-in for every script |
| Numerical validation checklist | No | Yes (units / currency / period / scale) |
| Bank valuation: skip DCF | Hit-or-miss | Default override to RoTE / CET1 / NIM / P/B |
| Tushare routing + known-bad-interface avoidance | No | Built-in gotchas list + AKShare fallback for HK |
| Multi-preset stock screening | No | Yes (`a_dividend_quality`, `a_value`, HK Connect) |
| Three-layer report (md+html+pdf) | Manual | One command |
| HK Connect universe export | No | Yes (with date back-fill) |
| Soft loop limits + repeat-query detection | No | Yes (prevents runaway tool use) |
| Bull / bear / risk debate prompts | No | `references/debate-prompts.md`, `references/risk-debate-prompts.md` |
| Decision schema (5-tier rating + markdown render contract) | No | `references/decision-schema.md` |
| China-market analyst framing | No | `references/cn-market-analyst-prompts.md` |
| Auto-resolve memory log (fetch close + benchmark, compute alpha) | No | `dexter_memory_log.py auto-resolve` |

## Disclaimer

This skill produces data analysis and research records, not investment advice. All conclusions require independent judgement against the latest public information.

## Support

If this skill helps you, consider supporting the author:

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Agents365-ai/images_payment/main/qrcode/wechat-pay.png" width="180" alt="WeChat Pay">
      <br>
      <b>WeChat Pay</b>
    </td>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Agents365-ai/images_payment/main/qrcode/alipay.png" width="180" alt="Alipay">
      <br>
      <b>Alipay</b>
    </td>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Agents365-ai/images_payment/main/qrcode/buymeacoffee.png" width="180" alt="Buy Me a Coffee">
      <br>
      <b>Buy Me a Coffee</b>
    </td>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Agents365-ai/images_payment/main/awarding/award.gif" width="180" alt="Give a Reward">
      <br>
      <b>Give a Reward</b>
    </td>
  </tr>
</table>

## Author

- Bilibili: https://space.bilibili.com/1107534197
- GitHub: https://github.com/Agents365-ai
