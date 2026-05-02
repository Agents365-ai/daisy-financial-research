"""End-to-end lifecycle tests for scripts/dexter_memory_log.py.

Covers:
  - record idempotency on (date, ticker)
  - resolve atomic rewrite (pending tag → resolved tag, REFLECTION appended)
  - list / context / stats correctness against a real on-disk log
  - on-disk file format (separator, tag-line shape, DECISION/REFLECTION sections)

The on-disk format is deliberately wire-compatible with
TradingAgents/tradingagents/agents/utils/memory.py, so these assertions
also defend that compatibility.
"""
from __future__ import annotations

from conftest import assert_error_envelope, assert_success_envelope


def _record(run_script, out_dir, **kw):
    args = ["dexter_memory_log.py", "record", "--out-dir", str(out_dir)]
    for k, v in kw.items():
        args += [f"--{k.replace('_', '-')}", str(v)]
    return run_script(*args)


def _resolve(run_script, out_dir, **kw):
    args = ["dexter_memory_log.py", "resolve", "--out-dir", str(out_dir)]
    for k, v in kw.items():
        args += [f"--{k.replace('_', '-')}", str(v)]
    return run_script(*args)


def _list(run_script, out_dir, **kw):
    args = ["dexter_memory_log.py", "list", "--out-dir", str(out_dir)]
    for k, v in kw.items():
        args += [f"--{k.replace('_', '-')}", str(v)]
    return run_script(*args)


def test_full_lifecycle(run_script, out_dir):
    # 1. record A
    env = assert_success_envelope(_record(
        run_script, out_dir,
        ticker="600519.SH", rating="Buy", date="20260415",
        decision="Mao Tai PE22 ROE30, dividend stable",
    ))
    assert env["data"]["entry_added"] is True
    assert env["data"]["tag"] == "[2026-04-15 | 600519.SH | Buy | pending]"

    # 2. record B
    env = assert_success_envelope(_record(
        run_script, out_dir,
        ticker="00005.HK", rating="Overweight", date="20260420",
        decision="HSBC dividend 7%, CET1 strong",
    ))
    assert env["data"]["entry_added"] is True

    # 3. record A again — must skip
    env = assert_success_envelope(_record(
        run_script, out_dir,
        ticker="600519.SH", rating="Buy", date="20260415",
        decision="duplicate",
    ))
    assert env["data"]["entry_added"] is False
    assert env["data"]["skipped_reason"] == "duplicate_pending"

    # 4. list pending → 2 entries
    env = assert_success_envelope(_list(run_script, out_dir, status="pending"))
    assert env["data"]["count"] == 2
    tickers = {e["ticker"] for e in env["data"]["entries"]}
    assert tickers == {"600519.SH", "00005.HK"}

    # 5. resolve A
    env = assert_success_envelope(_resolve(
        run_script, out_dir,
        ticker="600519.SH", date="20260415",
        raw_return="4.8", alpha_return="1.2", holding_days="17",
        reflection="Held 17d, raw +4.8% vs CSI300 +3.6%, alpha +1.2%.",
    ))
    assert env["data"]["updated"] is True
    assert env["data"]["new_tag"] == "[2026-04-15 | 600519.SH | Buy | +4.8% | +1.2% | 17d]"

    # 6. list resolved → 1 entry
    env = assert_success_envelope(_list(run_script, out_dir, status="resolved"))
    assert env["data"]["count"] == 1
    assert env["data"]["entries"][0]["pending"] is False
    assert env["data"]["entries"][0]["raw"] == "+4.8%"
    assert env["data"]["entries"][0]["alpha"] == "+1.2%"

    # 7. context for 600519.SH
    env = assert_success_envelope(run_script(
        "dexter_memory_log.py", "context",
        "--ticker", "600519.SH", "--out-dir", str(out_dir),
    ))
    block = env["data"]["context"]
    assert "Past analyses of 600519.SH" in block
    assert "DECISION:" in block
    assert "REFLECTION:" in block
    assert env["data"]["n_same"] == 1

    # 8. stats
    env = assert_success_envelope(run_script(
        "dexter_memory_log.py", "stats", "--out-dir", str(out_dir),
    ))
    data = env["data"]
    assert data["total"] == 2 and data["pending"] == 1 and data["resolved"] == 1
    assert data["win_rate"] == 1.0
    assert data["alpha_win_rate"] == 1.0
    assert data["mean_raw_return_pct"] == 4.8
    assert data["mean_alpha_return_pct"] == 1.2
    assert data["by_rating"]["Buy"]["count"] == 1


def test_resolve_unknown_entry_returns_no_data(run_script, out_dir):
    # Seed one entry so the log file exists
    _record(run_script, out_dir, ticker="X.SH", rating="Hold",
            date="20260101", decision="seed")
    res = _resolve(
        run_script, out_dir,
        ticker="NOPE", date="20260101",
        raw_return="1", alpha_return="1", holding_days="5",
        reflection="x",
    )
    assert_error_envelope(res, exit_code=4, code="no_data")


def test_on_disk_format_is_wire_compatible(run_script, out_dir):
    """The file format must remain compatible with TradingAgents memory.py."""
    _record(run_script, out_dir, ticker="600519.SH", rating="Buy",
            date="20260415", decision="thesis")
    _resolve(
        run_script, out_dir,
        ticker="600519.SH", date="20260415",
        raw_return="2.5", alpha_return="0.5", holding_days="10",
        reflection="lesson",
    )

    log = (out_dir / "memory" / "decision-log.md").read_text(encoding="utf-8")
    assert "<!-- ENTRY_END -->" in log, "missing entry separator"
    assert "[2026-04-15 | 600519.SH | Buy | +2.5% | +0.5% | 10d]" in log
    assert "DECISION:" in log
    assert "REFLECTION:" in log
    # Tag line is exactly one per entry, on its own line
    tag_lines = [l for l in log.splitlines() if l.startswith("[2026-04-15")]
    assert len(tag_lines) == 1


def test_list_filter_by_ticker(run_script, out_dir):
    _record(run_script, out_dir, ticker="A.SH", rating="Buy",
            date="20260101", decision="a")
    _record(run_script, out_dir, ticker="B.SH", rating="Sell",
            date="20260102", decision="b")

    env = assert_success_envelope(_list(run_script, out_dir, ticker="A.SH"))
    assert env["data"]["count"] == 1
    assert env["data"]["entries"][0]["ticker"] == "A.SH"


def test_list_filter_since(run_script, out_dir):
    _record(run_script, out_dir, ticker="A.SH", rating="Buy",
            date="20260101", decision="a")
    _record(run_script, out_dir, ticker="B.SH", rating="Sell",
            date="20260301", decision="b")

    env = assert_success_envelope(_list(run_script, out_dir, since="20260201"))
    assert env["data"]["count"] == 1
    assert env["data"]["entries"][0]["ticker"] == "B.SH"


# ----- compute-returns / auto-resolve dry-run paths (no network) -----

def test_compute_returns_dry_run_a_share(run_script, out_dir):
    res = run_script("dexter_memory_log.py", "compute-returns",
                     "--ticker", "600519.SH", "--date", "20260415",
                     "--dry-run", "--out-dir", str(out_dir))
    env = assert_success_envelope(res)
    assert env["data"]["dry_run"] is True
    assert env["data"]["market"] == "a_share"
    assert env["data"]["benchmark_ts_code"] == "000300.SH"


def test_compute_returns_dry_run_hk(run_script, out_dir):
    res = run_script("dexter_memory_log.py", "compute-returns",
                     "--ticker", "00005.HK", "--date", "20260415",
                     "--dry-run", "--out-dir", str(out_dir))
    env = assert_success_envelope(res)
    assert env["data"]["market"] == "hk"
    assert env["data"]["benchmark_ts_code"] == "HSI.HK"


def test_auto_resolve_dry_run(run_script, out_dir):
    res = run_script("dexter_memory_log.py", "auto-resolve",
                     "--ticker", "600519.SH", "--date", "20260415",
                     "--reflection", "x", "--dry-run", "--out-dir", str(out_dir))
    env = assert_success_envelope(res)
    assert env["data"]["dry_run"] is True
    assert env["data"]["would_resolve"] is True


def test_compute_returns_as_of_must_be_after_decision(run_script, out_dir):
    res = run_script("dexter_memory_log.py", "compute-returns",
                     "--ticker", "600519.SH", "--date", "20260502",
                     "--as-of", "20260415", "--dry-run",
                     "--out-dir", str(out_dir))
    assert_error_envelope(res, exit_code=3, code="validation_error")


def test_compute_returns_bad_date(run_script, out_dir):
    res = run_script("dexter_memory_log.py", "compute-returns",
                     "--ticker", "600519.SH", "--date", "BANANA",
                     "--dry-run", "--out-dir", str(out_dir))
    assert_error_envelope(res, exit_code=3, code="validation_error")
