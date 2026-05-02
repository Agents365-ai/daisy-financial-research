# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A multi-platform agent skill (`daisy-financial-research`) for stock / company / sector research, DCF valuation, and stock screening. The local working directory is `dexter-financial-research/` (legacy name retained); the published name and the GitHub repo are `daisy-financial-research`. Loaded by Claude Code, Opencode, OpenClaw / ClawHub, Hermes, OpenAI Codex, and SkillsMP.

The canonical user-facing contract is `SKILL.md`; everything else (`scripts/`, `references/`, `templates/`, `agents/openai.yaml`) supports it. When changing behavior the user will see, update `SKILL.md` in lockstep with the scripts — they reference each other by exact path and CLI flag.

## Runtime layout

By default, every script writes under the user's current working directory:

- `./financial-research/reports/` — final reports (md/html/pdf).
- `./financial-research/watchlists/` — screener outputs.
- `./financial-research/scratchpad/` — per-task JSONL scratchpads.
- `./financial-research/universes/` — HK Connect universe exports.

Each script accepts `--out-dir <root>` to redirect the root; subdirs are appended automatically. Hermes users who want the legacy `~/.hermes/reports/financial-research/<subdir>/` layout pass `--out-dir ~/.hermes/reports/financial-research`.

`TUSHARE_TOKEN` env var is required for any `screen_*`, `hk_connect_universe.py`, or other Tushare-backed call.

`SKILL.md` examples use a bare `python` placeholder; the caller substitutes whichever interpreter has `tushare`, `pandas`, `requests` installed (system `python3`, `~/.hermes/venv/bin/python`, `~\.hermes\venv\Scripts\python.exe`, conda, `uv run python`, pyenv...). Do not reintroduce hardcoded interpreter paths — the convention is documented in `SKILL.md` under "Python interpreter convention".

`SKILL.md` references scripts by the placeholder `<this-skill-dir>/scripts/X.py` (the same placeholder convention drawio-skill uses). The agent runtime substitutes the actual install dir. Do not reintroduce hardcoded `~/.<platform>/skills/.../scripts/` paths in SKILL.md.

Renaming a script is a breaking change to the skill contract.

## Scripts

- `scripts/dexter_scratchpad.py` — `init` / `add` / `show` subcommands; appends JSONL records of tool calls and results. Default output: `./financial-research/scratchpad/`.
- `scripts/financial_report.py` — copies a Markdown source to the reports dir and renders HTML; `--pdf` adds PDF (best-effort, may no-op if no HTML→PDF tool is available). Default output: `./financial-research/reports/`.
- `scripts/hk_connect_universe.py` — `pro.hk_hold(...)` based HK Stock Connect (港股通) universe export. Searches backward when the requested date has no data. Default output: `./financial-research/universes/`.
- `scripts/screen_a_share.py` — A-share screener with named presets (see `references/stock-screening-presets.md`); `--report` emits a Markdown source that `financial_report.py` can render. Default outputs: `./financial-research/watchlists/` (csv/json) and `./financial-research/reports/` (when `--report`).
- `scripts/screen_hk_connect.py` — HK Stock Connect screener; only used when 港股通 is explicitly requested. Default output: `./financial-research/watchlists/`.

All scripts accept `--out-dir <root>`; subdirs are appended automatically.

## Tushare gotchas (verified in this env)

- `pro.hk_daily_basic(...)` returns `请指定正确的接口名` — treat as unavailable.
- `pro.hk_basic`, `pro.hk_daily`, `pro.hk_hold`, `pro.ggt_top10`, `pro.ggt_daily`, `pro.moneyflow_hsgt` are known-working.
- Date format is `YYYYMMDD` strings (not `YYYY-MM-DD`), ts_codes are `000001.SZ` / `600000.SH` / `00005.HK`.

## Search routing (do not change without user sign-off)

The skill commits to a specific finance-search stack: Tushare for structured data, **Brave MCP** as primary web search, **Bailian WebSearch MCP** as Chinese/China-market supplement, Python for math, browser only for dynamic pages. Asta/Semantic Scholar is explicitly **not** part of the finance route.

## Bank/financial-sector valuation

For banks (HSBC etc.), DCF is the wrong primary frame. Use RoTE/ROE, CET1, payout/yield, NIM/NII, credit cost, P/B or P/E, buyback capacity. The HSBC test workflow and pitfalls are recorded in `references/hsbc-hk-bank-research-test-20260429.md` — consult before changing bank-related logic.
