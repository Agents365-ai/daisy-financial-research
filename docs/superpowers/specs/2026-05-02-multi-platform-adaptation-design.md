# Multi-Platform Adaptation — Daisy Financial Research

**Date:** 2026-05-02
**Status:** Approved (pending implementation plan)
**Reference skill:** `drawio-skill` (Agents365-ai), used as the structural template.

## Goal

Adapt the existing Hermes-only skill so it installs and runs cleanly on every platform `drawio-skill` supports: Claude Code, Opencode, OpenClaw / ClawHub, Hermes, OpenAI Codex, and SkillsMP. Preserve current script behavior; change only what's needed for cross-platform install, path resolution, and discovery.

## Scope

**In scope**
- SKILL.md frontmatter rewrite (multi-platform metadata namespaces).
- Replace hardcoded `~/.hermes/skills/research/dexter-financial-research/scripts/` paths in SKILL.md with the `<this-skill-dir>/scripts/` placeholder convention.
- Add `--out-dir` flag to every script; default output root becomes `./financial-research/<subdir>/` under the user's cwd.
- Add `agents/openai.yaml` Codex sidecar.
- Add `.last_update` 24h auto-update step at the start of the SKILL.md workflow.
- README.md (Chinese, default) + README_EN.md with multi-platform install matrix and feature comparison table.
- Update repo CLAUDE.md to reflect the new defaults.
- Rename the public skill identity to `daisy-financial-research` (frontmatter `name:`, README titles, homepage URL). Keep the local directory `dexter-financial-research/`. Keep "Dexter" in the body as design-pattern attribution to `virattt/dexter`.

**Out of scope**
- Renaming the local working directory.
- Renaming Python scripts (`dexter_scratchpad.py` etc. — they're called by name from SKILL.md and the `dexter_` prefix matches the directory name).
- Extracting a `scripts/_common.py` helper module — drawio doesn't either; minimum code that solves the task.
- Top-level CLI dispatcher (`daisy.py screen ...`) — preserves the current per-script contract.
- Behavior changes to screening, DCF, or report-generation logic.
- Renaming the `~/.hermes/reports/dexter-scratchpad/` legacy archive — Hermes users opt back in via `--out-dir`.

## Naming

| Surface | Value |
|---|---|
| Public skill name (`name:` in frontmatter) | `daisy-financial-research` |
| Local working directory | `dexter-financial-research/` (unchanged) |
| GitHub repo | `Agents365-ai/daisy-financial-research` (unchanged) |
| README H1 | "Daisy Financial Research" |
| Script filenames | `dexter_*.py`, `screen_*.py`, etc. (unchanged) |
| Output dir name | `financial-research/` |
| Body-text attribution | "inspired by `virattt/dexter` design patterns" — kept |

## Output path strategy

**Default:** `Path.cwd() / "financial-research" / <subdir>/`, where `<subdir>` is one of:

| Script | Subdir |
|---|---|
| `dexter_scratchpad.py` | `scratchpad/` |
| `financial_report.py` | `reports/` |
| `screen_a_share.py` | `watchlists/` (csv/json) and `reports/` (when `--report`) |
| `screen_hk_connect.py` | `watchlists/` |
| `hk_connect_universe.py` | `universes/` |

**Override:** every script accepts `--out-dir <path>`. `--out-dir` always names the **root** (the directory that holds the per-script subdirs); the script then writes into `<root>/<subdir>/` exactly the way it writes into `<cwd>/financial-research/<subdir>/`. So `--out-dir ~/.hermes/reports/financial-research` reproduces the current Hermes layout exactly: watchlists land in `~/.hermes/reports/financial-research/watchlists/`, reports in `~/.hermes/reports/financial-research/reports/`, and so on.

For `screen_a_share.py --report`, this means the same `--out-dir` root produces output in two sibling subdirs (`<root>/watchlists/` for csv/json, `<root>/reports/` for the Markdown). Matches the current script's two-root structure (`OUT_DIR` and `REPORT_DIR`) — they're collapsed into one `--out-dir` plus subdir convention.

**No env var.** A flag is enough; an env var adds a second source of truth and a cache-the-mistake failure mode.

**`mkdir -p` semantics:** scripts create the resolved directory if it doesn't exist (current behavior in the existing scripts already does this for `~/.hermes/...`).

## SKILL.md path placeholder

Replace every literal path of the form
`~/.hermes/skills/research/dexter-financial-research/scripts/X.py`
with
`<this-skill-dir>/scripts/X.py`.

The agent runtime substitutes `<this-skill-dir>` with the actual install path. This is the same convention `drawio-skill/SKILL.md` uses (e.g. `<this-skill-dir>/.last_update`, `<this-skill-dir>/styles/built-in/`). No code change needed in scripts — only in SKILL.md text.

## Frontmatter shape

Modeled on drawio-skill (the JSON `metadata` block keeps OpenClaw's parser happy — it only supports single-line frontmatter values).

```yaml
---
name: daisy-financial-research
description: Use when user asks for stock / company / sector deep-dive research, DCF or valuation, financial comparison, market-catalyst analysis, or stock screening across A-share, Hong Kong, or US markets. Plans, gathers data via Tushare and web search, validates numbers, and produces a sourced report.
license: MIT
homepage: https://github.com/Agents365-ai/daisy-financial-research
compatibility: Requires Python 3.9+ with `tushare`, `pandas`, `requests` for screening / Tushare scripts. TUSHARE_TOKEN env var required for any Tushare call. No external CLI tools needed for the core analysis workflow.
platforms: [macos, linux, windows]
metadata: {"openclaw":{"requires":{"bins":["python3"]},"emoji":"📈","os":["darwin","linux","win32"]},"hermes":{"tags":["finance","research","stocks","valuation","dcf","tushare","agent-workflow","screening"],"category":"research","related_skills":["tushare"]},"author":"Agents365-ai","version":"2.0.0"}
---
```

Notes:
- Bumping to `version: 2.0.0` because the cwd-relative output is a breaking change for any existing Hermes installation.
- `requires.bins: ["python3"]` is loose on purpose — Tushare/pandas are runtime requirements documented in `compatibility:` and the README, not gated by the OpenClaw installer (we don't want to block install for users who only use the non-Tushare parts of the skill, like the DCF checklist or the report template).

## Codex sidecar

`agents/openai.yaml`:

```yaml
interface:
  display_name: "Daisy Financial Research"
  short_description: "Autonomous stock / company / sector research with Tushare data, DCF valuation, screening, and sourced reports."
  brand_color: "#2E7D5B"

policy:
  allow_implicit_invocation: true

capabilities:
  - Plan-then-execute research loop with scratchpad, soft loop limits, and numerical validation
  - DCF workflow with sensitivity analysis and sanity checks
  - Bank/financial-sector valuation (RoTE, CET1, NIM, P/B) instead of DCF
  - A-share and Hong Kong Stock Connect screening with named presets
  - Markdown + HTML (+ optional PDF) sourced reports under ./financial-research/
  - Brave MCP / Bailian WebSearch MCP integration for web context

prerequisites:
  - Python 3.9+ with tushare, pandas, requests
  - TUSHARE_TOKEN environment variable for any Tushare call
```

## Auto-update step

Insert **before** the existing "### 0. State scope and assumptions briefly" section (renumber the existing step 0 to remain step 0 — the auto-update is a silent first-use-per-conversation chore, not a numbered analysis step). Verbatim from drawio with the path swapped:

> Check the timestamp of `<this-skill-dir>/.last_update`. If the file is missing or older than 24 hours, run `git -C <this-skill-dir> pull --ff-only && date +%s > <this-skill-dir>/.last_update`. If the pull fails (offline, conflict, not a git checkout), ignore the error and continue normally. Do not mention the update to the user unless they ask.

## README structure

Two files. Chinese is default (`README.md`); English is `README_EN.md`. Both include the same six sections, ported from drawio-skill's structure:

1. **What it does** — feature bullets (research workflow, screening, DCF, multi-platform output).
2. **Multi-platform support table** — Claude Code / Opencode / OpenClaw / Hermes / OpenAI Codex / SkillsMP, all "✅ Full support".
3. **Comparison vs no skill** — feature table; key differentiators are the scratchpad discipline, numerical validation, and the bank-valuation override.
4. **Prerequisites** — Python + tushare; macOS/Windows/Linux install snippets.
5. **Skill installation** — per-platform `git clone` commands and a path-summary table.
6. **Updates** — describe the `.last_update` auto-check and the manual `git pull` fallback.

Add the `Hermes back-compat note` callout in the installation section: one paragraph showing `--out-dir ~/.hermes/reports/financial-research` to keep the old archive layout.

## CLAUDE.md update

Replace the current "What this repo is" paragraph and "Runtime layout" section to reflect:
- Multi-platform skill, no longer Hermes-specific.
- Default output dir is `./financial-research/<subdir>/` under cwd.
- Hermes users opt back into the old layout with `--out-dir`.
- `<this-skill-dir>/scripts/X.py` is the canonical path convention in SKILL.md (was: hardcoded `~/.hermes/skills/...`).
- Keep the Tushare gotchas, search routing, and bank-valuation sections — none of those change.

## Files inventory

**Modified**
- `SKILL.md` — frontmatter, path placeholders, output dir examples, auto-update step, header.
- `CLAUDE.md` — runtime layout, paths.
- `scripts/dexter_scratchpad.py` — `--out-dir` flag, default to `Path.cwd() / "financial-research" / "scratchpad"`.
- `scripts/financial_report.py` — `--out-dir` flag, default `./financial-research/reports/`.
- `scripts/hk_connect_universe.py` — `--out-dir` flag, default `./financial-research/universes/`.
- `scripts/screen_a_share.py` — `--out-dir` flag, default `./financial-research/watchlists/` and `reports/` for `--report`.
- `scripts/screen_hk_connect.py` — `--out-dir` flag, default `./financial-research/watchlists/`.
- `references/hsbc-hk-bank-research-test-20260429.md` — replace `~/.hermes/venv/bin/python` with the `python` interpreter convention; update scratchpad path to the new default.

**Added**
- `agents/openai.yaml` — Codex sidecar.
- `README.md` — Chinese, multi-platform.
- `README_EN.md` — English.
- `.last_update` — initial timestamp file (will be updated by the auto-update step).
- `docs/superpowers/specs/2026-05-02-multi-platform-adaptation-design.md` — this spec.

**Unchanged**
- `references/dexter-analysis.md`, `references/stock-screening-presets.md`, `templates/screening_report.md` — content is platform-agnostic.

## Backward compatibility

Hermes users must add `--out-dir ~/.hermes/reports/financial-research` to script invocations to keep the old archive paths. The README installation section will document this with a copy-pasteable example. No automatic migration is provided — existing reports stay where they are; new reports follow the new default unless the flag is set.

## Publish-time TODOs (for the user, not implementation)

- Confirm GitHub repo topics on `daisy-financial-research`: `claude-code`, `claude-code-skill`, `claude-skills`, `agent-skills`, `skillsmp`, `openclaw`, `openclaw-skills`, `skill-md`, plus `finance`, `tushare`, `dcf`, `stock-screening`.
- Tag a `v2.0.0` release after merge.
- Add the skill to the workspace inventory table in `~/myagents/myskills/CLAUDE.md` (currently tracks drawio, mermaid, etc. — daisy isn't listed yet).
