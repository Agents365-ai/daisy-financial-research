"""Agent-native CLI envelope contract tests.

Asserted invariants across every script under scripts/:
  - --help exits 0 and lists the documented exit codes
  - --schema returns a valid success envelope describing the script
  - --dry-run on mutating scripts returns a success envelope without side effects
  - validation errors return exit=3 with a structured error envelope
  - DAISY_FORCE_JSON=1 forces JSON regardless of TTY state

If any of these break, every agent integrated with the skill silently breaks.
"""
from __future__ import annotations

import json
import os

import pytest

from conftest import (
    ALL_SCRIPT_NAMES,
    SELF_CONTAINED_SCRIPT_NAMES,
    TUSHARE_SCRIPT_NAMES,
    assert_error_envelope,
    assert_success_envelope,
)


@pytest.mark.parametrize("script", ALL_SCRIPT_NAMES)
def test_help_exits_zero_and_documents_exit_codes(script, run_script):
    res = run_script(script, "--help")
    assert res.returncode == 0
    # Both stdout and stderr can hold help text depending on argparse version
    combined = res.stdout + res.stderr
    assert "Exit codes" in combined, f"--help missing exit-code epilog for {script}"
    assert "0 ok" in combined and "3 validation" in combined


@pytest.mark.parametrize("script", ALL_SCRIPT_NAMES)
def test_schema_returns_valid_envelope(script, run_script):
    res = run_script(script, "--schema")
    env = assert_success_envelope(res)
    data = env["data"]
    assert "name" in data, f"schema missing 'name' for {script}"
    assert "error_codes" in data, f"schema missing 'error_codes' for {script}"


@pytest.mark.parametrize("script", ALL_SCRIPT_NAMES)
def test_force_json_env_overrides_tty(script, run_script):
    """DAISY_FORCE_JSON=1 (set by conftest) should yield parseable JSON."""
    res = run_script(script, "--schema")
    json.loads(res.stdout)  # must not raise


# ----- dry-run paths (no network, no writes) -----

def test_scratchpad_init_dry_run(run_script, out_dir):
    res = run_script("dexter_scratchpad.py", "init", "test query",
                     "--dry-run", "--out-dir", str(out_dir))
    env = assert_success_envelope(res)
    assert env["data"]["dry_run"] is True
    assert "would_create" in env["data"]
    # Verify no file was actually written
    scratch = out_dir / "scratchpad"
    if scratch.exists():
        assert list(scratch.iterdir()) == []


def test_memory_log_record_dry_run(run_script, out_dir):
    res = run_script("dexter_memory_log.py", "record",
                     "--ticker", "600519.SH", "--rating", "Buy",
                     "--decision", "test thesis", "--dry-run",
                     "--out-dir", str(out_dir))
    env = assert_success_envelope(res)
    assert env["data"]["dry_run"] is True
    log_path = out_dir / "memory" / "decision-log.md"
    assert not log_path.exists(), "dry-run must not write the log"


def test_financial_report_dry_run(run_script, out_dir, sample_markdown):
    res = run_script("financial_report.py", str(sample_markdown),
                     "--dry-run", "--out-dir", str(out_dir))
    env = assert_success_envelope(res)
    assert env["data"]["dry_run"] is True
    assert "would_write" in env["data"]
    reports = out_dir / "reports"
    if reports.exists():
        assert list(reports.iterdir()) == []


def test_akshare_hk_valuation_dry_run_normalizes_ts_code(run_script):
    """Dry-run must short-circuit before akshare import and normalize the ts_code."""
    res = run_script("akshare_hk_valuation.py", "valuation",
                     "--ts-code", "00005.HK", "--dry-run")
    env = assert_success_envelope(res)
    assert env["data"]["dry_run"] is True
    assert env["data"]["ts_code"] == "00005", "expected .HK suffix to be stripped"


def test_akshare_hk_valuation_bad_ts_code_returns_validation(run_script):
    res = run_script("akshare_hk_valuation.py", "valuation",
                     "--ts-code", "BANANA", "--dry-run")
    assert_error_envelope(res, exit_code=3, code="validation_error")


@pytest.mark.parametrize("script", TUSHARE_SCRIPT_NAMES)
def test_tushare_scripts_dry_run_without_token(script, run_script, out_dir, monkeypatch):
    """Dry-run path must short-circuit before any auth check."""
    args = ["--dry-run", "--out-dir", str(out_dir)]
    if script == "screen_a_share.py":
        args = ["--preset", "a_value", *args]
    res = run_script(script, *args, env={"TUSHARE_TOKEN": ""})
    env = assert_success_envelope(res)
    assert env["data"]["dry_run"] is True


# ----- validation error envelopes -----

def test_bad_date_returns_validation_error(run_script, out_dir):
    res = run_script("hk_connect_universe.py", "--date", "NOTADATE",
                     "--dry-run", "--out-dir", str(out_dir))
    assert_error_envelope(res, exit_code=3, code="validation_error")


def test_memory_log_bad_date_returns_validation_error(run_script, out_dir):
    res = run_script("dexter_memory_log.py", "record",
                     "--ticker", "FOO", "--rating", "Buy",
                     "--decision", "x", "--date", "NOTADATE",
                     "--out-dir", str(out_dir))
    assert_error_envelope(res, exit_code=3, code="validation_error")


def test_memory_log_resolve_missing_returns_no_data(run_script, out_dir):
    """Resolve targeting a non-existent log file → no_data, exit=4."""
    res = run_script("dexter_memory_log.py", "resolve",
                     "--ticker", "NOPE", "--date", "20260101",
                     "--raw-return", "1", "--alpha-return", "1",
                     "--holding-days", "5", "--reflection", "x",
                     "--out-dir", str(out_dir))
    assert_error_envelope(res, exit_code=4, code="no_data")


# ----- meta block invariants -----

@pytest.mark.parametrize("script", ALL_SCRIPT_NAMES)
def test_meta_block_shape(script, run_script):
    res = run_script(script, "--schema")
    env = res.envelope
    meta = env["meta"]
    # schema_version is semver-like
    parts = meta["schema_version"].split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), \
        f"non-semver schema_version: {meta['schema_version']!r}"
    # request_id has the documented prefix and a non-trivial suffix
    rid = meta["request_id"]
    assert rid.startswith("req_") and len(rid) >= 12
