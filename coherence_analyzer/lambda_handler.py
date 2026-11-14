"""AWS Lambda entry point for the Step Functions version of Flow B (SPEC §4, §14; P2.15).

One Lambda hosts every Flow B step; the state machine (`deploy/aws/flow_b.asl.json`) says which one through
`event["step"]`. The steps are the pipeline's own dict-in / dict-out functions, so a Step Functions run and
`pipeline.check` compute the same report.

Event shapes (JSON only):
  * `b1_parse_intent`: `{"step", "fixture_dir", "intent_id", "auto_confirm"}`. The returned state also carries
    `auto_confirm`, which the B2 Choice reads.
  * every other step: `{"step", "state": {...}}` where `state` is the previous step's output (for B6, the B4 state
    merged with the two B5 deltas by `States.JsonMerge` in the state machine).
  * the two B5 steps (`b5_analyse_current`, `b5_analyse_proposed`) return only the keys they add (one Parallel branch
    each); B6 adds `has_violations` (the B7 Choice variable); B9 returns `{"report"}` only (the audit record is in the
    store), which keeps the execution output well under the 256 KiB Step Functions payload limit. Every other step
    returns the whole state, as in `pipeline.check`.

B2 is a Choice in the state machine, not a Task. Reaching `b3_refresh` means the Choice saw `auto_confirm: true`, so the
handler materialises the confirmation (`pipeline.b2_confirm(state, True)`: the fixture intent, which is user-declared
data) before B3. A state that was not confirmed never gets past this point.

B7 is skipped by a Choice when the proposed portfolio has no violation. B9 then fills in what `pipeline.b7_resize`
returns in that case (`alternatives: []`, `resizer: None`), so both paths give `pipeline.check`'s report.

The store backend (local files or S3) comes from the environment (`COHERENCE_ANALYZER_STORE`, `..._S3_BUCKET`,
`..._S3_PREFIX`, read by `store.py`); the Lambda is deployed with `s3`. This module reads no clock: the only clock reads
are the pipeline's (audit timestamps, run id), as in the CLI.
"""
from __future__ import annotations

import copy
from typing import Any, Callable

from . import pipeline


def _b1(event: dict) -> dict:
    for key in ("fixture_dir", "intent_id"):
        if key not in event:
            raise ValueError(f"step b1_parse_intent needs {key!r} in the event")
    state = pipeline.b1_parse_intent(event["fixture_dir"], event["intent_id"])
    state["auto_confirm"] = bool(event.get("auto_confirm", False))
    return state


def _state_of(event: dict) -> dict:
    state = event.get("state")
    if not isinstance(state, dict):
        raise ValueError(f"step {event.get('step')!r} needs a 'state' object in the event")
    return copy.deepcopy(state)


def _b3(event: dict) -> dict:
    state = _state_of(event)
    if "intent" not in state:                       # B2 (the Choice) let the run through: confirm the intent
        if state.get("auto_confirm") is not True:
            raise ValueError("b3_refresh needs a confirmed intent: the run was not auto-confirmed (B2)")
        state = pipeline.b2_confirm(state, True)
    return pipeline.b3_refresh(state)


def _b6(event: dict) -> dict:
    state = pipeline.b6_checks_and_diff(_state_of(event))
    return {**state, "has_violations": any(c["status"] == "violation" for c in state["checks_proposed"])}


def _b9(event: dict) -> dict:
    state = _state_of(event)
    state.setdefault("alternatives", [])            # B7 skipped by the Choice: pipeline.b7_resize's no-violation result
    state.setdefault("resizer", None)
    return {"report": pipeline.b9_save(state)["report"]}


def _on_state(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
    return lambda event: fn(_state_of(event))


# The only dispatchable steps: Flow B's, by the name of the pipeline function. Nothing else of the pipeline is reachable.
STEPS: dict[str, Callable[[dict], dict]] = {
    "b1_parse_intent": _b1,
    "b3_refresh": _b3,
    "b4_apply": _on_state(pipeline.b4_apply),
    "b5_analyse_current": _on_state(pipeline.b5_analyse_current),
    "b5_analyse_proposed": _on_state(pipeline.b5_analyse_proposed),
    "b6_checks_and_diff": _b6,
    "b7_resize": _on_state(pipeline.b7_resize),
    "b8_explain": _on_state(pipeline.b8_explain),
    "b9_save": _b9,
}


def handler(event: dict, context: Any) -> dict:
    """Run the Flow B step named by `event["step"]` and return its JSON dict. Unknown or missing step: error."""
    if not isinstance(event, dict) or "step" not in event:
        raise ValueError("the event needs a 'step' (one of: " + ", ".join(sorted(STEPS)) + ")")
    step = event["step"]
    if not isinstance(step, str) or step not in STEPS:
        raise ValueError(f"unknown step {step!r}; expected one of: " + ", ".join(sorted(STEPS)))
    return STEPS[step](event)
