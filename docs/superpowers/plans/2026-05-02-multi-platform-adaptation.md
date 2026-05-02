# Multi-Platform Adaptation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adapt the Hermes-only `dexter-financial-research` skill so it installs and runs cleanly on Claude Code, Opencode, OpenClaw / ClawHub, Hermes, OpenAI Codex, and SkillsMP — public name `daisy-financial-research`, default output to cwd, drawio-skill structural parity.

**Architecture:** Mechanical refactor; no behavior changes. Each Python script gets a `--out-dir <root>` flag and writes into `<root>/<subdir>/` (default root: `Path.cwd() / "financial-research"`). SKILL.md rewrites frontmatter to add `homepage`/`compatibility`/`platforms`/JSON `metadata` (drawio shape), replaces every `~/.hermes/skills/research/dexter-financial-research/scripts/X.py` with `<this-skill-dir>/scripts/X.py`, and prepends a `.last_update` 24h auto-update step. Two new READMEs (CN default, EN) and one Codex sidecar complete the platform adapters.

**Tech Stack:** Python 3.9+, argparse, pathlib (no new deps). Markdown for SKILL.md / READMEs. YAML for the Codex sidecar.

**Reference spec:** `docs/superpowers/specs/2026-05-02-multi-platform-adaptation-design.md`

**No-test pattern:** The repo has no test infrastructure today, and drawio-skill (the structural reference) doesn't either. Each script-modification task therefore ends with an **inline verification command** that exercises the new behavior. Tushare-free scripts (`dexter_scratchpad.py`, `financial_report.py`) get full functional verification (run, check file at expected path). Tushare-required scripts (the three screeners) get `--help` smoke checks plus a Python `-c` import-and-call check on the path-resolution code path.

**Path-resolution convention (used by every script in this plan):**

```python
DEFAULT_ROOT_NAME = "financial-research"  # constant per script

def resolve_out_dir(arg_out_dir: str | None, subdir: str) -> Path:
    """Return <root>/<subdir>, where <root> defaults to cwd/financial-research."""
    if arg_out_dir:
        root = Path(arg_out_dir).expanduser()
    else:
        root = Path.cwd() / DEFAULT_ROOT_NAME
    out = root / subdir
    out.mkdir(parents=True, exist_ok=True)
    return out
```

This snippet is repeated inline in each script (no shared module — see spec). The function name and signature MUST match across scripts so the plan's verification commands work uniformly.

---

## Task 1: Add Codex sidecar `agents/openai.yaml`

**Files:**
- Create: `agents/openai.yaml`

- [ ] **Step 1: Create the directory and file**

```bash
mkdir -p agents
```

Write `agents/openai.yaml`:

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

- [ ] **Step 2: Verify the file is valid YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('agents/openai.yaml'))" && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add agents/openai.yaml
git commit -m "feat: add OpenAI Codex sidecar (agents/openai.yaml)"
```

---

## Task 2: Add initial `.last_update` timestamp

**Files:**
- Create: `.last_update`
- Modify: `.gitignore` (verify `.last_update` is NOT ignored — it ships with the repo as the seed value)

- [ ] **Step 1: Check `.gitignore`**

Run: `grep -n "last_update" .gitignore || echo "not ignored — good"`
Expected: `not ignored — good`. If a match is printed, remove that line from `.gitignore` so the file ships with the repo.

- [ ] **Step 2: Write the seed timestamp**

Run: `date +%s > .last_update`
Run: `cat .last_update | head -c 30 && echo`
Expected: a 10-digit unix timestamp followed by a newline.

- [ ] **Step 3: Commit**

```bash
git add .last_update .gitignore
git commit -m "feat: seed .last_update for 24h auto-update check"
```

---

## Task 3: `dexter_scratchpad.py` — `--out-dir` flag, default to cwd

**Files:**
- Modify: `scripts/dexter_scratchpad.py:19` (replace `BASE_DIR`), `:42-48` (cmd_init wires the flag)

- [ ] **Step 1: Replace the BASE_DIR constant block**

Replace lines 18–20 of `scripts/dexter_scratchpad.py`:

```python
# old (line 19):
BASE_DIR = Path(os.path.expanduser("~/.hermes/reports/dexter-scratchpad"))
```

With:

```python
DEFAULT_ROOT_NAME = "financial-research"
SUBDIR = "scratchpad"


def resolve_out_dir(arg_out_dir: str | None) -> Path:
    """Return <root>/scratchpad, where <root> defaults to cwd/financial-research."""
    if arg_out_dir:
        root = Path(arg_out_dir).expanduser()
    else:
        root = Path.cwd() / DEFAULT_ROOT_NAME
    out = root / SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    return out
```

- [ ] **Step 2: Wire the flag into `cmd_init`**

Replace the body of `cmd_init` (lines 42–48):

```python
def cmd_init(args: argparse.Namespace) -> None:
    query = args.query
    h = hashlib.md5(query.encode("utf-8")).hexdigest()[:12]
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = resolve_out_dir(args.out_dir)
    path = out_dir / f"{ts}_{h}.jsonl"
    append(path, {"type": "init", "timestamp": now_iso(), "query": query})
    print(path)
```

- [ ] **Step 3: Add the `--out-dir` argparse flag to the `init` subparser**

In `main()`, find the block:

```python
    p_init = sub.add_parser("init")
    p_init.add_argument("query")
    p_init.set_defaults(func=cmd_init)
```

Replace with:

```python
    p_init = sub.add_parser("init")
    p_init.add_argument("query")
    p_init.add_argument("--out-dir", dest="out_dir", default=None,
                        help="Output root; default <cwd>/financial-research/")
    p_init.set_defaults(func=cmd_init)
```

- [ ] **Step 4: Verify default cwd behavior**

```bash
TMP=$(mktemp -d) && (cd "$TMP" && python3 scripts_path/dexter_scratchpad.py init "test query" 2>&1)
```

Replace `scripts_path` with the absolute path to `scripts/dexter_scratchpad.py` from the repo root. Expected: prints a path of the form `<TMP>/financial-research/scratchpad/YYYYMMDD-HHMMSS_<hash>.jsonl`. Verify the file exists:

```bash
ls "$TMP/financial-research/scratchpad/"
```

Expected: one `.jsonl` file is listed.

- [ ] **Step 5: Verify `--out-dir` override**

```bash
TMP=$(mktemp -d) && python3 "$(pwd)/scripts/dexter_scratchpad.py" init "override test" --out-dir "$TMP/custom"
ls "$TMP/custom/scratchpad/"
```

Expected: prints a path of the form `<TMP>/custom/scratchpad/YYYYMMDD-HHMMSS_<hash>.jsonl`; `ls` lists exactly that file.

- [ ] **Step 6: Commit**

```bash
git add scripts/dexter_scratchpad.py
git commit -m "refactor(scratchpad): default output to ./financial-research/scratchpad/, add --out-dir"
```

---

## Task 4: `financial_report.py` — change default, keep existing `--out-dir` flag

**Files:**
- Modify: `scripts/financial_report.py:29` (BASE_DIR), `:258` (default for `--out-dir`), `:273-275` (writes use the flag)

- [ ] **Step 1: Replace the BASE_DIR constant**

Replace line 29 of `scripts/financial_report.py`:

```python
# old:
BASE_DIR = Path(os.path.expanduser("~/.hermes/reports/financial-research"))
```

With:

```python
DEFAULT_ROOT_NAME = "financial-research"
SUBDIR = "reports"


def resolve_out_dir(arg_out_dir: str | None) -> Path:
    """Return <root>/reports, where <root> defaults to cwd/financial-research."""
    if arg_out_dir:
        root = Path(arg_out_dir).expanduser()
    else:
        root = Path.cwd() / DEFAULT_ROOT_NAME
    out = root / SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    return out
```

The unused `os` import already exists; leave it.

- [ ] **Step 2: Update the `--out-dir` argparse default to `None`**

In `main()`, find:

```python
    parser.add_argument("--out-dir", default=str(BASE_DIR), help="Output directory")
```

Replace with:

```python
    parser.add_argument("--out-dir", dest="out_dir", default=None,
                        help="Output root; default <cwd>/financial-research/ (reports/ subdir auto-appended)")
```

- [ ] **Step 3: Update the body of `main()` to use the resolver**

Find this block (around line 272-273):

```python
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
```

Replace with:

```python
    out_dir = resolve_out_dir(args.out_dir).resolve()
```

- [ ] **Step 4: Verify default cwd behavior**

```bash
TMP=$(mktemp -d) && cat > "$TMP/in.md" <<'EOF'
# Test Report

Body.
EOF
(cd "$TMP" && python3 "$(pwd | sed 's|/private||')/scripts/financial_report.py" "$TMP/in.md" --slug test 2>&1)
ls "$TMP/financial-research/reports/"
```

(The `sed` strips the `/private` prefix that macOS's `mktemp` adds; on Linux it's a no-op.) Expected: `markdown:`, `html:`, and `pdf: skipped` lines printed; `ls` shows two files matching `*_test.md` and `*_test.html` under `<TMP>/financial-research/reports/`.

- [ ] **Step 5: Verify `--out-dir` override**

```bash
TMP=$(mktemp -d) && cat > "$TMP/in.md" <<'EOF'
# Override Test
EOF
python3 "$(pwd)/scripts/financial_report.py" "$TMP/in.md" --slug override --out-dir "$TMP/custom"
ls "$TMP/custom/reports/"
```

Expected: files appear at `<TMP>/custom/reports/*_override.{md,html}`.

- [ ] **Step 6: Commit**

```bash
git add scripts/financial_report.py
git commit -m "refactor(financial_report): default output to ./financial-research/reports/"
```

---

## Task 5: `hk_connect_universe.py` — add `--out-dir`, keep `--out`

**Files:**
- Modify: `scripts/hk_connect_universe.py:23` (OUT_DIR), `:55` (`--out` arg unchanged), `:79-80` (output path resolution)

The existing `--out` flag (full file path) stays for backward compatibility; the new `--out-dir` controls the root when `--out` is not given.

- [ ] **Step 1: Replace the OUT_DIR constant**

Replace line 23 of `scripts/hk_connect_universe.py`:

```python
# old:
OUT_DIR = Path(os.path.expanduser("~/.hermes/reports/financial-research"))
```

With:

```python
DEFAULT_ROOT_NAME = "financial-research"
SUBDIR = "universes"


def resolve_out_dir(arg_out_dir: str | None) -> Path:
    """Return <root>/universes, where <root> defaults to cwd/financial-research."""
    if arg_out_dir:
        root = Path(arg_out_dir).expanduser()
    else:
        root = Path.cwd() / DEFAULT_ROOT_NAME
    out = root / SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    return out
```

- [ ] **Step 2: Add the `--out-dir` flag**

In `main()`, find the existing `--out` argument and add a sibling `--out-dir` immediately after:

```python
    ap.add_argument("--out", help="Output CSV path; overrides --out-dir if given")
    ap.add_argument("--out-dir", dest="out_dir", default=None,
                    help="Output root; default <cwd>/financial-research/ (universes/ subdir auto-appended)")
```

(Note: also update the existing `--out` help text from `default under ~/.hermes/...` to the new wording above.)

- [ ] **Step 3: Replace the output-path block**

Find lines 79–80:

```python
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out).expanduser() if args.out else OUT_DIR / f"{trade_date}_hk-connect-universe.csv"
```

Replace with:

```python
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = resolve_out_dir(args.out_dir)
        out = out_dir / f"{trade_date}_hk-connect-universe.csv"
```

- [ ] **Step 4: Verify `--help` advertises both flags**

```bash
python3 scripts/hk_connect_universe.py --help 2>&1 | grep -E "(--out|--out-dir)"
```

Expected: two lines, one for each flag, with the updated help text. (`--help` does not require Tushare or a token.)

- [ ] **Step 5: Verify path resolution via Python import (no Tushare call)**

```bash
python3 -c "
import sys, pathlib
sys.path.insert(0, 'scripts')
from hk_connect_universe import resolve_out_dir
import tempfile
tmp = tempfile.mkdtemp()
p = resolve_out_dir(tmp + '/custom')
assert str(p) == tmp + '/custom/universes', p
assert p.exists(), 'mkdir failed'
print('OK', p)
"
```

Expected: `OK <TMP>/custom/universes`.

- [ ] **Step 6: Commit**

```bash
git add scripts/hk_connect_universe.py
git commit -m "refactor(hk_connect_universe): add --out-dir defaulting to ./financial-research/universes/"
```

---

## Task 6: `screen_a_share.py` — collapse two roots into one `--out-dir`

**Files:**
- Modify: `scripts/screen_a_share.py:27-28` (OUT_DIR + REPORT_DIR), `:120-168` (`make_markdown` writes to REPORT_DIR), `:171-181` (argparse), `:231-243` (output writes)

This script has two output roots today: `OUT_DIR` for csv/json watchlists, `REPORT_DIR` for the optional Markdown report. The new `--out-dir <root>` flag controls both: watchlists go to `<root>/watchlists/`, the report goes to `<root>/reports/`. Default root: `./financial-research/`.

- [ ] **Step 1: Replace both OUT_DIR / REPORT_DIR constants with a single resolver**

Replace lines 27–28:

```python
# old:
OUT_DIR = Path(os.path.expanduser("~/.hermes/reports/financial-research/watchlists"))
REPORT_DIR = Path(os.path.expanduser("~/.hermes/reports/financial-research"))
```

With:

```python
DEFAULT_ROOT_NAME = "financial-research"


def resolve_out_dir(arg_out_dir: str | None, subdir: str) -> Path:
    """Return <root>/<subdir>, where <root> defaults to cwd/financial-research."""
    if arg_out_dir:
        root = Path(arg_out_dir).expanduser()
    else:
        root = Path.cwd() / DEFAULT_ROOT_NAME
    out = root / subdir
    out.mkdir(parents=True, exist_ok=True)
    return out
```

- [ ] **Step 2: Update `make_markdown` to take the resolved report dir as an arg**

Change the signature of `make_markdown` (line 120) from:

```python
def make_markdown(df: pd.DataFrame, args, trade_date: str, csv_path: Path) -> Path:
```

To:

```python
def make_markdown(df: pd.DataFrame, args, trade_date: str, csv_path: Path, report_dir: Path) -> Path:
```

Replace the function's tail (lines 165–168):

```python
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{trade_date}_a-share-{args.preset}-screen.md"
    path.write_text(md, encoding="utf-8")
    return path
```

With:

```python
    path = report_dir / f"{trade_date}_a-share-{args.preset}-screen.md"
    path.write_text(md, encoding="utf-8")
    return path
```

(`report_dir` is already created by `resolve_out_dir`.)

- [ ] **Step 3: Add the `--out-dir` argparse flag**

In `main()` (after line 181 `args = ap.parse_args()` is parsed — add the flag declaration earlier in the parser, immediately after the `--report` arg around line 180):

```python
    ap.add_argument("--report", action="store_true", help="Create Markdown report source")
    ap.add_argument("--out-dir", dest="out_dir", default=None,
                    help="Output root; default <cwd>/financial-research/ (watchlists/ and reports/ subdirs auto-appended)")
```

- [ ] **Step 4: Update the output-write block (around lines 231–243)**

Find:

```python
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = OUT_DIR / f"{trade_date}_a_share_{args.preset}"
    csv_path = base.with_suffix(".csv")
    json_path = base.with_suffix(".json")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(df.to_json(orient="records", force_ascii=False, indent=2), encoding="utf-8")

    print(f"trade_date: {trade_date}")
    print(f"candidates: {len(df)}")
    print(f"csv: {csv_path}")
    print(f"json: {json_path}")
    if args.report:
        md_path = make_markdown(df, args, trade_date, csv_path)
        print(f"markdown_report_source: {md_path}")
```

Replace with:

```python
    watchlist_dir = resolve_out_dir(args.out_dir, "watchlists")
    base = watchlist_dir / f"{trade_date}_a_share_{args.preset}"
    csv_path = base.with_suffix(".csv")
    json_path = base.with_suffix(".json")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(df.to_json(orient="records", force_ascii=False, indent=2), encoding="utf-8")

    print(f"trade_date: {trade_date}")
    print(f"candidates: {len(df)}")
    print(f"csv: {csv_path}")
    print(f"json: {json_path}")
    if args.report:
        report_dir = resolve_out_dir(args.out_dir, "reports")
        md_path = make_markdown(df, args, trade_date, csv_path, report_dir)
        print(f"markdown_report_source: {md_path}")
```

- [ ] **Step 5: Verify `--help` shows the new flag**

```bash
python3 scripts/screen_a_share.py --help 2>&1 | grep -- "--out-dir"
```

Expected: one line with `--out-dir` and the help text mentioning watchlists/ and reports/ subdirs.

- [ ] **Step 6: Verify path resolution via Python import (no Tushare call)**

```bash
python3 -c "
import sys, tempfile
sys.path.insert(0, 'scripts')
from screen_a_share import resolve_out_dir
tmp = tempfile.mkdtemp()
w = resolve_out_dir(tmp + '/r', 'watchlists')
r = resolve_out_dir(tmp + '/r', 'reports')
assert str(w).endswith('/r/watchlists') and w.exists(), w
assert str(r).endswith('/r/reports') and r.exists(), r
print('OK', w, r)
"
```

Expected: `OK <TMP>/r/watchlists <TMP>/r/reports`.

- [ ] **Step 7: Commit**

```bash
git add scripts/screen_a_share.py
git commit -m "refactor(screen_a_share): collapse OUT_DIR/REPORT_DIR into single --out-dir root"
```

---

## Task 7: `screen_hk_connect.py` — add `--out-dir`

**Files:**
- Modify: `scripts/screen_hk_connect.py:21` (OUT_DIR), `:63-70` (argparse), `:103-105` (output writes)

- [ ] **Step 1: Replace the OUT_DIR constant**

Replace line 21:

```python
# old:
OUT_DIR = Path(os.path.expanduser("~/.hermes/reports/financial-research/watchlists"))
```

With:

```python
DEFAULT_ROOT_NAME = "financial-research"
SUBDIR = "watchlists"


def resolve_out_dir(arg_out_dir: str | None) -> Path:
    """Return <root>/watchlists, where <root> defaults to cwd/financial-research."""
    if arg_out_dir:
        root = Path(arg_out_dir).expanduser()
    else:
        root = Path.cwd() / DEFAULT_ROOT_NAME
    out = root / SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    return out
```

- [ ] **Step 2: Add the `--out-dir` argparse flag**

In `main()`, after the `--with-momentum` line (around line 70):

```python
    ap.add_argument("--with-momentum", action="store_true", help="Fetch hk_daily to compute approx 3M return for pool")
    ap.add_argument("--out-dir", dest="out_dir", default=None,
                    help="Output root; default <cwd>/financial-research/ (watchlists/ subdir auto-appended)")
```

- [ ] **Step 3: Replace the output-write block (around lines 103–105)**

Find:

```python
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "with_momentum" if args.with_momentum else "southbound_ratio"
    base = OUT_DIR / f"{trade_date}_hk_connect_{suffix}"
```

Replace with:

```python
    out_dir = resolve_out_dir(args.out_dir)
    suffix = "with_momentum" if args.with_momentum else "southbound_ratio"
    base = out_dir / f"{trade_date}_hk_connect_{suffix}"
```

- [ ] **Step 4: Verify `--help` shows the flag**

```bash
python3 scripts/screen_hk_connect.py --help 2>&1 | grep -- "--out-dir"
```

Expected: one line with `--out-dir`.

- [ ] **Step 5: Verify path resolution via Python import**

```bash
python3 -c "
import sys, tempfile
sys.path.insert(0, 'scripts')
from screen_hk_connect import resolve_out_dir
tmp = tempfile.mkdtemp()
p = resolve_out_dir(tmp + '/r')
assert str(p) == tmp + '/r/watchlists' and p.exists(), p
print('OK', p)
"
```

Expected: `OK <TMP>/r/watchlists`.

- [ ] **Step 6: Commit**

```bash
git add scripts/screen_hk_connect.py
git commit -m "refactor(screen_hk_connect): add --out-dir defaulting to ./financial-research/watchlists/"
```

---

## Task 8: SKILL.md frontmatter rewrite + branding

**Files:**
- Modify: `SKILL.md:1-13` (frontmatter + H1)

- [ ] **Step 1: Replace lines 1–13**

Replace the existing frontmatter block (lines 1–11) and the H1 + intro paragraph (lines 13–15):

```markdown
---
name: dexter-financial-research
description: Dexter-inspired autonomous financial research workflow for Hermes. Use when the user asks for deep stock/company/sector research, DCF or valuation, financial comparison, market-catalyst analysis, or wants an agentic finance analyst that plans, gathers data, validates, and produces a sourced answer.
version: 1.0.0
author: Hermes Agent, adapted from virattt/dexter key design patterns
license: MIT
metadata:
  hermes:
    tags: [finance, research, stocks, valuation, dcf, tushare, agent-workflow]
    related_skills: [tushare]
---

# Dexter Financial Research

This skill ports the useful parts of `virattt/dexter` into a Hermes skill rather than a separate TypeScript CLI agent.
```

With:

```markdown
---
name: daisy-financial-research
description: Use when user asks for stock / company / sector deep-dive research, DCF or valuation, financial comparison, market-catalyst analysis, or stock screening across A-share, Hong Kong, or US markets. Plans, gathers data via Tushare and web search, validates numbers, and produces a sourced report.
license: MIT
homepage: https://github.com/Agents365-ai/daisy-financial-research
compatibility: Requires Python 3.9+ with `tushare`, `pandas`, `requests` for screening / Tushare scripts. TUSHARE_TOKEN env var required for any Tushare call. No external CLI tools needed for the core analysis workflow.
platforms: [macos, linux, windows]
metadata: {"openclaw":{"requires":{"bins":["python3"]},"emoji":"📈","os":["darwin","linux","win32"]},"hermes":{"tags":["finance","research","stocks","valuation","dcf","tushare","agent-workflow","screening"],"category":"research","related_skills":["tushare"]},"author":"Agents365-ai","version":"2.0.0"}
---

# Daisy Financial Research

Autonomous stock / company / sector research workflow — plan, gather data, validate numbers, produce a sourced report. Inspired by the `virattt/dexter` design patterns (iterative agent loop, scratchpad, soft loop limits, numerical validation), packaged as a multi-platform skill.
```

- [ ] **Step 2: Verify the frontmatter parses as YAML**

```bash
python3 -c "
import yaml
with open('SKILL.md') as f:
    text = f.read()
fm = text.split('---', 2)[1]
data = yaml.safe_load(fm)
assert data['name'] == 'daisy-financial-research', data['name']
assert data['homepage'].startswith('https://github.com/'), data['homepage']
assert 'macos' in data['platforms'], data['platforms']
import json
md = json.loads(data['metadata']) if isinstance(data['metadata'], str) else data['metadata']
assert md['author'] == 'Agents365-ai', md
assert md['version'] == '2.0.0', md
print('OK')
"
```

Expected: `OK`. (The `metadata` field is JSON-on-one-line, which is valid YAML — `yaml.safe_load` returns it as a dict.)

- [ ] **Step 3: Commit**

```bash
git add SKILL.md
git commit -m "feat(skill): rebrand to daisy-financial-research, add multi-platform frontmatter"
```

---

## Task 9: SKILL.md path placeholders + auto-update step + output-path examples

**Files:**
- Modify: `SKILL.md` — every literal `~/.hermes/skills/research/dexter-financial-research/scripts/` becomes `<this-skill-dir>/scripts/`; output-path examples use the new `./financial-research/<subdir>/` defaults; an auto-update step is inserted before the existing "### 0. State scope and assumptions briefly".

- [ ] **Step 1: Replace every script-path occurrence**

Run a check first:

```bash
grep -n "~/\.hermes/skills/research/dexter-financial-research" SKILL.md
```

Expected: roughly 6 matches (the `scripts/dexter_scratchpad.py`, `scripts/financial_report.py`, `scripts/hk_connect_universe.py`, `scripts/screen_a_share.py`, `scripts/screen_hk_connect.py` invocations).

Then replace all of them in one pass:

```bash
python3 -c "
import re
text = open('SKILL.md').read()
new = text.replace('~/.hermes/skills/research/dexter-financial-research/scripts/', '<this-skill-dir>/scripts/')
open('SKILL.md', 'w').write(new)
print('replacements:', text.count('~/.hermes/skills/research/dexter-financial-research/scripts/'))
"
```

Expected: prints `replacements: 6` (or whatever the grep count was).

- [ ] **Step 2: Replace output-path examples in SKILL.md**

The skill currently mentions these output paths in narrative text:
- `~/.hermes/reports/financial-research/` (reports root)
- `~/.hermes/reports/financial-research/watchlists/` (watchlists)
- `~/.hermes/reports/dexter-scratchpad/` (scratchpad)

Update them to the new defaults. Use `Edit` to do these replacements one at a time (avoid global replace because some places mention the old path as a back-compat hint that we'll add separately):

| Old | New |
|---|---|
| `~/.hermes/reports/financial-research/` | `./financial-research/reports/` (the cwd-relative default) |
| `~/.hermes/reports/financial-research/watchlists/` | `./financial-research/watchlists/` |
| `~/.hermes/reports/dexter-scratchpad/` | `./financial-research/scratchpad/` |

Verify all three legacy paths are gone:

```bash
grep -n "~/\.hermes/reports/" SKILL.md && echo "FAIL: still references" || echo "OK: clean"
```

Expected: `OK: clean`. (If any line is printed, edit it manually.)

- [ ] **Step 3: Add Hermes back-compat callout**

Find the "### 7. Report export policy" section (around line 178). Immediately after the line that says `~/.hermes/reports/financial-research/` was the old path — wait, it's been replaced — re-read the section as it stands now and add this paragraph after the "Default behavior:" bulleted list:

```markdown
**Hermes back-compat note.** Hermes installations that want to keep the legacy archive layout (`~/.hermes/reports/financial-research/`) can pass `--out-dir ~/.hermes/reports/financial-research` to any script — the script appends the matching subdir (`reports/`, `watchlists/`, `universes/`, `scratchpad/`) automatically.
```

- [ ] **Step 4: Insert the auto-update step**

Find the `## Mandatory workflow` heading (around line 51). Insert this block immediately after that heading and before `### 0. State scope and assumptions briefly`:

```markdown
### Auto-update (first use per conversation)

Silently keep the skill current. Check the timestamp of `<this-skill-dir>/.last_update`. If the file is missing or older than 24 hours, run:

```bash
git -C <this-skill-dir> pull --ff-only && date +%s > <this-skill-dir>/.last_update
```

If the pull fails (offline, conflict, not a git checkout, etc.), ignore the error and continue normally. Do not mention the update to the user unless they ask.

```

(Triple-fenced code block inside the section. The existing `### 0. State scope and assumptions briefly` keeps its number — the auto-update is unnumbered, drawio's convention.)

- [ ] **Step 5: Verify all four content changes landed**

```bash
grep -c "<this-skill-dir>/scripts/" SKILL.md
grep -c "Hermes back-compat note" SKILL.md
grep -c "Auto-update (first use per conversation)" SKILL.md
grep -c "\.last_update" SKILL.md
```

Expected, in order: ≥6, 1, 1, ≥2.

- [ ] **Step 6: Commit**

```bash
git add SKILL.md
git commit -m "feat(skill): use <this-skill-dir> placeholder, add auto-update + cwd output examples"
```

---

## Task 10: Update `references/hsbc-hk-bank-research-test-20260429.md`

**Files:**
- Modify: `references/hsbc-hk-bank-research-test-20260429.md:19` (Python interpreter path), `:89` (scratchpad path)

- [ ] **Step 1: Inspect the two lines**

```bash
sed -n '15,25p;85,95p' references/hsbc-hk-bank-research-test-20260429.md
```

- [ ] **Step 2: Replace the interpreter path on line 19**

Use `Edit` to replace the literal `\`~/.hermes/venv/bin/python\`` with `\`python\` (whichever interpreter has \`tushare\`, \`pandas\`, \`requests\` installed; see SKILL.md "Python interpreter convention")`.

- [ ] **Step 3: Replace the scratchpad path on line 89**

Replace `\`~/.hermes/reports/dexter-scratchpad/20260429-162217_b2d5c91bf629.jsonl\`` with `\`./financial-research/scratchpad/20260429-162217_b2d5c91bf629.jsonl\`` (relative-path example matching the new default).

- [ ] **Step 4: Verify**

```bash
grep -n "~/\.hermes" references/hsbc-hk-bank-research-test-20260429.md && echo "FAIL" || echo "OK: clean"
```

Expected: `OK: clean`.

- [ ] **Step 5: Commit**

```bash
git add references/hsbc-hk-bank-research-test-20260429.md
git commit -m "docs(reference): drop ~/.hermes paths from HSBC test record"
```

---

## Task 11: Update repo `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md:1-44` (entire file is short — full rewrite is cleanest)

- [ ] **Step 1: Replace the whole file**

Replace `CLAUDE.md` with:

```markdown
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
```

- [ ] **Step 2: Verify**

```bash
head -5 CLAUDE.md
grep -c "daisy-financial-research" CLAUDE.md
grep -c "<this-skill-dir>" CLAUDE.md
grep -c "~/\.hermes/reports/financial-research" CLAUDE.md
```

Expected, in order: the H1 line, ≥2, ≥1, 1 (the single mention is the back-compat example, which is correct).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(repo): update CLAUDE.md for multi-platform layout"
```

---

## Task 12: Write `README.md` (Chinese, default)

**Files:**
- Create: `README.md`

The repo currently has no README. Per workspace CLAUDE.md, Chinese is the default README; English goes in `README_EN.md`.

- [ ] **Step 1: Write the file**

Write `README.md`:

````markdown
# Daisy 金融研究 — 自动化股票/公司研究 Agent Skill

[English](README_EN.md) | [GitHub](https://github.com/Agents365-ai/daisy-financial-research)

## 这是什么

一个面向 AI Coding Agent 的金融研究技能 (skill)。给定股票/公司/行业话题，它会先制定研究计划，再用 Tushare 取结构化数据、用 Brave/Bailian MCP 做网络检索、用 Python 做计算与估值，最后产出带来源、可复核的 Markdown + HTML(+ PDF) 报告。

设计参照 `virattt/dexter` 的迭代式 agent 循环 (plan → gather → validate → answer)，但作为一个跨平台 skill 打包，无需独立 CLI。

**关键能力:**
- 计划先行 + JSONL scratchpad，记录每次工具调用、参数、结果、假设
- DCF 估值 + 敏感性矩阵 + 合理性校验
- 银行/金融板块估值替代框架 (RoTE / CET1 / NIM / P/B / 派息率)
- A 股 + 港股通预设筛选 (股息质量、价值、动量等)
- 三层报告输出 (md → html → 可选 pdf)，CSS 已内置中英文字体回退
- Brave MCP + Bailian WebSearch MCP 双通道检索

## 多平台支持

| 平台 | 状态 | 说明 |
|---|---|---|
| **Claude Code** | ✅ | 原生 SKILL.md 格式 |
| **Opencode** | ✅ | 自动读取 `~/.claude/skills/` |
| **OpenClaw / ClawHub** | ✅ | `metadata.openclaw` 命名空间，依赖检查 |
| **Hermes Agent** | ✅ | `metadata.hermes` 命名空间 |
| **OpenAI Codex** | ✅ | `agents/openai.yaml` sidecar |
| **SkillsMP** | ✅ | GitHub topic 已配置 |

## 前置依赖

```bash
# Python 3.9+
pip install tushare pandas requests
# 可选: PDF 输出
brew install pandoc
brew install --cask mactex   # 或 brew install --cask basictex (体积小)
```

环境变量:
```bash
export TUSHARE_TOKEN=xxxxxxxx   # 任何 Tushare 调用都需要
```

## 安装

| 平台 | 全局 | 项目级 |
|---|---|---|
| Claude Code | `git clone https://github.com/Agents365-ai/daisy-financial-research.git ~/.claude/skills/daisy-financial-research` | `git clone ... .claude/skills/daisy-financial-research` |
| Opencode | `git clone ... ~/.config/opencode/skills/daisy-financial-research` | `git clone ... .opencode/skills/daisy-financial-research` |
| OpenClaw | `clawhub install daisy-financial-research` 或 `git clone ... ~/.openclaw/skills/daisy-financial-research` | `git clone ... skills/daisy-financial-research` |
| Hermes | `git clone ... ~/.hermes/skills/research/daisy-financial-research` | 通过 `~/.hermes/config.yaml` 的 `external_dirs` |
| OpenAI Codex | `git clone ... ~/.agents/skills/daisy-financial-research` | `git clone ... .agents/skills/daisy-financial-research` |
| SkillsMP | `skills install daisy-financial-research` | — |

## 快速开始

```bash
# A 股股息质量 watchlist + Markdown 报告草稿
python <skill-dir>/scripts/screen_a_share.py --preset a_dividend_quality --top 50 --report

# 把 Markdown 草稿渲染成三层报告
python <skill-dir>/scripts/financial_report.py ./financial-research/reports/<TIMESTAMP>_a-share-a_dividend_quality-screen.md \
    --title "A股股息 watchlist" --slug a-div-quality --pdf
```

默认输出全部落到当前目录下的 `./financial-research/{reports,watchlists,scratchpad,universes}/` 里。

## 输出路径

| 脚本 | 默认子目录 |
|---|---|
| `dexter_scratchpad.py` | `./financial-research/scratchpad/` |
| `financial_report.py` | `./financial-research/reports/` |
| `screen_a_share.py` | `./financial-research/watchlists/` (+ `reports/` 如果 `--report`) |
| `screen_hk_connect.py` | `./financial-research/watchlists/` |
| `hk_connect_universe.py` | `./financial-research/universes/` |

任何脚本都接受 `--out-dir <root>` 来自定义根目录，子目录会自动追加。

**Hermes 用户**: 想保留旧的 `~/.hermes/reports/financial-research/<subdir>/` 布局，给每个脚本加 `--out-dir ~/.hermes/reports/financial-research` 即可。

## 自动更新

技能会在每次会话首次调用时检查 `<skill-dir>/.last_update`。超过 24 小时则静默 `git pull --ff-only`。失败 (离线/冲突/非 git checkout) 不会打断流程，也不会通知用户。

手动更新:
```bash
cd <skill-dir> && git pull
```

## 与无 skill 的对比

| 能力 | 原生 agent | 本 skill |
|---|---|---|
| 计划先行 + scratchpad | 否 | 是 (强制 JSONL 记录) |
| 数值校验 checklist | 否 | 是 (单位/币种/期间/口径) |
| 银行估值不用 DCF | 看运气 | 默认强制改用 RoTE/CET1/NIM/P/B |
| Tushare 路由 + 已知失败接口规避 | 否 | 是 (内置 gotchas) |
| 多预设股票筛选 | 否 | 是 (`a_dividend_quality` / `a_value` / 港股通) |
| 三层报告 (md+html+pdf) | 需手写 | 一行命令产出 |
| 港股通 universe 导出 | 否 | 是 (向后回填日期) |
| 软循环上限 + 重复查询检测 | 否 | 是 (避免工具调用失控) |

## 免责声明

本技能仅用于数据分析与研究记录，不构成投资建议。所有结论需结合最新公开信息独立判断。
````

- [ ] **Step 2: Verify Markdown renders without obvious issues**

```bash
wc -l README.md
head -3 README.md
```

Expected: ~110 lines; first three lines are `# Daisy 金融研究 ...`, blank, then the link line.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add Chinese README (default) with multi-platform install matrix"
```

---

## Task 13: Write `README_EN.md`

**Files:**
- Create: `README_EN.md`

- [ ] **Step 1: Write the file**

Write `README_EN.md`:

````markdown
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
````

- [ ] **Step 2: Verify**

```bash
wc -l README_EN.md
head -3 README_EN.md
```

Expected: ~110 lines; first three lines are the H1, blank, then the link line.

- [ ] **Step 3: Commit**

```bash
git add README_EN.md
git commit -m "docs: add English README"
```

---

## Task 14: Final integration check

**Files:** none modified — verification only.

- [ ] **Step 1: Run all scripts' `--help` and confirm `--out-dir` is everywhere**

```bash
for s in scripts/dexter_scratchpad.py scripts/financial_report.py scripts/hk_connect_universe.py scripts/screen_a_share.py scripts/screen_hk_connect.py; do
  echo "=== $s ==="
  if [ "$s" = "scripts/dexter_scratchpad.py" ]; then
    python3 "$s" init --help 2>&1 | grep -E "(--out-dir|usage)" | head -3
  else
    python3 "$s" --help 2>&1 | grep -E "(--out-dir|usage)" | head -3
  fi
done
```

Expected: every script's output contains a line with `--out-dir`. (`dexter_scratchpad.py` is special-cased because the flag lives on the `init` subcommand.)

- [ ] **Step 2: Confirm no `~/.hermes/...` paths remain in user-facing files**

```bash
grep -rn "~/\.hermes" SKILL.md README.md README_EN.md CLAUDE.md scripts/ 2>&1 | grep -v "back-compat\|legacy\|--out-dir ~/.hermes" || echo "OK: clean"
```

Expected: `OK: clean`. (Any matches MUST be in a back-compat-note context that explicitly mentions `--out-dir`. If a stray operational reference remains, fix it.)

- [ ] **Step 3: Confirm SKILL.md frontmatter loads cleanly under both YAML and the OpenClaw single-line constraint**

```bash
python3 -c "
import yaml, json
text = open('SKILL.md').read()
fm = text.split('---', 2)[1]
data = yaml.safe_load(fm)
md_field = data['metadata']
# OpenClaw parser only supports single-line values: confirm metadata is a string-encoded JSON OR a dict that fits on one line.
if isinstance(md_field, str):
    json.loads(md_field)  # must be valid JSON
elif isinstance(md_field, dict):
    one_line = json.dumps(md_field, separators=(',', ':'))
    assert len(one_line) < 1024, 'metadata too long for OpenClaw single-line frontmatter'
print('frontmatter OK')
"
```

Expected: `frontmatter OK`.

- [ ] **Step 4: Confirm the integration files exist**

```bash
ls -la agents/openai.yaml README.md README_EN.md .last_update
```

Expected: all four files listed, non-zero size.

- [ ] **Step 5: Final commit (none expected)**

```bash
git status
```

Expected: `nothing to commit, working tree clean`. (If anything is dirty, address it before claiming the plan complete.)

---

## Self-Review (done by plan author)

**Spec coverage:**
- Frontmatter rewrite → Task 8 ✓
- `<this-skill-dir>` placeholder → Task 9 ✓
- `--out-dir` flag on every script → Tasks 3-7 ✓
- `agents/openai.yaml` → Task 1 ✓
- `.last_update` auto-update step in SKILL.md → Task 9 step 4; seed file → Task 2 ✓
- README.md (CN) + README_EN.md → Tasks 12, 13 ✓
- CLAUDE.md update → Task 11 ✓
- Reference doc path scrub → Task 10 ✓
- Naming: skill name = `daisy-financial-research`, dir = `dexter-financial-research`, attribution to `virattt/dexter` preserved → Tasks 8, 9, 11, 12, 13 ✓
- Hermes back-compat via `--out-dir` → Tasks 9 step 3, 11, 12, 13 ✓
- Final integration verification → Task 14 ✓

**Placeholder scan:** No "TBD", no "implement later", no "see Task N" cross-references. Each script-modification task contains the full code block to write.

**Type / signature consistency:** `resolve_out_dir(arg_out_dir: str | None, ...) -> Path` is the signature in every script. Tasks 5 and 6 use the two-arg variant `(arg_out_dir, subdir)`; Tasks 3, 4, 7 use the one-arg variant with the subdir baked into a module constant. This is intentional (only `screen_a_share.py` writes to two subdirs) and the verification commands in each task call the right variant. The argparse flag is `--out-dir` (dest=`out_dir`) everywhere.
