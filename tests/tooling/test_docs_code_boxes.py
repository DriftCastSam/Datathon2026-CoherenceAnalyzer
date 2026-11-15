"""docs/index.html code boxes (`data-src="file::name"`) must point at functions that exist.

Only resolution is tested, not freshness: a code change does not fail the suite, but a rename or a deleted function
does. Refresh the snippets with `python scripts/build_docs.py` (`--check` reports stale ones)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("build_docs", ROOT / "scripts" / "build_docs.py")
build_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_docs)


def test_every_code_box_resolves():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    refs = build_docs.refs(page)
    assert len(refs) > 50
    _, errors = build_docs.build(page)
    assert errors == []


def test_highlight_keeps_source_text():
    import html
    import re
    code = 'def f(x: int = 1) -> str:\n    # c\n    return f"{x}" + \'a\'  # tail\n'
    out = build_docs.highlight(code)
    assert html.unescape(re.sub(r"<[^>]+>", "", out)) == code
    assert '<span class="fn">f</span>' in out


def test_unresolved_ref_is_reported():
    page = '<details class="node c" data-src="coherence_analyzer/gate.py::no_such_fn"></details>'
    new, errors = build_docs.build(page)
    assert new == page and errors
