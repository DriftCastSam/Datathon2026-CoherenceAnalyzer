"""Five-factor architecture: mapping, factor modules, report sections, template_section (docs/spec-factors.md §20.2-20.4, §20.8).

Factor modules are views: rules stay in rules/, checks in coherence/hard.py (§20.3). Section statuses are pinned on
the measured Robert runs (docs/spec-decisions.md D2/D3/D5; hard.run_all + engine.analyse, profile derived with
as_of 2026-11-12):
  Robert + NVDA 10% from ZST.TO: non-pass checks H3, H4, H7, H8, H9, H10 violation; H6, H10 unknown.
  Robert baseline: H6, H10 unknown only; verdict `coherent`.
Soft checks S1-S4 are not implemented yet (P1), so the contexts below carry hard checks only.
`explain.verify_section` is P0.8; here V1, V3, V4, V5 are asserted with local helpers and V2 with `explain.numcheck`.
"""
import copy
import fnmatch
import json
import os

import pytest

os.environ.setdefault("COHERENCE_ANALYZER_LLM", "mock")

from coherence_analyzer import data, engine, explain, factors  # noqa: E402
from coherence_analyzer.coherence import hard, report  # noqa: E402
from coherence_analyzer.factors import contraintes, horizon, objectif, preferences, risque  # noqa: E402
from coherence_analyzer.rules import derive, limits as limits_mod  # noqa: E402

AS_OF = "2026-11-12"
ROBERT_VALUE = 400_000
ROBERT_HORIZON_M = 48
META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")

DISPLAY = ("objectif", "horizon", "risque", "contraintes", "preferences")
COMPUTE = ("horizon", "contraintes", "risque", "objectif", "preferences")
MODULES = {"objectif": objectif, "horizon": horizon, "risque": risque,
           "contraintes": contraintes, "preferences": preferences}

# §20.2 table, "Checks owned" column (G1 and O1 included)
EXPECTED_OWNER = {
    "objectif": {"G1", "O1"},
    "horizon": {"H9"},
    "risque": {"H4", "H7", "H8", "H14", "S1", "S3"},
    "contraintes": {"H1", "H2", "H3", "H5", "H6", "H10", "H11", "H12", "H13", "S2"},
    "preferences": {"S4"},
}
ALL_IDS = {f"H{i}" for i in range(1, 15)} | {"S1", "S2", "S3", "S4", "G1", "O1"}

QUESTIONS = {
    "fr-CA": {"objectif": "Pourquoi investir?", "horizon": "Quand l'argent sera-t-il requis?",
              "risque": "Quelle perte peut être absorbée?", "contraintes": "Quelles limites s'appliquent?",
              "preferences": "Qu'est-ce qui oriente les choix?"},
    "en-CA": {"objectif": "Why invest?", "horizon": "When will the money be needed?",
              "risque": "What loss can be absorbed?", "contraintes": "Which limits apply?",
              "preferences": "What guides the choices?"},
}
TITLES_FR = {"objectif": "Objectif", "horizon": "Horizon", "risque": "Risque",
             "contraintes": "Contraintes", "preferences": "Préférences"}

# §20.2 "Profile fields shown": the explicit dotted paths (wildcard entries are checked separately)
EXPLICIT_FIELDS = {
    "objectif": {"objectives.primary", "objectives.secondary", "objectives.goals", "identity.life_stage"},
    "horizon": {"objectives.horizon_years", "objectives.effective_horizon_years",
                "situation.planned_withdrawals", "identity.retirement_target_age"},
    "risque": {"risk.declared", "risk.capacity", "risk.effective", "risk.behavioural",
               "risk.max_acceptable_drawdown_pct", "risk.reaction_to_20pct_drop"},
    "contraintes": {"situation.emergency_fund_months", "situation.income_stability", "identity.employer_sector",
                    "preferences.asset_types_refused", "preferences.max_fee_mer_pct", "knowledge.score"},
    "preferences": {"values.notes", "preferences.prefers_dividends", "preferences.prefers_large_caps",
                    "preferences.home_bias_ok", "preferences.interests"},
}
# §20.2 "Engine metrics quoted"
EXPECTED_METRICS = {"objectif": {"p50_T"}, "horizon": {"p5_T", "p50_T", "p95_T", "width"},
                    "risque": {"vol", "mdd", "loss_prob"}, "preferences": set()}

RANK = {"pass": 0, "info": 1, "unknown": 2, "warning": 3, "violation": 4}   # §20.4
SEGMENT_KINDS = {"fact", "assumption", "inference", "unknown"}              # §20.5


# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def raw_profile(root) -> dict:
    return json.loads((root / "fixtures" / "robert" / "profile.json").read_text())


@pytest.fixture(scope="module")
def profile(raw_profile) -> dict:
    p = derive.derive_all(copy.deepcopy(raw_profile), portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(p, ROBERT_VALUE)
    p["limits"], p["limits_trace"] = lim, trace
    return p


@pytest.fixture(scope="module")
def assets():
    return data.load_assets()


@pytest.fixture(scope="module")
def meta() -> dict:
    return {n: data.load_meta(n) for n in META_NAMES}


@pytest.fixture(scope="module")
def events(root) -> list[dict]:
    lines = (root / "fixtures" / "robert" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture(scope="module")
def intent_fixture(root) -> dict:
    return json.loads((root / "fixtures" / "robert" / "intents.json").read_text())[0]


@pytest.fixture(scope="module")
def m_base(market) -> dict:
    return engine.analyse(market, engine.ROBERT, ROBERT_HORIZON_M, ROBERT_VALUE)


@pytest.fixture(scope="module")
def m_nvda(market) -> dict:
    return engine.analyse(market, engine.ROBERT_NVDA, ROBERT_HORIZON_M, ROBERT_VALUE)


def _fractions(w_pct: dict) -> dict:
    return {t: v / 100 for t, v in w_pct.items()}


@pytest.fixture(scope="module")
def checks_base(profile, assets, meta, m_base) -> list:
    return hard.run_all(_fractions(engine.ROBERT), m_base, profile, assets, meta)


@pytest.fixture(scope="module")
def checks_nvda(profile, assets, meta, m_nvda) -> list:
    return hard.run_all(_fractions(engine.ROBERT_NVDA), m_nvda, profile, assets, meta)


def _ctx(profile, current, proposed=None, m_cur=None, m_prop=None, intent=None, events=(), locale="fr-CA"):
    return factors.SectionContext(
        profile=profile, limits=profile["limits"], limits_trace=profile["limits_trace"],
        checks_current=current, checks_proposed=proposed, metrics_current=m_cur or {},
        metrics_proposed=m_prop, intent=intent, events=list(events), locale=locale)


@pytest.fixture(scope="module")
def ctx_base(profile, checks_base, m_base):
    """Flow C on Robert's current portfolio."""
    return _ctx(profile, checks_base, m_cur=m_base)


@pytest.fixture(scope="module")
def ctx_review(profile, checks_nvda, m_nvda):
    """Flow C on the 'already bought NVDA' portfolio (D7 point 5)."""
    return _ctx(profile, checks_nvda, m_cur=m_nvda)


@pytest.fixture(scope="module")
def ctx_trade(profile, checks_base, checks_nvda, m_base, m_nvda, intent_fixture, events):
    """Flow B: Robert + NVDA 10% funded from ZST.TO (current = baseline, proposed = NVDA)."""
    return _ctx(profile, checks_base, checks_nvda, m_base, m_nvda, intent_fixture, events)


@pytest.fixture(scope="module")
def secs_trade(ctx_trade) -> dict:
    return {s["factor"]: s for s in factors.build_sections(ctx_trade)}


@pytest.fixture(scope="module")
def secs_review(ctx_review) -> dict:
    return {s["factor"]: s for s in factors.build_sections(ctx_review)}


@pytest.fixture(scope="module")
def secs_base(ctx_base) -> dict:
    return {s["factor"]: s for s in factors.build_sections(ctx_base)}


# ---------------------------------------------------------------- helpers

def _norm(s: str) -> str:
    """SPEC-AMBIGUITY: fr-CA grouping/percent spacing may be a plain space, NBSP or NNBSP (all render alike);
    the tests compare after folding them to a plain space."""
    return s.replace("\u00a0", " ").replace("\u202f", " ")


def _entries(facts: dict, cid: str) -> list[dict]:
    return [c for c in facts["checks"] if c["id"] == cid]


def _entry(facts: dict, cid: str, status: str) -> dict:
    found = [c for c in _entries(facts, cid) if c["status"] == status]
    assert len(found) == 1, (cid, status, facts["checks"])
    return found[0]


def _worst(statuses) -> str:
    return max(statuses, key=RANK.__getitem__, default="pass")


def _verdict_from_statuses(statuses) -> str:
    st = set(statuses)
    if "violation" in st:
        return "incoherent"
    if "warning" in st:
        return "coherent_with_warnings"
    return "coherent"


def _resolve(path: str, node) -> bool:
    """Dotted path resolution tolerant of keys that themselves contain dots (e.g. 'risk.declared')."""
    if path == "":
        return True
    if not isinstance(node, dict):
        return False
    for key in node:
        if path == key or (path.startswith(key + ".") and _resolve(path[len(key) + 1:], node[key])):
            return True
    return False


def _field_exists(profile: dict, path: str) -> bool:
    """Profile path with `*` as a one-level wildcard; trailing `[...]` filters are ignored."""
    path = path.split("[", 1)[0]

    def walk(node, parts):
        if not parts:
            return True
        if not isinstance(node, dict):
            return False
        head, rest = parts[0], parts[1:]
        keys = [k for k in node if fnmatch.fnmatchcase(k, head)]
        return any(walk(node[k], rest) for k in keys)

    return walk(profile, path.split("."))


def _all_text(out: dict) -> str:
    return " ".join(seg["text"] for seg in out["segments"])


# ---------------------------------------------------------------- §20.2 mapping

def test_orders_are_the_spec_orders():
    """§20.2: compute order horizon -> contraintes -> risque -> objectif -> preferences; display = slide order 01..05."""
    assert tuple(factors.DISPLAY_ORDER) == DISPLAY
    assert tuple(factors.COMPUTE_ORDER) == COMPUTE
    assert set(factors.DISPLAY_ORDER) == set(factors.COMPUTE_ORDER)


def test_check_factor_covers_every_check_exactly_once():
    """§20.2/§20.8: every id of §7.3/§7.4 plus G1, O1 maps to exactly one known factor; nothing else is mapped."""
    assert set(factors.CHECK_FACTOR) == ALL_IDS
    assert len(ALL_IDS) == 20
    assert set(factors.CHECK_FACTOR.values()) <= set(DISPLAY)
    owned = [cid for f in DISPLAY for cid in EXPECTED_OWNER[f]]
    assert len(owned) == len(set(owned)) == 20


@pytest.mark.parametrize("fid", DISPLAY)
def test_check_factor_matches_the_table(fid):
    """§20.2 'Checks owned' column, factor by factor (ESG split: H11/H13 are contraintes, S4 is preferences)."""
    assert {c for c, f in factors.CHECK_FACTOR.items() if f == fid} == EXPECTED_OWNER[fid]


def test_factor_of_agrees_with_the_table_and_rejects_unknown_ids():
    for cid, fid in EXPECTED_OWNER_FLAT().items():
        assert factors.factor_of(cid) == fid
    # §20.2: "A check id missing from the table is a programming error"
    for bad in ("H99", "X1", "", "h1"):
        with pytest.raises((KeyError, ValueError)):
            factors.factor_of(bad)


def EXPECTED_OWNER_FLAT() -> dict:
    return {cid: f for f, ids in EXPECTED_OWNER.items() for cid in ids}


def test_every_emitted_check_id_is_mapped(checks_base, checks_nvda):
    for c in checks_base + checks_nvda:
        assert c["id"] in factors.CHECK_FACTOR, c["id"]


def test_tag_checks_adds_factor_without_touching_the_originals(checks_nvda):
    """§20.4: checks gain a `factor` key added by the report builder; hard.py/soft.py output stays unchanged."""
    before = copy.deepcopy(checks_nvda)
    tagged = factors.tag_checks(checks_nvda)
    assert checks_nvda == before
    assert all("factor" not in c for c in checks_nvda)
    assert len(tagged) == len(checks_nvda)
    for orig, new in zip(checks_nvda, tagged):
        assert new is not orig
        assert new["factor"] == EXPECTED_OWNER_FLAT()[orig["id"]]
        assert {k: v for k, v in new.items() if k != "factor"} == orig
    assert factors.tag_checks([]) == []


# ---------------------------------------------------------------- §20.3 factor modules

@pytest.mark.parametrize("fid", DISPLAY)
def test_module_constants(fid):
    m = MODULES[fid]
    assert m.ID == fid
    assert m.TITLE["fr-CA"] == TITLES_FR[fid]
    assert isinstance(m.TITLE["en-CA"], str) and m.TITLE["en-CA"]
    assert m.QUESTION == QUESTIONS_FOR(fid)
    for const in (m.FIELDS, m.METRICS, m.REQUIRED, m.NOT_COVERED):
        assert isinstance(const, tuple)
        assert all(isinstance(x, str) and x for x in const)
    assert len(set(m.FIELDS)) == len(m.FIELDS) > 0
    assert callable(m.facts)


def QUESTIONS_FOR(fid: str) -> dict:
    return {loc: QUESTIONS[loc][fid] for loc in ("fr-CA", "en-CA")}


def test_risque_english_title_is_risk():
    assert risque.TITLE == {"fr-CA": "Risque", "en-CA": "Risk"}


@pytest.mark.parametrize("fid", DISPLAY)
def test_fields_are_the_spec_fields(fid):
    """§20.2 'Profile fields shown': every explicit path is listed (no field is dropped)."""
    assert EXPLICIT_FIELDS[fid] <= set(MODULES[fid].FIELDS)


def test_wildcard_fields_cover_the_esg_split():
    """§20.2 ESG split: exclusions (approved limits) are contraintes; importance scores are preferences."""
    assert any(f.startswith("values.") and f.endswith(".exclusions") for f in contraintes.FIELDS)
    assert any(f.startswith("values.") and f.endswith(".importance") for f in preferences.FIELDS)
    assert not any(f.endswith(".importance") for f in contraintes.FIELDS)
    assert not any(f.endswith(".exclusions") for f in preferences.FIELDS)


@pytest.mark.parametrize("fid", DISPLAY)
def test_every_field_path_exists_in_the_robert_fixture(fid, raw_profile):
    """§20.8: every factor's FIELDS path exists in the Robert fixture."""
    assert len(MODULES[fid].FIELDS) > 0
    missing = [f for f in MODULES[fid].FIELDS if not _field_exists(raw_profile, f)]
    assert missing == []


def test_a_field_belongs_to_one_factor():
    """§20.2 lists no field under two factors."""
    seen: dict[str, str] = {}
    assert all(MODULES[f].FIELDS for f in DISPLAY)
    for fid in DISPLAY:
        for f in MODULES[fid].FIELDS:
            assert f not in seen, (f, seen.get(f), fid)
            seen[f] = fid


@pytest.mark.parametrize("fid", ["objectif", "horizon", "risque", "preferences"])
def test_metrics_are_the_spec_engine_keys(fid, m_base):
    """§20.2 'Engine metrics quoted'; preferences quote none."""
    assert set(MODULES[fid].METRICS) == EXPECTED_METRICS[fid]
    assert set(MODULES[fid].METRICS) <= set(m_base)


def test_not_covered_lists_what_the_spec_says():
    """§20.2: contraintes `fiscalite` and objectif `objective_vs_portfolio` are listed, never a check."""
    assert "fiscalite" in contraintes.NOT_COVERED
    assert "objective_vs_portfolio" in objectif.NOT_COVERED
    assert not set(contraintes.NOT_COVERED) & set(factors.CHECK_FACTOR)
    assert not set(objectif.NOT_COVERED) & set(factors.CHECK_FACTOR)


# ---------------------------------------------------------------- §20.4 sections: shape and order

SECTION_KEYS = {"factor", "title", "question", "status", "checks", "unknowns", "not_covered", "facts"}


def test_five_sections_in_display_order(ctx_trade):
    secs = factors.build_sections(ctx_trade)
    assert [s["factor"] for s in secs] == list(DISPLAY)
    assert isinstance(secs, list) and len(secs) == 5


@pytest.mark.parametrize("fid", DISPLAY)
def test_section_shape_fr(fid, secs_trade):
    s = secs_trade[fid]
    assert SECTION_KEYS <= set(s)
    assert s["title"] == TITLES_FR[fid]
    assert s["question"] == QUESTIONS["fr-CA"][fid]
    assert s["status"] in RANK
    assert isinstance(s["checks"], list) and all(isinstance(c, str) for c in s["checks"])
    assert len(s["checks"]) == len(set(s["checks"]))          # ids only, one entry per id
    assert isinstance(s["unknowns"], list)
    assert list(s["not_covered"]) == list(MODULES[fid].NOT_COVERED)
    assert isinstance(s["facts"], dict)
    assert json.loads(json.dumps(s["facts"])) == s["facts"]   # JSON-clean, no numpy leakage


@pytest.mark.parametrize("fid", DISPLAY)
def test_section_english_locale(fid, profile, checks_nvda, m_nvda):
    secs = factors.build_sections(_ctx(profile, checks_nvda, m_cur=m_nvda, locale="en-CA"))
    s = {x["factor"]: x for x in secs}[fid]
    assert s["title"] == MODULES[fid].TITLE["en-CA"]
    assert s["question"] == QUESTIONS["en-CA"][fid]
    assert s["facts"]["locale"] == "en-CA"
    assert s["facts"]["question"] == QUESTIONS["en-CA"][fid]


@pytest.mark.parametrize("fixture_name", ["secs_trade", "secs_review", "secs_base"])
def test_section_checks_belong_to_the_factor(fixture_name, request):
    secs = request.getfixturevalue(fixture_name)
    for fid, s in secs.items():
        assert set(s["checks"]) <= EXPECTED_OWNER[fid], (fid, s["checks"])


def test_section_checks_list_every_non_pass_check_of_the_factor(secs_trade, checks_nvda):
    # SPEC-AMBIGUITY: §20.4 does not say whether passing checks are listed in `checks`; only non-pass ones are required.
    for fid, s in secs_trade.items():
        non_pass = {c["id"] for c in checks_nvda
                    if c["status"] != "pass" and EXPECTED_OWNER_FLAT()[c["id"]] == fid}
        assert non_pass <= set(s["checks"]), fid


# ---------------------------------------------------------------- §20.8 statuses (measured on the real code)

def test_robert_nvda_trade_section_statuses(secs_trade):
    """§20.8 / D2: horizon violation (H9), risque violation (H4, H7, H8), contraintes violation (H3, H10),
    objectif pass, preferences pass. Flow B reports on checks_proposed."""
    assert {f: s["status"] for f, s in secs_trade.items()} == {
        "objectif": "pass", "horizon": "violation", "risque": "violation",
        "contraintes": "violation", "preferences": "pass"}
    assert set(secs_trade["horizon"]["checks"]) == {"H9"}
    assert {"H4", "H7", "H8"} <= set(secs_trade["risque"]["checks"])
    assert {"H3", "H10"} <= set(secs_trade["contraintes"]["checks"])
    assert secs_trade["objectif"]["checks"] == [] and secs_trade["preferences"]["checks"] == []


def test_robert_nvda_review_section_statuses_match_flow_b(secs_trade, secs_review):
    """Flow C on the same portfolio reports on checks_current and gets the same statuses."""
    assert {f: s["status"] for f, s in secs_review.items()} == {f: s["status"] for f, s in secs_trade.items()}
    assert {f: set(s["checks"]) for f, s in secs_review.items()} == {f: set(s["checks"]) for f, s in secs_trade.items()}


def test_robert_baseline_section_statuses(secs_base):
    """D5: baseline contraintes `unknown` (H6, H10 unknown), every other section `pass`."""
    assert {f: s["status"] for f, s in secs_base.items()} == {
        "objectif": "pass", "horizon": "pass", "risque": "pass",
        "contraintes": "unknown", "preferences": "pass"}


def test_flow_b_uses_proposed_checks_not_current(profile, checks_base, checks_nvda, m_base, m_nvda, intent_fixture):
    """§20.4: status is computed on checks_proposed in flow B; swapping current/proposed swaps the result."""
    swapped = factors.build_sections(_ctx(profile, checks_nvda, checks_base, m_nvda, m_base, intent_fixture))
    st = {s["factor"]: s["status"] for s in swapped}
    assert st["horizon"] == "pass" and st["risque"] == "pass" and st["contraintes"] == "unknown"


def test_unknowns_are_listed_whatever_the_status(secs_base, secs_trade):
    """§7.5/D3: unknowns are always listed; baseline contraintes lists H6 and H10, the others list none."""
    blob = json.dumps(secs_base["contraintes"]["unknowns"], default=str)
    assert "H6" in blob and "H10" in blob
    for fid in ("objectif", "horizon", "risque", "preferences"):
        assert secs_base[fid]["unknowns"] == []
    blob = json.dumps(secs_trade["contraintes"]["unknowns"], default=str)
    assert "H6" in blob and "H10" in blob
    assert secs_trade["horizon"]["unknowns"] == [] and secs_trade["risque"]["unknowns"] == []


# ---------------------------------------------------------------- §20.4 verdict consistency and status ranking

S1_WARNING = {"id": "S1", "kind": "soft", "status": "warning", "subject": None, "value": 2, "limit": None,
              "unit": "levels", "derived_from": ["risk.behavioural", "risk.effective"],
              "source": "llm:behaviour_classifier", "confidence": 0.55, "message_key": "behaviour_gap"}


def _with_status(checks: list, **changes: str) -> list:
    out = copy.deepcopy(checks)
    for c in out:
        if c["id"] in changes:
            c["status"] = changes[c["id"]]
    return out


@pytest.mark.parametrize("which,expected", [("base", "coherent"), ("nvda", "incoherent")])
def test_report_verdict_equals_verdict_from_section_statuses(which, expected, profile, checks_base, checks_nvda,
                                                              m_base, m_nvda):
    """§20.4 Consistency: report.verdict (§7.5) == verdict derived from the section statuses."""
    checks, m = (checks_base, m_base) if which == "base" else (checks_nvda, m_nvda)
    secs = factors.build_sections(_ctx(profile, checks, m_cur=m))
    assert report.verdict(checks) == expected
    assert _verdict_from_statuses(s["status"] for s in secs) == expected


def test_warning_gives_coherent_with_warnings_and_unknown_never_changes_the_verdict(profile, checks_base, m_base):
    """D3/§7.5: a soft warning -> coherent_with_warnings; unknowns (contraintes is `unknown`) change nothing."""
    checks = checks_base + [S1_WARNING]
    secs = {s["factor"]: s for s in factors.build_sections(_ctx(profile, checks, m_cur=m_base))}
    assert secs["risque"]["status"] == "warning"
    assert secs["contraintes"]["status"] == "unknown"
    assert report.verdict(checks) == "coherent_with_warnings"
    assert _verdict_from_statuses(s["status"] for s in secs.values()) == "coherent_with_warnings"
    only_unknown = {s["factor"]: s["status"] for s in
                    factors.build_sections(_ctx(profile, checks_base, m_cur=m_base))}
    assert _verdict_from_statuses(only_unknown.values()) == report.verdict(checks_base) == "coherent"


@pytest.mark.parametrize("changes,extra,expected", [
    ({}, [], "pass"),
    ({"H14": "info"}, [], "info"),
    ({"H14": "info", "H4": "unknown"}, [], "unknown"),
    ({"H14": "info", "H4": "unknown"}, [S1_WARNING], "warning"),
    ({"H14": "info"}, [S1_WARNING], "warning"),
    ({"H8": "violation", "H4": "unknown"}, [S1_WARNING], "violation"),
    ({"H7": "violation"}, [], "violation"),
])
def test_section_status_is_the_worst_of_its_checks(changes, extra, expected, profile, checks_base, m_base):
    """§20.4: ranked violation > warning > unknown > info > pass; owned checks only (risque here)."""
    checks = _with_status(checks_base, **changes) + [copy.deepcopy(c) for c in extra]
    secs = {s["factor"]: s for s in factors.build_sections(_ctx(profile, checks, m_cur=m_base))}
    assert secs["risque"]["status"] == expected
    # other factors only see their own checks: horizon/preferences/objectif have none of the changed ids
    assert secs["horizon"]["status"] == "pass" and secs["preferences"]["status"] == "pass"
    assert secs["objectif"]["status"] == "pass"


def test_section_with_no_checks_is_pass(profile, m_base):
    secs = {s["factor"]: s for s in factors.build_sections(_ctx(profile, [], m_cur=m_base))}
    assert {s["status"] for s in secs.values()} == {"pass"}
    assert all(s["checks"] == [] and s["unknowns"] == [] for s in secs.values())


def test_hard_checks_cannot_make_a_warning_section(secs_trade, secs_review, secs_base):
    """Invariant 3: with hard checks only, no section is `warning`."""
    for secs in (secs_trade, secs_review, secs_base):
        assert all(s["status"] != "warning" for s in secs.values())


# ---------------------------------------------------------------- §20.4 section facts payload

FACT_KEYS = {"factor", "locale", "question", "status", "verdict", "intent", "declared", "derived",
             "checks", "metrics", "evidence_events", "unknowns"}
INTENT_FACTS = {"ticker": "NVDA", "target_weight": "10 %", "funding": "ZST.TO"}


@pytest.mark.parametrize("fid", DISPLAY)
def test_facts_payload_shape_flow_b(fid, secs_trade):
    s = secs_trade[fid]
    f = s["facts"]
    assert FACT_KEYS <= set(f)
    assert f["factor"] == fid and f["locale"] == "fr-CA"
    assert f["question"] == QUESTIONS["fr-CA"][fid]
    assert f["status"] == s["status"]
    assert f["verdict"] == "incoherent"
    assert f["unknowns"] == s["unknowns"]
    assert isinstance(f["declared"], dict) and isinstance(f["derived"], dict)
    assert f["evidence_events"] == []      # no S1/S3 check in this run, so nothing cites an event


@pytest.mark.parametrize("fid", DISPLAY)
def test_facts_intent(fid, secs_trade, secs_review):
    """§20.4: intent pre-formatted for flow B, null for flow C."""
    assert {k: _norm(v) for k, v in secs_trade[fid]["facts"]["intent"].items()} == INTENT_FACTS
    assert secs_review[fid]["facts"]["intent"] is None


def test_facts_verdict_is_the_report_verdict(secs_base, secs_review):
    for s in secs_base.values():
        assert s["facts"]["verdict"] == "coherent"
    for s in secs_review.values():
        assert s["facts"]["verdict"] == "incoherent"


def test_facts_list_only_non_pass_checks(secs_trade, secs_base):
    for secs in (secs_trade, secs_base):
        for s in secs.values():
            for c in s["facts"]["checks"]:
                assert c["status"] != "pass", (s["factor"], c)
                assert c["status"] in RANK
    assert secs_trade["objectif"]["facts"]["checks"] == []
    assert secs_trade["preferences"]["facts"]["checks"] == []
    assert secs_base["horizon"]["facts"]["checks"] == []
    assert secs_base["risque"]["facts"]["checks"] == []


def test_facts_check_entries_horizon_and_risque(secs_trade):
    """Pre-formatted fr-CA values (§20.4 examples: '-13,4 %', '-12,0 %'); measured on Robert + NVDA."""
    h = secs_trade["horizon"]["facts"]
    assert [c["id"] for c in h["checks"]] == ["H9"]
    h9 = _entry(h, "H9", "violation")
    assert _norm(h9["value"]) == "316 971 $" and _norm(h9["limit"]) == "360 000 $"
    r = secs_trade["risque"]["facts"]
    assert {c["id"] for c in r["checks"]} == {"H4", "H7", "H8"}
    for cid, val, lim in (("H4", "25,0 %", "30,0 %"), ("H7", "9,8 %", "9,0 %"), ("H8", "-13,4 %", "-12,0 %")):
        e = _entry(r, cid, "violation")
        assert (_norm(e["value"]), _norm(e["limit"])) == (val, lim), cid


META_IDS = {"H11", "H12", "H13"}   # need the data/meta files (P1.2)


def test_facts_check_entries_contraintes(secs_trade):
    c = secs_trade["contraintes"]["facts"]
    # P1.2 oracle fix: H11-H13 now run; with no data/meta CSV each of their entries is `unknown` (SPEC §7.3, D7 point 4).
    assert sorted((e["id"], e["status"]) for e in c["checks"] if e["id"] not in META_IDS) == [
        ("H10", "unknown"), ("H10", "violation"), ("H3", "violation"), ("H6", "unknown")]
    assert all(e["status"] == "unknown" for e in c["checks"] if e["id"] in META_IDS)
    h3, h10 = _entry(c, "H3", "violation"), _entry(c, "H10", "violation")
    assert (_norm(h3["value"]), _norm(h3["limit"])) == ("10,0 %", "8,0 %")
    assert (_norm(h10["value"]), _norm(h10["limit"])) == ("34,7 %", "30,0 %")
    # SPEC-AMBIGUITY: the facts entry must let the writer name the offender; the ticker is the check subject.
    assert "NVDA" in json.dumps(h3, ensure_ascii=False)
    assert "Information Technology" in json.dumps(h10, ensure_ascii=False)


def test_facts_baseline_contraintes_lists_only_the_unknowns(secs_base):
    c = secs_base["contraintes"]["facts"]
    assert sorted((e["id"], e["status"]) for e in c["checks"] if e["id"] not in META_IDS) == [
        ("H10", "unknown"), ("H6", "unknown")]
    assert all(e["status"] == "unknown" for e in c["checks"] if e["id"] in META_IDS)   # P1.2 oracle fix, no meta CSV yet
    blob = json.dumps(c["unknowns"], default=str)
    assert "H6" in blob and "H10" in blob


@pytest.mark.parametrize("fid", DISPLAY)
def test_facts_check_entries_trace_to_the_limit(fid, secs_trade, checks_nvda):
    """§20.4: each entry carries id, status, value, limit, derived_from (starting at its limit, never an llm: path)."""
    originals = {}
    for c in checks_nvda:
        originals.setdefault((c["id"], c["status"]), c)
    for e in secs_trade[fid]["facts"]["checks"]:
        assert {"id", "status", "value", "limit", "derived_from"} <= set(e), e
        assert isinstance(e["value"], str) and isinstance(e["limit"], str)      # pre-formatted, copied by the writer
        orig = originals[(e["id"], e["status"])]
        assert e["derived_from"] and e["derived_from"][0] == orig["derived_from"][0]
        assert e["derived_from"][0].startswith("limits.")
        assert set(e["derived_from"]) <= set(orig["derived_from"])
        assert not any(p.startswith(("risk.behavioural", "knowledge.question_quality")) for p in e["derived_from"])


# ---------------------------------------------------------------- facts: metrics

BASE_METRICS_FR = {"vol": "7,3 %", "mdd": "-7,7 %", "loss_prob": "13,5 %", "p5_T": "366 719 $",
                   "p50_T": "479 312 $", "p95_T": "606 097 $", "width": "239 378 $"}
NVDA_METRICS_FR = {"vol": "9,8 %", "mdd": "-13,4 %", "loss_prob": "21,3 %", "p5_T": "316 971 $",
                   "p50_T": "487 578 $", "p95_T": "670 173 $", "width": "353 202 $"}


def _metrics(facts: dict, which: str) -> dict:
    return {k: _norm(v) for k, v in (facts.get("metrics", {}).get(which) or {}).items()}


@pytest.mark.parametrize("fid", ["objectif", "horizon", "risque"])
def test_facts_metrics_quote_only_the_factors_metrics_flow_b(fid, secs_trade):
    """§20.2 'Engine metrics quoted' and §20.4: current and proposed, pre-formatted in fr-CA."""
    keys = EXPECTED_METRICS[fid]
    f = secs_trade[fid]["facts"]
    assert _metrics(f, "current") == {k: BASE_METRICS_FR[k] for k in keys}
    assert _metrics(f, "proposed") == {k: NVDA_METRICS_FR[k] for k in keys}


def test_facts_preferences_quote_no_metrics(secs_trade, secs_base):
    for secs in (secs_trade, secs_base):
        assert _metrics(secs["preferences"]["facts"], "current") == {}
        assert _metrics(secs["preferences"]["facts"], "proposed") == {}


@pytest.mark.parametrize("fid", ["objectif", "horizon", "risque"])
def test_facts_metrics_flow_c_has_current_only(fid, secs_review, secs_base):
    keys = EXPECTED_METRICS[fid]
    assert _metrics(secs_review[fid]["facts"], "current") == {k: NVDA_METRICS_FR[k] for k in keys}
    assert _metrics(secs_review[fid]["facts"], "proposed") == {}
    assert _metrics(secs_base[fid]["facts"], "current") == {k: BASE_METRICS_FR[k] for k in keys}


def test_facts_english_locale_uses_en_ca_numbers(profile, checks_nvda, m_nvda):
    """en-CA: decimal point, no decimal comma; same underlying numbers."""
    secs = {s["factor"]: s for s in factors.build_sections(_ctx(profile, checks_nvda, m_cur=m_nvda, locale="en-CA"))}
    h8 = _entry(secs["risque"]["facts"], "H8", "violation")
    assert "13.4" in h8["value"] and "," not in h8["value"]
    assert "12.0" in h8["limit"]
    m = _metrics(secs["risque"]["facts"], "current")
    assert "9.8" in m["vol"] and "," not in m["vol"]
    assert "316" in _metrics(secs["horizon"]["facts"], "current")["p5_T"]
    assert secs["risque"]["status"] == "violation"


# ---------------------------------------------------------------- facts: declared vs derived

def _node(profile: dict, path: str):
    cur = profile
    for part in path.split("."):
        assert isinstance(cur, dict) and part in cur, path
        cur = cur[part]
    return cur


def _is_derived_node(node) -> bool:
    return isinstance(node, dict) and "by" in node


@pytest.mark.parametrize("fid", DISPLAY)
def test_facts_declared_and_derived_follow_the_profile(fid, secs_trade, profile):
    """§20.4: `declared` = plain user fields, `derived` = {value, by, because} objects; paths are the factor's FIELDS."""
    f = secs_trade[fid]["facts"]
    patterns = [p.split("[", 1)[0] for p in MODULES[fid].FIELDS]
    assert not set(f["declared"]) & set(f["derived"])
    for path in [*f["declared"], *f["derived"]]:
        assert any(fnmatch.fnmatchcase(path, pat) for pat in patterns), (fid, path)
    for path in f["declared"]:
        assert not _is_derived_node(_node(profile, path)), path
    for path, d in f["derived"].items():
        node = _node(profile, path)
        assert _is_derived_node(node), path
        assert {"value", "by", "because"} <= set(d), (path, d)
        assert d["by"].split(":", 1)[0] == str(node["by"]).split(":", 1)[0]
        assert d["by"].split(":", 1)[0] in ("rule", "llm")
        assert isinstance(d["because"], list) and all(isinstance(b, str) for b in d["because"])


def test_facts_risque_declared_and_derived(secs_trade):
    f = secs_trade["risque"]["facts"]
    assert f["declared"]["risk.declared"] == "low_moderate"
    assert _norm(f["declared"]["risk.max_acceptable_drawdown_pct"]) == "-12 %"
    eff = f["derived"]["risk.effective"]
    assert eff["value"] == "low_moderate"
    assert eff["by"].startswith("rule:")
    # §20.4: one level of the chain, formatted "path = value"
    assert "risk.declared = low_moderate" in eff["because"]
    assert "risk.capacity = low_moderate" in eff["because"]


def test_facts_llm_field_is_labelled_llm_never_rule(secs_trade):
    """Invariant 2/3: risk.behavioural is shown (FIELDS) but labelled `llm:`; it is never a `rule:` field."""
    d = secs_trade["risque"]["facts"]["derived"]
    assert "risk.behavioural" in d
    assert d["risk.behavioural"]["by"].startswith("llm:")
    assert d["risk.behavioural"]["value"] == "moderate_high"
    for path, entry in d.items():
        if path != "risk.behavioural":
            assert entry["by"].startswith("rule:"), path


def test_facts_horizon_declared_and_derived(secs_trade):
    f = secs_trade["horizon"]["facts"]
    assert "objectives.horizon_years" in f["declared"]
    eff = f["derived"]["objectives.effective_horizon_years"]
    assert "3,6" in _norm(str(eff["value"]))        # D1: 2026-11 -> 2030-06 = 43 months = 3.6 y, fr-CA formatted
    assert eff["by"].startswith("rule:")
    assert any(b.startswith("objectives.horizon_years = ") for b in eff["because"])


def test_facts_contraintes_declared_and_derived(secs_trade):
    f = secs_trade["contraintes"]["facts"]
    assert f["declared"]["identity.employer_sector"] == "Health Care"
    assert "preferences.asset_types_refused" in f["declared"]
    ef = f["derived"]["situation.emergency_fund_months"]
    assert "4,6" in _norm(str(ef["value"]))
    assert ef["by"].startswith("rule:")


def test_facts_objectif_and_preferences_declared(secs_trade):
    o = secs_trade["objectif"]["facts"]
    assert o["declared"]["objectives.primary"] == "capital_preservation_with_growth"
    assert o["derived"]["identity.life_stage"]["value"] == "pre_retirement"
    assert "preferences.home_bias_ok" in secs_trade["preferences"]["facts"]["declared"]


# ---------------------------------------------------------------- purity and view-ness

def test_build_sections_is_pure_and_deterministic(ctx_trade):
    before = copy.deepcopy(ctx_trade)
    a = factors.build_sections(ctx_trade)
    b = factors.build_sections(ctx_trade)
    assert json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)
    assert ctx_trade == before


@pytest.mark.parametrize("fid", DISPLAY)
def test_module_facts_is_the_section_facts(fid, ctx_trade, secs_trade):
    """§20.3: build_sections only assembles what each factor module's facts() builds."""
    assert MODULES[fid].facts(ctx_trade) == secs_trade[fid]["facts"]


# ---------------------------------------------------------------- §20.5 template_section

CASES = ["secs_trade", "secs_review", "secs_base"]


def _refs_resolve(ref: str, facts: dict) -> bool:
    """V1: a ref is a check id in facts.checks, an event_id in facts.evidence_events, or a dotted key path in facts."""
    if ref in {c["id"] for c in facts["checks"]}:
        return True
    if ref in {e["event_id"] for e in facts["evidence_events"]}:
        return True
    return _resolve(ref, facts)


def _v1_v3_v4_v5(out: dict, facts: dict) -> list[str]:
    """Local re-statement of V1, V3, V4, V5 (§20.5); explain.verify_section is P0.8."""
    fails: list[str] = []
    segs = out["segments"]
    if not 1 <= len(segs) <= 4:                                                    # V5
        fails.append(f"V5 count {len(segs)}")
    llm_field = any(d["by"].startswith("llm:") for d in facts["derived"].values())
    refs_seen: set[str] = set()
    for seg in segs:
        if seg["kind"] not in SEGMENT_KINDS:
            fails.append(f"V5 kind {seg['kind']}")
        if len(seg["text"]) > 300 or not seg["text"].strip():
            fails.append("V5 length")
        if seg["kind"] == "inference" and not (llm_field or facts["evidence_events"]):
            fails.append("V5 inference not allowed")
        if not seg["refs"]:                                                        # V1
            fails.append("V1 segment without ref")
        for r in seg["refs"]:
            refs_seen.add(r)
            if not _refs_resolve(r, facts):
                fails.append(f"V1 unresolved ref {r}")
        low = seg["text"].lower()                                                  # V4
        fails += [f"V4 {p}" for p in explain.ADVISORY_BLOCKLIST if p in low]
    for c in facts["checks"]:                                                      # V3
        if c["status"] in ("violation", "warning") and c["id"] not in refs_seen:
            fails.append(f"V3 uncovered {c['id']}")
    return fails


@pytest.mark.parametrize("fid", DISPLAY)
@pytest.mark.parametrize("case", CASES)
def test_template_section_passes_v1_v3_v4_v5_and_numcheck(case, fid, request):
    """§20.5/§20.8: the template always passes V1-V5 (V2 = explain.numcheck against its own facts)."""
    facts = request.getfixturevalue(case)[fid]["facts"]
    out = explain.template_section(facts)
    assert isinstance(out["segments"], list)
    assert _v1_v3_v4_v5(out, facts) == []
    assert explain.numcheck(_all_text(out), facts) == []                          # V2
    for seg in out["segments"]:
        assert explain.numcheck(seg["text"], facts) == []


@pytest.mark.parametrize("fid", DISPLAY)
def test_template_section_passes_in_english(fid, profile, checks_nvda, m_nvda):
    secs = {s["factor"]: s for s in factors.build_sections(_ctx(profile, checks_nvda, m_cur=m_nvda, locale="en-CA"))}
    facts = secs[fid]["facts"]
    out = explain.template_section(facts)
    assert _v1_v3_v4_v5(out, facts) == []
    assert explain.numcheck(_all_text(out), facts) == []


@pytest.mark.parametrize("fid", DISPLAY)
def test_template_section_is_deterministic_and_pure(fid, secs_trade):
    facts = secs_trade[fid]["facts"]
    frozen = copy.deepcopy(facts)
    a = explain.template_section(facts)
    b = explain.template_section(copy.deepcopy(facts))
    assert json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)
    assert facts == frozen


def test_template_section_covers_every_violation_and_quotes_only_its_facts(secs_trade):
    """V3 on the three violating sections: each violation check id is referenced; the text is non-empty."""
    for fid, expected in (("horizon", {"H9"}), ("risque", {"H4", "H7", "H8"}), ("contraintes", {"H3", "H10"})):
        facts = secs_trade[fid]["facts"]
        out = explain.template_section(facts)
        refs = {r for seg in out["segments"] for r in seg["refs"]}
        assert expected <= refs, (fid, refs)
        assert all(seg["text"].strip() for seg in out["segments"])


def test_template_section_says_what_it_cannot_verify(secs_base):
    """Baseline contraintes (H6, H10 unknown): the template carries an `unknown` segment (D3: never silent)."""
    facts = secs_base["contraintes"]["facts"]
    out = explain.template_section(facts)
    assert any(seg["kind"] == "unknown" for seg in out["segments"])
    refs = {r for seg in out["segments"] for r in seg["refs"]}
    assert {"H6", "H10"} & refs


@pytest.mark.parametrize("fid", ["objectif", "preferences"])
def test_template_section_for_pass_section_makes_no_claim_of_violation(fid, secs_trade):
    facts = secs_trade[fid]["facts"]
    out = explain.template_section(facts)
    assert facts["checks"] == [] and facts["unknowns"] == []
    assert not any(seg["kind"] == "unknown" for seg in out["segments"])
    assert _v1_v3_v4_v5(out, facts) == []


def test_template_section_has_no_buy_sell_advice(secs_trade):
    """Invariant 4: no recommendation language in any template section, FR or EN."""
    for s in secs_trade.values():
        text = _all_text(explain.template_section(s["facts"])).lower()
        assert not any(p in text for p in explain.ADVISORY_BLOCKLIST)
