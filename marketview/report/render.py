"""블록 목록 → Markdown / HTML. 이미지는 Markdown 에선 상대경로(assets/…), HTML(PDF 용)에선 file:// 절대경로."""

from __future__ import annotations

import html
import re
from pathlib import Path

from ..config import palette
from ..config.settings import REPO_ROOT

Block = tuple


def _md_link(text: str, url: str) -> str:
    """제목의 [ ] 와 주소의 ( ) 공백이 링크 문법을 깨지 않게 한다."""
    text = text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    url = url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
    return f"[{text}]({url})"


def _cell_md(c) -> str:
    return _md_link(c[0], c[1]) if isinstance(c, tuple) else str(c)


def to_markdown(blocks: list[Block], img_prefix: str = "assets/") -> str:
    out: list[str] = []
    for b in blocks:
        kind = b[0]
        if kind in ("h1", "h2", "h3"):
            out.append("#" * int(kind[1]) + " " + b[1])
        elif kind == "p":
            out.append(b[1])
        elif kind == "note":
            out.append(f"*{b[1]}*")
        elif kind == "quote":
            out.append("\n".join("> " + line for line in b[1].splitlines()))
        elif kind == "img":
            out.append(f"![{b[2]}]({img_prefix}{b[1]})")
        elif kind == "list":
            out.append("\n".join("- " + _cell_md(i) for i in b[1]))
        elif kind == "table":
            _, headers, rows, aligns = b
            sep = {"l": ":--", "r": "--:", "c": ":-:"}
            out.append("\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join(sep[a] for a in aligns) + " |",
                                  *("| " + " | ".join(_cell_md(c) for c in r) + " |" for r in rows)]))
    return "\n\n".join(out) + "\n"


def _inline(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(text))


def _cell_html(c) -> str:
    if isinstance(c, tuple):
        return f'<a href="{html.escape(c[1], quote=True)}">{html.escape(c[0])}</a>'
    return html.escape(str(c))


CSS = f"""
@font-face {{ font-family: "PretendardLocal"; src: url("{(REPO_ROOT / 'marketview/static/fonts/PretendardVariable.woff2').as_uri()}") format("woff2-variations"); font-weight: 45 920; }}
@page {{ size: A4; margin: 14mm 12mm; }}
body {{ font: 9.5pt/1.5 "PretendardLocal", "Malgun Gothic", sans-serif; color: {palette.TEXT}; background: #fff; font-variant-numeric: tabular-nums; }}
h1 {{ font-size: 17pt; margin: 0 0 4px; }}
h2 {{ font-size: 12.5pt; margin: 18px 0 6px; padding-bottom: 3px; border-bottom: 1.5px solid {palette.ACCENT}; color: {palette.ACCENT}; break-after: avoid; }}
h3 {{ font-size: 10.5pt; margin: 12px 0 4px; break-after: avoid; }}
p {{ margin: 3px 0 6px; }}
table {{ border-collapse: collapse; width: 100%; margin: 4px 0 10px; font-size: 8.5pt; }}
th, td {{ border: 1px solid {palette.LINE}; padding: 2px 6px; }}
th {{ background: #f6f7f9; font-weight: 600; }}
tr {{ break-inside: avoid; }}
td.r {{ text-align: right; }} td.c {{ text-align: center; }}
img {{ width: 100%; margin: 4px 0 8px; break-inside: avoid; }}
blockquote {{ margin: 2px 0 8px; padding: 4px 10px; border-left: 3px solid {palette.LINE}; background: #f6f7f9; white-space: pre-wrap; }}
ul {{ margin: 2px 0 8px; padding-left: 18px; }}
.note {{ color: {palette.MUTED}; font-size: 8.5pt; }}
a {{ color: {palette.ACCENT}; text-decoration: none; }}
"""


def to_html(blocks: list[Block], assets_dir: Path, title: str) -> str:
    out: list[str] = []
    for b in blocks:
        kind = b[0]
        if kind in ("h1", "h2", "h3"):
            out.append(f"<{kind}>{html.escape(b[1])}</{kind}>")
        elif kind == "p":
            out.append(f"<p>{_inline(b[1])}</p>")
        elif kind == "note":
            out.append(f'<p class="note">{html.escape(b[1])}</p>')
        elif kind == "quote":
            out.append(f"<blockquote>{html.escape(b[1])}</blockquote>")
        elif kind == "img":
            out.append(f'<img src="{(assets_dir / b[1]).as_uri()}" alt="{html.escape(b[2])}">')
        elif kind == "list":
            out.append("<ul>" + "".join(f"<li>{_cell_html(i)}</li>" for i in b[1]) + "</ul>")
        elif kind == "table":
            _, headers, rows, aligns = b
            head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
            body = "".join("<tr>" + "".join(f'<td class="{a}">{_cell_html(c)}</td>' for c, a in zip(r, aligns)) + "</tr>" for r in rows)
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f"<style>{CSS}</style></head><body>{''.join(out)}</body></html>")
