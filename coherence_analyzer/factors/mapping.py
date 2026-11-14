"""Check -> factor mapping and display/compute orders (docs/spec-factors.md §20.2). Pure data, no imports from the package.

ESG split (§20.2): explicit approved exclusions (H11, H13) are `contraintes`; leanings and unapproved rules (S4) are
`preferences`. A check id missing from the table is a programming error (`factor_of` raises KeyError).
"""
from __future__ import annotations

import copy

DISPLAY_ORDER = ("objectif", "horizon", "risque", "contraintes", "preferences")   # slide order 01..05
COMPUTE_ORDER = ("horizon", "contraintes", "risque", "objectif", "preferences")   # dependency order

_OWNED: dict[str, tuple[str, ...]] = {
    "objectif": ("G1", "O1"),
    "horizon": ("H9",),
    "risque": ("H4", "H7", "H8", "H14", "S1", "S3"),
    "contraintes": ("H1", "H2", "H3", "H5", "H6", "H10", "H11", "H12", "H13", "S2"),
    "preferences": ("S4",),
}

CHECK_FACTOR: dict[str, str] = {cid: fid for fid in DISPLAY_ORDER for cid in _OWNED[fid]}


def factor_of(check_id: str) -> str:
    """The one factor that owns `check_id`; KeyError for an id that is not in the §20.2 table."""
    return CHECK_FACTOR[check_id]


def tag_checks(checks: list[dict]) -> list[dict]:
    """Return copies of the checks with a `factor` key (hard.py / soft.py output stays unchanged)."""
    out = []
    for c in checks:
        n = copy.deepcopy(c)
        n["factor"] = factor_of(c["id"])
        out.append(n)
    return out
