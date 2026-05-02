#!/usr/bin/env python3
"""Render the daisy-financial-research three-layer report stack (Markdown + HTML + optional PDF).

Inputs
  - a Markdown source report

Outputs (default <cwd>/financial-research/reports/)
  - canonical Markdown copy
  - styled HTML (pandoc when available, internal fallback otherwise)
  - optional PDF via pandoc + xelatex/lualatex/pdflatex

Agent-native conventions
  --format json|table   stdout shape; auto-JSON when stdout is not a TTY
  --schema              emit parameter schema, then exit
  --dry-run             preview which files would be written, without rendering
  Exit codes            0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Iterable

from _envelope import (
    ExitCode,
    Timer,
    add_common_args,
    emit_failure,
    emit_progress,
    emit_schema,
    emit_success,
    new_request_id,
    resolve_format,
)

DEFAULT_ROOT_NAME = "financial-research"
SUBDIR = "reports"
DEFAULT_CJK_FONT = "Hiragino Sans GB"

SCHEMA = {
    "name": "financial_report",
    "description": "Render Markdown source into Markdown + HTML + optional PDF report stack",
    "params": {
        "markdown": {"type": "string", "required": True, "description": "Path to source Markdown file"},
        "title": {"type": "string", "default": "first H1 or filename stem"},
        "slug": {"type": "string", "default": "slugified title"},
        "out_dir": {"type": "string", "default": "./financial-research", "description": "Root; reports/ subdir auto-appended"},
        "pdf": {"type": "bool", "default": False, "description": "Attempt PDF via pandoc + LaTeX"},
        "no_pdf": {"type": "bool", "default": False, "description": "Skip PDF even if --pdf is passed"},
        "cjk_font": {"type": "string", "default": DEFAULT_CJK_FONT},
    },
    "returns": {
        "markdown": "absolute path to copied Markdown",
        "html": "absolute path to rendered HTML",
        "pdf": "absolute path to rendered PDF, or null if skipped/failed",
        "renderer": "pandoc | fallback",
    },
    "error_codes": ["validation_error", "no_data", "dependency_missing", "runtime_error"],
}


def resolve_out_dir(arg_out_dir: str | None) -> Path:
    """Return <root>/reports, where <root> defaults to cwd/financial-research."""
    if arg_out_dir:
        root = Path(arg_out_dir).expanduser()
    else:
        root = Path.cwd() / DEFAULT_ROOT_NAME
    out = root / SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    return out


CSS = r"""
:root {
  --bg: #f6f7fb;
  --paper: #ffffff;
  --ink: #172033;
  --muted: #5d667a;
  --line: #dfe4ee;
  --accent: #1f6feb;
  --good: #137333;
  --warn: #b26a00;
  --bad: #b3261e;
  --code: #f1f4f9;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", "Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", Arial, sans-serif;
}
main {
  max-width: 1080px;
  margin: 32px auto;
  background: var(--paper);
  padding: 44px 52px;
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: 0 12px 36px rgba(23,32,51,.08);
}
h1 { font-size: 2.1rem; line-height: 1.2; margin: 0 0 1rem; }
h2 { font-size: 1.45rem; margin-top: 2.2rem; padding-top: .9rem; border-top: 1px solid var(--line); }
h3 { font-size: 1.15rem; margin-top: 1.5rem; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
table { width: 100%; border-collapse: collapse; margin: 1rem 0 1.4rem; font-size: .95rem; }
th, td { border: 1px solid var(--line); padding: .55rem .7rem; vertical-align: top; }
th { background: #eef3fb; text-align: left; }
tr:nth-child(even) td { background: #fafbfe; }
blockquote { margin: 1rem 0; padding: .7rem 1rem; border-left: 4px solid var(--accent); background: #f2f7ff; color: var(--muted); }
code { background: var(--code); padding: .12rem .28rem; border-radius: 4px; }
pre { background: #101828; color: #eef4ff; padding: 1rem; border-radius: 10px; overflow: auto; }
.badge { display: inline-block; padding: .15rem .5rem; border-radius: 999px; font-size: .82rem; font-weight: 600; }
.good { color: var(--good); }
.warn { color: var(--warn); }
.bad { color: var(--bad); }
.meta { color: var(--muted); font-size: .92rem; margin-bottom: 1.4rem; }
.disclaimer { margin-top: 2rem; padding: .9rem 1rem; background: #fff7e6; border: 1px solid #ffe0a3; border-radius: 10px; color: #634200; }
@media print {
  body { background: #fff; }
  main { box-shadow: none; border: none; margin: 0; padding: 20px; max-width: none; }
  a { color: inherit; text-decoration: underline; }
}
"""


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w一-鿿.-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-._")
    return text or "financial-report"


def first_heading(md: str) -> str | None:
    for line in md.splitlines():
        m = re.match(r"^#\s+(.+?)\s*$", line)
        if m:
            return m.group(1).strip()
    return None


def strip_yaml_frontmatter(md: str) -> str:
    if md.startswith("---\n"):
        end = md.find("\n---\n", 4)
        if end != -1:
            return md[end + 5 :].lstrip()
    return md


def run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.returncode, proc.stdout


def fallback_markdown_to_html(md: str) -> str:
    """Small fallback renderer for headings, paragraphs, lists, code fences, and pipe tables."""
    lines = strip_yaml_frontmatter(md).splitlines()
    out: list[str] = []
    in_ul = False
    in_code = False
    code_buf: list[str] = []

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            if not in_code:
                close_ul(); in_code = True; code_buf = []
            else:
                out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
                in_code = False
            i += 1
            continue
        if in_code:
            code_buf.append(line); i += 1; continue
        if not line.strip():
            close_ul(); i += 1; continue
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}:?", lines[i + 1]):
            close_ul()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            header = rows[0]
            body = rows[2:]
            out.append("<table><thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in header) + "</tr></thead><tbody>")
            for row in body:
                out.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in row) + "</tr>")
            out.append("</tbody></table>")
            continue
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            close_ul(); level = len(m.group(1)); out.append(f"<h{level}>{html.escape(m.group(2))}</h{level}>"); i += 1; continue
        m = re.match(r"^[-*]\s+(.+)$", line)
        if m:
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append(f"<li>{html.escape(m.group(1))}</li>"); i += 1; continue
        close_ul()
        escaped = html.escape(line)
        escaped = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', escaped)
        out.append(f"<p>{escaped}</p>")
        i += 1
    close_ul()
    return "\n".join(out)


def render_html(md_path: Path, html_path: Path, title: str) -> tuple[bool, str, str]:
    pandoc = shutil.which("pandoc")
    if pandoc:
        body_path = html_path.with_suffix(".body.html")
        code, output = run([
            pandoc, str(md_path),
            "--from", "markdown+pipe_tables+yaml_metadata_block",
            "--to", "html5",
            "--standalone",
            "--metadata", f"title={title}",
            "--css", "__INLINE_STYLE_PLACEHOLDER__",
            "-o", str(body_path),
        ])
        if code == 0 and body_path.exists():
            raw = body_path.read_text(encoding="utf-8")
            body_path.unlink(missing_ok=True)
            raw = raw.replace('<link rel="stylesheet" href="__INLINE_STYLE_PLACEHOLDER__" />', f"<style>\n{CSS}\n</style>")
            raw = raw.replace('<link rel="stylesheet" href="__INLINE_STYLE_PLACEHOLDER__">', f"<style>\n{CSS}\n</style>")
            raw = re.sub(r"<body>\s*", "<body>\n<main>\n", raw, count=1)
            raw = re.sub(r"\s*</body>", "\n</main>\n</body>", raw, count=1)
            html_path.write_text(raw, encoding="utf-8")
            return True, "html rendered with pandoc", "pandoc"
        return False, f"pandoc html failed: {output.strip()}", "pandoc"

    md = md_path.read_text(encoding="utf-8")
    body = fallback_markdown_to_html(md)
    doc = f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body><main>
{body}
</main></body></html>
"""
    html_path.write_text(doc, encoding="utf-8")
    return True, "html rendered with fallback renderer", "fallback"


def render_pdf(md_path: Path, pdf_path: Path, title: str, cjk_font: str) -> tuple[bool, str, str | None]:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        return False, "pandoc not found; skipped PDF", None
    engine = shutil.which("xelatex") or shutil.which("lualatex") or shutil.which("pdflatex")
    if not engine:
        return False, "LaTeX engine not found; skipped PDF", None
    cmd = [
        pandoc, str(md_path),
        "--from", "markdown+pipe_tables+yaml_metadata_block",
        "--pdf-engine", Path(engine).name,
        "-V", f"mainfont={cjk_font}",
        "-V", f"CJKmainfont={cjk_font}",
        "-V", "geometry:margin=0.8in",
        "-V", "colorlinks=true",
        "-V", "linkcolor=blue",
        "--metadata", f"title={title}",
        "-o", str(pdf_path),
    ]
    code, output = run(cmd)
    engine_name = Path(engine).name
    if code == 0 and pdf_path.exists():
        return True, f"pdf rendered with pandoc + {engine_name}", engine_name
    return False, "pdf generation failed: " + output[-1200:], engine_name


def copy_markdown(src: Path, dest: Path, title: str) -> None:
    text = src.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        today = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        fm = f"---\ntitle: {title}\ncreated: {today}\nreport_type: financial_research\n---\n\n"
        text = fm + text
    dest.write_text(text, encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render Markdown + HTML + optional PDF financial research report",
        epilog="Exit codes: 0 ok · 1 runtime · 2 auth · 3 validation · 4 no_data · 5 dependency",
    )
    parser.add_argument("markdown", nargs="?", help="Path to source Markdown report")
    parser.add_argument("--title", help="Report title; defaults to first H1 or file stem")
    parser.add_argument("--slug", help="Output filename slug; defaults to title slug")
    parser.add_argument("--out-dir", dest="out_dir", default=None,
                        help="Output root; default <cwd>/financial-research/ (reports/ subdir auto-appended)")
    parser.add_argument("--pdf", action="store_true", help="Attempt PDF export via pandoc + LaTeX")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF export even if --pdf is not used")
    parser.add_argument("--cjk-font", default=DEFAULT_CJK_FONT, help="CJK font for PDF via xelatex")
    add_common_args(parser)
    args = parser.parse_args(argv)

    fmt = resolve_format(args.format)
    timer = Timer()
    request_id = new_request_id()

    if args.schema:
        return emit_schema(SCHEMA, fmt, timer=timer)

    if not args.markdown:
        return emit_failure(
            ExitCode.VALIDATION,
            "missing required argument: markdown",
            fmt,
            code="validation_error",
            retryable=False,
            context={"required": "markdown"},
            timer=timer,
        )

    src = Path(args.markdown).expanduser().resolve()
    if not src.exists():
        return emit_failure(
            ExitCode.NO_DATA,
            f"markdown file not found: {src}",
            fmt,
            code="no_data",
            retryable=False,
            context={"path": str(src)},
            timer=timer,
        )

    md_text = src.read_text(encoding="utf-8")
    title = args.title or first_heading(md_text) or src.stem
    slug = slugify(args.slug or title)
    timestamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    out_dir = resolve_out_dir(args.out_dir).resolve()

    base = out_dir / f"{timestamp}_{slug}"
    md_out = base.with_suffix(".md")
    html_out = base.with_suffix(".html")
    pdf_out = base.with_suffix(".pdf")
    will_render_pdf = bool(args.pdf and not args.no_pdf)

    if args.dry_run:
        return emit_success(
            {
                "dry_run": True,
                "would_write": {
                    "markdown": str(md_out),
                    "html": str(html_out),
                    "pdf": str(pdf_out) if will_render_pdf else None,
                },
                "title": title,
                "slug": slug,
                "pandoc_available": shutil.which("pandoc") is not None,
                "latex_available": any(shutil.which(e) for e in ("xelatex", "lualatex", "pdflatex")),
            },
            fmt, timer=timer,
            table_render=lambda: (
                print(f"would_markdown: {md_out}"),
                print(f"would_html: {html_out}"),
                print(f"would_pdf: {pdf_out if will_render_pdf else 'skipped'}"),
            ),
        )

    if fmt == "json":
        emit_progress("start", command="financial_report.run", request_id=request_id, src=str(src))

    copy_markdown(src, md_out, title)
    if fmt == "json":
        emit_progress("progress", phase="markdown", path=str(md_out))

    ok_html, msg_html, html_renderer = render_html(md_out, html_out, title)
    if not ok_html:
        return emit_failure(
            ExitCode.RUNTIME,
            msg_html,
            fmt,
            code="runtime_error",
            retryable=True,
            context={"phase": "html", "renderer": html_renderer},
            timer=timer,
        )
    if fmt == "json":
        emit_progress("progress", phase="html", path=str(html_out), renderer=html_renderer)

    pdf_ok = False
    pdf_msg = "pdf skipped (pass --pdf for formal deliverable)"
    pdf_engine: str | None = None
    if will_render_pdf:
        pdf_ok, pdf_msg, pdf_engine = render_pdf(md_out, pdf_out, title, args.cjk_font)
        if fmt == "json":
            emit_progress("progress", phase="pdf", ok=pdf_ok, path=str(pdf_out) if pdf_ok else None, engine=pdf_engine)

    data = {
        "markdown": str(md_out),
        "html": str(html_out),
        "html_renderer": html_renderer,
        "pdf": str(pdf_out) if pdf_ok else None,
        "pdf_engine": pdf_engine,
        "pdf_skipped_reason": None if pdf_ok or not will_render_pdf else pdf_msg,
        "title": title,
        "slug": slug,
    }

    def table() -> None:
        print(f"markdown: {md_out}")
        print(f"html: {html_out} ({msg_html})")
        if will_render_pdf:
            if pdf_ok:
                print(f"pdf: {pdf_out} ({pdf_msg})")
            else:
                print(f"pdf: skipped/failed ({pdf_msg})")
        else:
            print("pdf: skipped (pass --pdf for formal deliverable)")

    if will_render_pdf and not pdf_ok:
        # Soft warning: HTML succeeded, PDF failed. Treat as success but include the reason.
        # Agents reading data.pdf == null can detect this; humans see the table.
        pass

    if fmt == "json":
        emit_progress("complete", request_id=request_id)

    return emit_success(data, fmt, timer=timer, request_id=request_id, table_render=table)


if __name__ == "__main__":
    raise SystemExit(main())
