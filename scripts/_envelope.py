"""Agent-native CLI envelope helpers shared by daisy-financial-research scripts.

Every script in this skill emits one of two stable shapes on stdout:

  success: {"ok": true,  "data": {...}, "meta": {...}}
  failure: {"ok": false, "error": {"code", "message", "retryable", "context"}, "meta": {...}}

Format auto-detection
  - When --format is "json", or stdout is not a TTY, scripts emit JSON.
  - When --format is "table" (TTY default), scripts call their table_render
    callback for a human-readable summary; JSON envelope is suppressed.
  - NO_COLOR / DAISY_FORCE_JSON env vars are honored by resolve_format.

Exit code map (documented in --help):
  0  success
  1  runtime error (upstream API failure, unexpected exception)
  2  auth error   (missing TUSHARE_TOKEN, expired token)
  3  validation   (bad --date format, unknown preset, missing arg)
  4  no_data      (lookback exhausted, empty result after filters)
  5  dependency   (pandoc/LaTeX missing, optional tool unavailable)

Schema response
  Each script defines a SCHEMA dict; emit_schema serializes it inside the
  same success envelope so agents can introspect parameters without parsing
  --help text.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any, Callable

SCHEMA_VERSION = "1.0.0"


class ExitCode:
    OK = 0
    RUNTIME = 1
    AUTH = 2
    VALIDATION = 3
    NO_DATA = 4
    DEPENDENCY = 5


CODE_FOR_EXIT = {
    ExitCode.RUNTIME: "runtime_error",
    ExitCode.AUTH: "auth_missing",
    ExitCode.VALIDATION: "validation_error",
    ExitCode.NO_DATA: "no_data",
    ExitCode.DEPENDENCY: "dependency_missing",
}


class Timer:
    def __init__(self) -> None:
        self._start = time.monotonic()

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)


def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex[:12]


def resolve_format(arg_format: str | None, stream=sys.stdout) -> str:
    """Pick output format: explicit flag > env override > TTY auto-detect."""
    if arg_format in ("json", "table"):
        return arg_format
    if os.environ.get("DAISY_FORCE_JSON") == "1":
        return "json"
    try:
        if not stream.isatty():
            return "json"
    except (AttributeError, ValueError):
        return "json"
    return "table"


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Attach --format / --schema / --dry-run to a subparser or root parser.

    Scripts that don't support --dry-run should remove it after calling this.
    """
    parser.add_argument(
        "--format",
        choices=["json", "table"],
        default=None,
        help="Output format. Default: json when stdout is not a TTY, else table.",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Print the script's parameter and output schema as JSON, then exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the request shape without executing upstream calls or writes.",
    )


def _meta(timer: Timer | None, request_id: str | None, extra: dict | None) -> dict:
    meta: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    meta["request_id"] = request_id or new_request_id()
    if timer is not None:
        meta["latency_ms"] = timer.elapsed_ms()
    if extra:
        meta.update(extra)
    return meta


def emit_success(
    data: Any,
    fmt: str,
    *,
    timer: Timer | None = None,
    request_id: str | None = None,
    meta_extra: dict | None = None,
    table_render: Callable[[], None] | None = None,
) -> int:
    """Emit a success envelope. Returns ExitCode.OK."""
    if fmt == "json":
        envelope = {
            "ok": True,
            "data": data,
            "meta": _meta(timer, request_id, meta_extra),
        }
        print(json.dumps(envelope, ensure_ascii=False))
    else:
        if table_render is not None:
            table_render()
    return ExitCode.OK


def emit_failure(
    exit_code: int,
    message: str,
    fmt: str,
    *,
    code: str | None = None,
    retryable: bool = False,
    context: dict | None = None,
    timer: Timer | None = None,
    request_id: str | None = None,
) -> int:
    """Emit an error envelope. Returns the given exit_code.

    JSON goes to stdout (so agents capture it from the same channel as success).
    Human prose goes to stderr.
    """
    err_code = code or CODE_FOR_EXIT.get(exit_code, "runtime_error")
    if fmt == "json":
        envelope = {
            "ok": False,
            "error": {
                "code": err_code,
                "message": message,
                "retryable": retryable,
                "context": context or {},
            },
            "meta": _meta(timer, request_id, None),
        }
        print(json.dumps(envelope, ensure_ascii=False))
    else:
        prefix = "ERROR" if not retryable else "RETRYABLE"
        print(f"{prefix} [{err_code}]: {message}", file=sys.stderr)
        if context:
            for k, v in context.items():
                print(f"  {k}: {v}", file=sys.stderr)
    return exit_code


def emit_schema(schema: dict, fmt: str, *, timer: Timer | None = None) -> int:
    """Emit a script's self-description schema as a success envelope."""
    return emit_success(schema, fmt, timer=timer)


def emit_progress(event: str, **fields: Any) -> None:
    """Emit one NDJSON progress line on stderr.

    Used by long-running commands so agents can detect liveness without
    blocking on the final stdout envelope.
    """
    obj: dict[str, Any] = {"event": event}
    obj.update(fields)
    print(json.dumps(obj, ensure_ascii=False), file=sys.stderr, flush=True)
