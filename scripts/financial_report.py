#!/usr/bin/env python3
"""Generate the Dexter financial research three-layer report stack.

Inputs:
  - a Markdown source report
Outputs:
  - canonical Markdown copied under ~/.hermes/reports/financial-research/
  - styled HTML rendered from the Markdown
  - optional PDF rendered from the Markdown via pandoc + xelatex when available

The script is intentionally dependency-light: it prefers pandoc if installed and
falls back to a small Markdown subset renderer for HTML. PDF requires pandoc and
an installed LaTeX engine.
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

BASE_DIR = Path(os.path.expanduser("~/.hermes/reports/financial-research"))
DEFAULT_CJK_FONT = "Hiragino Sans GB"

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
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", text)
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
        # pipe table: header line + separator line
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
        # simple link conversion after escaping
        escaped = html.escape(line)
        escaped = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', escaped)
        out.append(f"<p>{escaped}</p>")
        i += 1
    close_ul()
    return "\n".join(out)


def render_html(md_path: Path, html_path: Path, title: str) -> tuple[bool, str]:
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
            # Pandoc writes a full document. Inject our CSS and wrap body content lightly.
            raw = raw.replace('<link rel="stylesheet" href="__INLINE_STYLE_PLACEHOLDER__" />', f"<style>\n{CSS}\n</style>")
            raw = raw.replace('<link rel="stylesheet" href="__INLINE_STYLE_PLACEHOLDER__">', f"<style>\n{CSS}\n</style>")
            raw = re.sub(r"<body>\s*", "<body>\n<main>\n", raw, count=1)
            raw = re.sub(r"\s*</body>", "\n</main>\n</body>", raw, count=1)
            html_path.write_text(raw, encoding="utf-8")
            return True, "html rendered with pandoc"
        return False, f"pandoc html failed: {output.strip()}"

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
    return True, "html rendered with fallback renderer"


def render_pdf(md_path: Path, pdf_path: Path, title: str, cjk_font: str) -> tuple[bool, str]:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        return False, "pandoc not found; skipped PDF"
    engine = shutil.which("xelatex") or shutil.which("lualatex") or shutil.which("pdflatex")
    if not engine:
        return False, "LaTeX engine not found; skipped PDF"
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
    if code == 0 and pdf_path.exists():
        return True, f"pdf rendered with pandoc + {Path(engine).name}"
    return False, "pdf generation failed: " + output[-1200:]


def copy_markdown(src: Path, dest: Path, title: str) -> None:
    text = src.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        today = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        fm = f"---\ntitle: {title}\ncreated: {today}\nreport_type: financial_research\n---\n\n"
        text = fm + text
    dest.write_text(text, encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Markdown + HTML + optional PDF financial research report")
    parser.add_argument("markdown", help="Path to source Markdown report")
    parser.add_argument("--title", help="Report title; defaults to first H1 or file stem")
    parser.add_argument("--slug", help="Output filename slug; defaults to title slug")
    parser.add_argument("--out-dir", default=str(BASE_DIR), help="Output directory")
    parser.add_argument("--pdf", action="store_true", help="Attempt PDF export via pandoc + LaTeX")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF export even if --pdf is not used")
    parser.add_argument("--cjk-font", default=DEFAULT_CJK_FONT, help="CJK font for PDF via xelatex")
    args = parser.parse_args(argv)

    src = Path(args.markdown).expanduser().resolve()
    if not src.exists():
        print(f"ERROR: markdown file not found: {src}", file=sys.stderr)
        return 2
    md_text = src.read_text(encoding="utf-8")
    title = args.title or first_heading(md_text) or src.stem
    slug = slugify(args.slug or title)
    timestamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    base = out_dir / f"{timestamp}_{slug}"
    md_out = base.with_suffix(".md")
    html_out = base.with_suffix(".html")
    pdf_out = base.with_suffix(".pdf")

    copy_markdown(src, md_out, title)
    ok_html, msg_html = render_html(md_out, html_out, title)
    if not ok_html:
        print(f"ERROR: {msg_html}", file=sys.stderr)
        return 1

    pdf_msg = "pdf skipped"
    pdf_ok = False
    if args.pdf and not args.no_pdf:
        pdf_ok, pdf_msg = render_pdf(md_out, pdf_out, title, args.cjk_font)

    print(f"markdown: {md_out}")
    print(f"html: {html_out} ({msg_html})")
    if args.pdf:
        if pdf_ok:
            print(f"pdf: {pdf_out} ({pdf_msg})")
        else:
            print(f"pdf: skipped/failed ({pdf_msg})")
    else:
        print("pdf: skipped (pass --pdf for formal deliverable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
