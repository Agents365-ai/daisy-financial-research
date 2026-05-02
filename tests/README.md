# Tests

Smoke + contract tests for the daisy-financial-research scripts.

## Running

```bash
python3 -m pytest tests/ -q
```

~6 seconds, 38 tests, no Tushare token required, no network.

## What's covered

**`test_envelope_contract.py`** — for every script under `scripts/`:
- `--help` exits 0 and documents the 0/1/2/3/4/5 exit code map
- `--schema` returns a valid `{ok, data, meta}` envelope with `schema_version`, `request_id` (`req_…`), `latency_ms`
- `--dry-run` runs without network or writes (verified for all four mutating scripts; Tushare-backed scripts run with `TUSHARE_TOKEN=""` to confirm dry-run short-circuits before auth)
- Validation errors return `exit=3` with a structured `{ok: false, error{code, message, retryable, context}}` envelope
- `DAISY_FORCE_JSON=1` forces JSON regardless of TTY (set by the conftest fixture for every subprocess invocation)

**`test_memory_log.py`** — full lifecycle for `dexter_memory_log.py`:
- `record` is idempotent on `(date, ticker)` — duplicates skip with `skipped_reason: "duplicate_pending"`
- `resolve` rewrites the pending tag to include realized returns and appends `REFLECTION:`
- `list` / `context` / `stats` against a real on-disk log
- On-disk file format remains wire-compatible with `TradingAgents/tradingagents/agents/utils/memory.py` (separator `<!-- ENTRY_END -->`, tag-line shape, `DECISION:` / `REFLECTION:` sections)

## Adding new tests

Use the `run_script` and `out_dir` fixtures in `conftest.py`:

```python
def test_my_thing(run_script, out_dir):
    res = run_script("screen_a_share.py", "--preset", "a_value",
                     "--dry-run", "--out-dir", str(out_dir))
    env = assert_success_envelope(res)
    assert env["data"]["preset"] == "a_value"
```
