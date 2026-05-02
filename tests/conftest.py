"""Shared fixtures for daisy-financial-research tests.

Tests run each script as a subprocess (the way an agent would) so they
exercise the real CLI envelope contract, not internal Python.

Constraints
  - No Tushare token required
  - No network calls — only --help / --schema / --dry-run paths and the
    fully self-contained dexter_scratchpad / dexter_memory_log scripts
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Scripts under test. Tuple of (filename, kind):
#   "tushare-mutating": needs tushare to actually run, but --dry-run is safe
#   "self-contained":   no upstream deps; can run end-to-end in tests
SCRIPTS = [
    ("dexter_scratchpad.py", "self-contained"),
    ("dexter_memory_log.py", "self-contained"),
    ("financial_report.py", "self-contained"),
    ("hk_connect_universe.py", "tushare-mutating"),
    ("screen_a_share.py", "tushare-mutating"),
    ("screen_hk_connect.py", "tushare-mutating"),
    # akshare is lazy-imported inside the script body, so --help / --schema /
    # --dry-run paths don't require akshare in the test environment.
    ("akshare_hk_valuation.py", "self-contained"),
]

ALL_SCRIPT_NAMES = [name for name, _ in SCRIPTS]
TUSHARE_SCRIPT_NAMES = [name for name, kind in SCRIPTS if kind == "tushare-mutating"]
SELF_CONTAINED_SCRIPT_NAMES = [name for name, kind in SCRIPTS if kind == "self-contained"]


@dataclass
class CLIResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def envelope(self) -> dict:
        """Parse stdout as a JSON envelope. Raises if not valid JSON."""
        return json.loads(self.stdout)


def _run_script(name: str, *args: str, env: dict | None = None,
                cwd: Path | None = None, timeout: int = 30) -> CLIResult:
    """Invoke a script as the agent would: capture stdout/stderr, return rc."""
    full_env = os.environ.copy()
    # Force JSON output regardless of whether the test runner is a TTY.
    full_env["DAISY_FORCE_JSON"] = "1"
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / name), *args],
        capture_output=True, text=True, env=full_env, cwd=cwd, timeout=timeout,
    )
    return CLIResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


@pytest.fixture
def run_script():
    """Fixture: callable that invokes a script and returns CLIResult."""
    return _run_script


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    """Isolated --out-dir for a test."""
    d = tmp_path / "financial-research"
    d.mkdir()
    return d


@pytest.fixture
def sample_markdown(tmp_path: Path) -> Path:
    """A minimal Markdown file for financial_report.py tests."""
    p = tmp_path / "sample.md"
    p.write_text("# Sample Report\n\nBody.\n", encoding="utf-8")
    return p


def assert_success_envelope(result: CLIResult) -> dict:
    """Validate {ok: true, data, meta} shape and return the envelope."""
    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    env = result.envelope
    assert env["ok"] is True, f"ok != true: {env}"
    assert "data" in env
    meta = env.get("meta", {})
    assert meta.get("schema_version"), "meta.schema_version missing"
    assert meta.get("request_id", "").startswith("req_"), f"bad request_id: {meta.get('request_id')!r}"
    assert isinstance(meta.get("latency_ms"), int), "meta.latency_ms missing or not int"
    return env


def assert_error_envelope(result: CLIResult, *, exit_code: int, code: str) -> dict:
    """Validate {ok: false, error{code, message, retryable, context}, meta}."""
    assert result.returncode == exit_code, (
        f"expected exit {exit_code}, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    env = result.envelope
    assert env["ok"] is False
    err = env["error"]
    assert err["code"] == code, f"expected code={code}, got {err['code']}"
    assert "message" in err
    assert "retryable" in err
    assert "context" in err
    return env
