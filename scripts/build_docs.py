"""Fill the code boxes of docs/index.html with the real source.

A code box is a leaf of the flowchart:

    <details class="node c" data-src="coherence_analyzer/gate.py::_intent" data-why="optional caption"></details>

`data-src` is `<file>::<name>` or `<file>::<Class>.<method>`. This script replaces everything inside each such
element with a summary (`gate._intent`, file:line) and the function's source, syntax coloured. Run it after a
code change; it is idempotent. `--check` exits 1 when the page is out of date (nothing is written).

    python scripts/build_docs.py            # rewrite docs/index.html
    python scripts/build_docs.py --check    # stale or unresolved boxes -> exit 1
"""
from __future__ import annotations

import argparse
import ast
import html
import io
import keyword
import re
import sys
import textwrap
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "index.html"

# One code box: no nested <details> inside it, so a non-greedy match up to the first </details> is exact.
BOX = re.compile(r'(<details class="node c"[^>]*\bdata-src="([^"]+)"[^>]*>)(.*?)(</details>)', re.S)
WHY = re.compile(r'\bdata-why="([^"]*)"')

STRING_TOKENS = {tokenize.STRING} | {getattr(tokenize, n) for n in ("FSTRING_START", "FSTRING_MIDDLE",
                                                                     "FSTRING_END") if hasattr(tokenize, n)}
CONSTS = {"True", "False", "None"}


class Unresolved(LookupError):
    pass


def find(tree: ast.Module, dotted: str) -> ast.AST:
    """Top-level def/class `name`, or `Class.method`."""
    scope: list[ast.stmt] = tree.body
    node: ast.AST | None = None
    for part in dotted.split("."):
        node = next((n for n in scope if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.name == part), None)
        if node is None:
            raise Unresolved(dotted)
        scope = getattr(node, "body", [])
    assert node is not None
    return node


def extract(ref: str) -> tuple[str, int, int, str]:
    """`file::name` -> (relative path, first line, last line, dedented source incl. decorators)."""
    rel, _, dotted = ref.partition("::")
    path = ROOT / rel
    if not path.is_file() or not dotted:
        raise Unresolved(ref)
    src = path.read_text(encoding="utf-8")
    node = find(ast.parse(src), dotted)
    first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    last = node.end_lineno or node.lineno
    lines = src.splitlines()[first - 1:last]
    return rel, first, last, textwrap.dedent("\n".join(lines))


def highlight(code: str) -> str:
    """Python source -> escaped HTML with <span> classes. Whitespace and layout are kept byte for byte."""
    lines = code.splitlines(keepends=True)
    offsets = [0]
    for ln in lines:
        offsets.append(offsets[-1] + len(ln))

    def pos(rc: tuple[int, int]) -> int:
        r, c = rc
        return offsets[r - 1] + c if r - 1 < len(lines) else len(code)

    out: list[str] = []
    cur = 0
    prev = ""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(code).readline))
    except (tokenize.TokenError, SyntaxError):
        return html.escape(code)
    for t in toks:
        a, b = pos(t.start), pos(t.end)
        if a < cur:
            continue
        cls = None
        if t.type in STRING_TOKENS:
            cls = "s"
        elif t.type == tokenize.COMMENT:
            cls = "cm"
        elif t.type == tokenize.NUMBER:
            cls = "n"
        elif t.type == tokenize.NAME:
            if prev in ("def", "class"):
                cls = "fn"
            elif t.string in CONSTS:
                cls = "n"
            elif keyword.iskeyword(t.string) or (keyword.issoftkeyword(t.string) and t.string in ("match", "case")):
                cls = "kw"
        elif t.type == tokenize.OP and t.string == "@" and prev in ("", "\n"):
            cls = "kw"
        out.append(html.escape(code[cur:a]))
        text = html.escape(code[a:b])
        out.append(f'<span class="{cls}">{text}</span>' if cls else text)
        cur = b
        if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT):
            prev = t.string
        elif t.type in (tokenize.NEWLINE, tokenize.NL):
            prev = "\n"
    out.append(html.escape(code[cur:]))
    return "".join(out)


def label(rel: str, dotted: str) -> str:
    """coherence_analyzer/rules/limits.py + build_limits -> limits.build_limits"""
    return f"{Path(rel).stem}.{dotted}"


def render(open_tag: str, ref: str) -> str:
    rel, first, last, code = extract(ref)
    dotted = ref.partition("::")[2]
    m = WHY.search(open_tag)
    why = f" · {m.group(1)}" if m and m.group(1) else ""
    n = last - first + 1
    href = "../" + rel
    return (f'<summary><b><code>{html.escape(label(rel, dotted))}</code></b>'
            f'<small>{html.escape(rel)}:{first} · {n} line{"s" if n > 1 else ""}{why}</small></summary>'
            f'<div class="body"><div class="srcbar"><a href="{href}">{html.escape(rel)}</a> lines {first}-{last}'
            f'</div><pre class="src">{highlight(code)}</pre></div>')


def build(page: str) -> tuple[str, list[str]]:
    errors: list[str] = []

    def sub(m: re.Match) -> str:
        try:
            return m.group(1) + render(m.group(1), m.group(2)) + m.group(4)
        except Unresolved as e:
            errors.append(f"{m.group(2)}: not found ({e})")
            return m.group(0)

    return BOX.sub(sub, page), errors


def refs(page: str) -> list[str]:
    return [m.group(2) for m in BOX.finditer(page)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if docs/index.html is stale; write nothing")
    ap.add_argument("--page", type=Path, default=PAGE)
    args = ap.parse_args(argv)
    page = args.page.read_text(encoding="utf-8")
    new, errors = build(page)
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    n = len(refs(page))
    if args.check:
        if errors or new != page:
            print(f"{args.page} is out of date: run python scripts/build_docs.py", file=sys.stderr)
            return 1
        print(f"ok: {n} code boxes up to date")
        return 0
    if new != page:
        args.page.write_text(new, encoding="utf-8")
    print(f"{n} code boxes, {len(errors)} unresolved")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
