"""numcheck engine: every number in an explanation must exist in the facts payload (SPEC §9.3 item 2, §15 test_numcheck).

Handles `7,4 %`, `7.4%`, `360 000 $`, `360 000 $` (NBSP / NNBSP), `$360,000`, `360,000 $`, `360000 $` and dates
(`2026-11-12`, `2030-06`) and years. A text number matches a payload number within +-0.05 absolute for percentages
and plain decimals, +-0.5 % relative for amounts, exactly for dates and years. Integers <= 10 with no unit are counts
and are exempt (a `5 %` is not a count). Signs are ignored: `13,4 %` matches `-13,4 %`. Digits glued to letters or
underscores (`H10`, `p5_T`, `ev_robert_0007`) are identifiers, not numbers. Pure and deterministic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PCT_TOL = 0.05
AMOUNT_REL_TOL = 0.005
SMALL_INT_MAX = 10
_EPS = 1e-9
_WS = " \u00a0\u202f"

_DATE = re.compile(r"(?<![\w])(\d{4})-(\d{2})(?:-(\d{2}))?(?!\d)")
_NUM = re.compile(r"(?<![\w])(\d{1,3}(?:[ \u00a0\u202f,]\d{3})+|\d+)(?:[.,](\d+))?(?!\w)")


@dataclass(frozen=True)
class Token:
    kind: str            # date | year | pct | amt | plain | count
    value: float | str   # str for dates / years
    text: str            # as written, with its unit (offender report)


def _next_char(s: str, i: int) -> str:
    while i < len(s) and s[i] in _WS:
        i += 1
    return s[i] if i < len(s) else ""


def _prev_char(s: str, i: int) -> str:
    i -= 1
    while i >= 0 and s[i] in _WS:
        i -= 1
    return s[i] if i >= 0 else ""


def tokenize(text: str) -> list[Token]:
    """Numbers of `text` in order of appearance."""
    toks: list[tuple[int, Token]] = []
    work = text
    for m in _DATE.finditer(text):
        mo, d = int(m.group(2)), m.group(3)
        if not (1 <= mo <= 12) or (d is not None and not 1 <= int(d) <= 31):
            continue
        toks.append((m.start(), Token("date", m.group(0), m.group(0))))
        work = work[:m.start()] + "\x00" * (m.end() - m.start()) + work[m.end():]
    for m in _NUM.finditer(work):
        ip, frac = m.group(1), m.group(2)
        after, before = _next_char(work, m.end()), _prev_char(work, m.start())
        dollar = after == "$" or before == "$"
        grouped = False
        if any(c in ip for c in _WS):
            digits, grouped = re.sub(r"[ \u00a0\u202f]", "", ip), True
        elif "," in ip:
            groups = ip.split(",")
            if len(groups) > 2 or dollar or len(groups[0]) == 3:      # `$360,000`, `1,234,567`, `360,000`
                digits, grouped = "".join(groups), True
            else:                                                     # `7,400 %`: a decimal comma
                digits, frac = groups[0], groups[1]
        else:
            digits = ip
        num = float(digits + ("." + frac if frac else ""))
        end = m.end()
        if after == "%":
            kind, end = "pct", work.index("%", end) + 1
        elif dollar:
            kind = "amt"
            if after == "$":
                end = work.index("$", end) + 1
        elif grouped:
            kind = "amt"
        elif frac is None and len(digits) == 4 and 1900 <= num <= 2100:
            kind = "year"
        elif frac is None and len(digits) >= 4:
            kind = "amt"
        elif frac is None and num <= SMALL_INT_MAX:
            kind = "count"
        else:
            kind = "plain"
        start = m.start()
        if kind == "amt" and before == "$":
            start = work.rindex("$", 0, start)
        value: float | str = str(int(num)) if kind == "year" else num
        toks.append((start, Token(kind, value, text[start:end].strip())))
    return [t for _, t in sorted(toks, key=lambda p: p[0])]


@dataclass
class _Known:
    pct: list[float] = field(default_factory=list)      # numbers written with %
    plain: list[float] = field(default_factory=list)    # unit-less numbers (incl. counts)
    amt: list[float] = field(default_factory=list)
    raw: list[float] = field(default_factory=list)      # JSON numbers in the payload
    exact: set[str] = field(default_factory=set)        # dates and years


def _register(known: _Known, tok: Token) -> None:
    v = tok.value
    if tok.kind == "date":
        s = str(v)
        known.exact.update({s, s[:7], s[:4]})
    elif tok.kind == "year":
        known.exact.add(str(v))
        known.plain.append(float(v))
        known.amt.append(float(v))
    elif tok.kind == "pct":
        known.pct.append(float(v))
    elif tok.kind == "amt":
        known.amt.append(float(v))
    else:
        known.plain.append(float(v))
        known.amt.append(float(v))


def _walk(node, known: _Known) -> None:
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, str):
        for t in tokenize(node):
            _register(known, t)
    elif isinstance(node, (int, float)):
        known.raw.append(abs(float(node)))
    elif isinstance(node, dict):
        for k in sorted(node, key=str):
            _walk(node[k], known)
    elif isinstance(node, (list, tuple, set, frozenset)):
        items = sorted(node, key=str) if isinstance(node, (set, frozenset)) else node
        for x in items:
            _walk(x, known)


def _near(x: float, pool: list[float]) -> bool:
    return any(abs(x - p) <= PCT_TOL + _EPS for p in pool)


def _near_rel(x: float, pool: list[float]) -> bool:
    return any(abs(x - p) <= AMOUNT_REL_TOL * abs(p) + _EPS for p in pool)


def _matches(tok: Token, k: _Known) -> bool:
    if tok.kind in ("date", "year"):
        return str(tok.value) in k.exact
    x = float(tok.value)
    if tok.kind == "pct":
        return _near(x, k.pct + k.plain + k.raw) or _near(x, [r * 100 for r in k.raw])
    if tok.kind == "amt":
        return _near_rel(x, k.amt + k.raw)
    return _near(x, k.plain + k.pct + k.raw)      # plain decimals and integers > 10


def numcheck(text: str, payload) -> list[str]:
    """Numbers in `text` that match no payload number (empty list = pass). Order of appearance, no duplicates."""
    known = _Known()
    _walk(payload, known)
    bad: list[str] = []
    for tok in tokenize(text or ""):
        if tok.kind == "count" or _matches(tok, known):
            continue
        if tok.text not in bad:
            bad.append(tok.text)
    return bad
