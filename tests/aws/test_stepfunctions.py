"""Step Functions version of Flow B (P2.15): ASL state machine + Lambda step handler + CloudFormation. Offline.

Sources: SPEC §4 Flow B (B1-B9), §14 AWS footprint (Step Functions, Lambda: P2), §10 persistence and audit, §17 step 15;
docs/spec-decisions.md D6 (model id in the audit record, no temperature), D7 point 3 (Flow B is P2); CLAUDE.md invariants.

What is pinned:
  * `deploy/aws/flow_b.asl.json`: structure (reachability, no dead end), every Task invokes ONE Lambda with a `step` name,
    Retry on the Lambda service errors + Catch ending in a Fail that carries the error, B2 = Choice on `auto_confirm`,
    B5 = Parallel (current / proposed), B7 = Choice on violations.
  * `coherence_analyzer/lambda_handler.py`: `handler(event, context)` dispatches `event["step"]`, unknown step raises.
  * the state machine is RUN LOCALLY by a small test-only ASL interpreter (below) that calls `lambda_handler.handler` in
    mock mode for every Task; the final report must equal `pipeline.check(robert, in_0001, auto_confirm=True)`.
  * `deploy/aws/coherence-analyzer.cfn.json`: new Lambda, roles, state machine, output; existing ids/properties unchanged.
  * README "AWS deploy": package/update instructions + a mermaid diagram.

Contract chosen by these tests (the spec does not name it):
  * the execution input is `{"fixture_dir": str, "intent_id": str, "auto_confirm": bool}` (the arguments of
    `pipeline.check`); the execution output is the Flow B report, or a state dict holding it under `report`; with
    `auto_confirm` false it is the draft (`draft`, `draft_gate`, `intent`, `event`, as `pipeline.check` returns it);
  * every Task payload is a dict with a literal `step` string; step names start with `b<N>_` (B1..B9) like the
    pipeline's own step functions; the same single Lambda (a `${Placeholder}` name, substituted by CloudFormation) is
    invoked everywhere, through `arn:aws:states:::lambda:invoke` (Parameters.FunctionName + Parameters.Payload) or a
    direct function Resource (the Parameters are then the payload);
  * the interpreter supports the ASL subset: Task, Pass, Wait, Choice, Parallel, Succeed, Fail; InputPath, Parameters,
    ResultSelector, ResultPath, OutputPath; `$.a.b[0]` paths, `$$.Execution.Input` / `$$.Task.Token` / `$$.State.Name`;
    intrinsics `States.JsonMerge` and `States.Array`; Retry / Catch. Anything else fails the test loudly.

No network, no real AWS call, nothing deployed; COHERENCE_ANALYZER_LLM=mock; no clock value is asserted.
"""
from __future__ import annotations

import copy
import fnmatch
import hashlib
import importlib
import json
import os
import re
import shutil
import types
from pathlib import Path

import pytest

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import config, pipeline, store  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures" / "robert"
ASL_PATH = ROOT / "deploy" / "aws" / "flow_b.asl.json"
CFN_PATH = ROOT / "deploy" / "aws" / "coherence-analyzer.cfn.json"
README = ROOT / "README.md"
TAGS = ROOT / "data" / "meta" / "activity_tags.csv"
TAGS_BYTES = TAGS.read_bytes() if TAGS.exists() else None
INTENT_ID = "in_0001"
EM_DASH = "\u2014"
SFN_PAYLOAD_LIMIT = 262_144                       # Step Functions: 256 KiB per state input / output (AWS quota)
ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
LAMBDA_SERVICE_ERRORS = ("Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException",
                         "Lambda.TooManyRequestsException")        # the list AWS documents for Lambda Task retries
FLOW_B_STEP_NUMBERS = {1, 3, 4, 5, 6, 7, 8, 9}                      # B2 is a Choice, not a Task

# sha256[:16] of the canonical JSON (sorted keys, compact) of each element of the template as deployed by the team
EXISTING_PARAMETERS = {"ModelArn": "c3eddd9183fa298f", "S3Prefix": "92281cb50fc3ed8b",
                       "FoundationModelId": "bfeeb35c1c39c231"}
EXISTING_RESOURCES = {"DataBucket": "76b0afe85239c62d", "DataBucketPolicy": "6d099ede0f4d3c84",
                      "ExplainerGuardrail": "82ec17bf2e0d180e", "ExplainerGuardrailVersion": "e1f0e73ff4e7a120",
                      "AppAccessPolicy": "2767e64fc145f617"}
EXISTING_OUTPUTS = {"BucketName": "add40c8d692a5954", "GuardrailId": "4ae704c5b4d7c77b",
                    "GuardrailVersion": "6d314d252560baf3", "AccessPolicyArn": "81a6d828bb9863a9"}


# ---------------------------------------------------------------- the test-only ASL interpreter

class StateError(Exception):
    """An ASL error (Lambda failure, States.*, or a Fail state)."""

    def __init__(self, error: str, cause: str = ""):
        super().__init__(f"{error}: {cause}")
        self.error, self.cause = error, cause


class LambdaServiceError(StateError):
    """What a flaky Lambda service looks like to the state machine (retryable)."""


_PATH_TOKEN = re.compile(r"\.([A-Za-z0-9_\-]+)|\[(\d+)\]|\['([^']+)'\]")


def _path_get(path: str, data, ctx):
    if path == "$":
        return data
    if path.startswith("$$"):
        root, rest = ctx, path[2:]
    elif path.startswith("$"):
        root, rest = data, path[1:]
    else:
        raise AssertionError(f"interpreter: unsupported path {path!r}")
    pos, cur = 0, root
    while pos < len(rest):
        m = _PATH_TOKEN.match(rest, pos)
        if not m:
            raise AssertionError(f"interpreter: unsupported JSONPath {path!r}")
        pos = m.end()
        key = m.group(1) or m.group(3)
        try:
            cur = cur[key] if key is not None else cur[int(m.group(2))]
        except (KeyError, IndexError, TypeError):
            raise StateError("States.Runtime", f"path {path!r} not found")
    return cur


def _path_set(path: str, data, value):
    """ResultPath semantics: `$` replaces, `$.a.b` sets a copy of the input."""
    if path == "$":
        return value
    keys = path[2:].split(".") if path.startswith("$.") else None
    if not keys or any(not re.fullmatch(r"[A-Za-z0-9_\-]+", k) for k in keys):
        raise AssertionError(f"interpreter: unsupported ResultPath {path!r}")
    out = copy.deepcopy(data) if isinstance(data, dict) else {}
    cur = out
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value
    return out


def _split_args(text: str) -> list[str]:
    args, depth, cur, quote = [], 0, "", False
    for ch in text:
        if ch == "'":
            quote = not quote
        if not quote and ch == "(":
            depth += 1
        if not quote and ch == ")":
            depth -= 1
        if ch == "," and depth == 0 and not quote:
            args.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    return args


def _expr(text: str, data, ctx):
    text = text.strip()
    if text.startswith("$"):
        return _path_get(text, data, ctx)
    if text in ("true", "false"):
        return text == "true"
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return float(text) if "." in text else int(text)
    m = re.fullmatch(r"States\.(\w+)\((.*)\)", text, re.S)
    if not m:
        raise AssertionError(f"interpreter: unsupported intrinsic expression {text!r}")
    name, args = m.group(1), [_expr(a, data, ctx) for a in _split_args(m.group(2))]
    if name == "JsonMerge":
        assert len(args) == 3 and args[2] is False, "interpreter: only shallow States.JsonMerge(a, b, false)"
        return {**args[0], **args[1]}
    if name == "Array":
        return list(args)
    raise AssertionError(f"interpreter: unsupported intrinsic States.{name}")


def _apply_template(tpl, data, ctx):
    if isinstance(tpl, dict):
        out = {}
        for k, v in tpl.items():
            if k.endswith(".$"):
                assert isinstance(v, str), f"{k} must be a string expression"
                out[k[:-2]] = _expr(v, data, ctx)
            else:
                out[k] = _apply_template(v, data, ctx)
        return out
    if isinstance(tpl, list):
        return [_apply_template(v, data, ctx) for v in tpl]
    return tpl


_COMPARATORS = {
    "Equals": lambda a, b: a == b, "LessThan": lambda a, b: a < b, "GreaterThan": lambda a, b: a > b,
    "LessThanEquals": lambda a, b: a <= b, "GreaterThanEquals": lambda a, b: a >= b,
}


def _rule_true(rule: dict, data, ctx) -> bool:
    if "And" in rule:
        return all(_rule_true(r, data, ctx) for r in rule["And"])
    if "Or" in rule:
        return any(_rule_true(r, data, ctx) for r in rule["Or"])
    if "Not" in rule:
        return not _rule_true(rule["Not"], data, ctx)
    var = rule["Variable"]
    try:
        value, present = _path_get(var, data, ctx), True
    except StateError:
        value, present = None, False
    for op, arg in rule.items():
        if op in ("Variable", "Next"):
            continue
        if op == "IsPresent":
            return present == arg
        if op == "IsNull":
            return (present and value is None) == arg
        if not present:
            return False
        if op == "IsBoolean":
            return isinstance(value, bool) == arg
        if op == "IsNumeric":
            return (isinstance(value, (int, float)) and not isinstance(value, bool)) == arg
        if op == "IsString":
            return isinstance(value, str) == arg
        if op == "StringMatches":
            return fnmatch.fnmatchcase(value, arg)
        m = re.fullmatch(r"(String|Numeric|Boolean)(\w+?)(Path)?", op)
        if not m or m.group(2) not in _COMPARATORS:
            raise AssertionError(f"interpreter: unsupported Choice operator {op!r}")
        other = _path_get(arg, data, ctx) if m.group(3) else arg
        if m.group(1) == "Boolean" and not isinstance(value, bool):
            return False
        return bool(_COMPARATORS[m.group(2)](value, other))
    raise AssertionError(f"interpreter: Choice rule without a comparator: {rule}")


class Machine:
    """Runs one ASL document. `invoke(function_ref, payload) -> dict` is the Lambda; every call is recorded."""

    def __init__(self, invoke):
        self.invoke = invoke
        self.calls: list[dict] = []          # {"state", "function", "payload"} per Lambda invocation (retries included)
        self.steps: list[str] = []           # `step` of every Task that succeeded, in execution order
        self.visited: list[str] = []         # state names, in execution order (branches included)

    # -- plumbing
    @staticmethod
    def _size(doc, what: str) -> None:
        n = len(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
        assert n <= SFN_PAYLOAD_LIMIT, f"{what}: {n} bytes exceeds the 256 KiB Step Functions payload limit"

    def _finish(self, st: dict, raw_in, result, ctx):
        if "ResultSelector" in st:
            result = _apply_template(st["ResultSelector"], result, ctx)
        rp = st.get("ResultPath", "$")
        out = raw_in if rp is None else _path_set(rp, raw_in, result)
        op = st.get("OutputPath", "$")
        out = {} if op is None else _path_get(op, out, ctx)
        self._size(out, f"state output of {ctx['State']['Name']}")
        return out

    @staticmethod
    def _matches(error_equals: list[str], error: str) -> bool:
        return "States.ALL" in error_equals or error in error_equals or (
            "States.TaskFailed" in error_equals and not error.startswith("States."))

    # -- state types
    def _task(self, name: str, st: dict, data, ctx):
        eff = {} if st.get("InputPath", "$") is None else _path_get(st.get("InputPath", "$"), data, ctx)
        resource = st["Resource"]
        callback = resource.endswith(".waitForTaskToken")
        ctx = {**ctx, "Task": {"Token": "TEST-TASK-TOKEN"}} if callback else ctx
        params = _apply_template(st["Parameters"], eff, ctx) if "Parameters" in st else eff
        if resource.startswith("arn:aws:states:::lambda:invoke"):
            assert isinstance(params, dict) and {"FunctionName", "Payload"} <= set(params), params
            function, payload, wrapped = params["FunctionName"], params["Payload"], True
        else:
            function, payload, wrapped = resource, params, False
        assert isinstance(payload, dict) and isinstance(payload.get("step"), str), \
            f"{name}: the Lambda payload must be a dict with a `step` string, got {payload!r}"
        self._size(payload, f"Lambda payload of {name}")
        attempts: dict[int, int] = {}
        while True:
            self.calls.append({"state": name, "function": function, "payload": copy.deepcopy(payload)})
            try:
                out = self.invoke(function, copy.deepcopy(payload))
                break
            except StateError as exc:
                hit = next((i for i, r in enumerate(st.get("Retry", [])) if self._matches(r["ErrorEquals"], exc.error)),
                           None)
                if hit is None or attempts.get(hit, 0) >= st["Retry"][hit].get("MaxAttempts", 3):
                    raise
                attempts[hit] = attempts.get(hit, 0) + 1
        self.steps.append(payload["step"])
        result = ({"ExecutedVersion": "$LATEST", "Payload": out, "StatusCode": 200} if wrapped else out)
        return self._finish(st, data, result, ctx)

    def _parallel(self, name: str, st: dict, data, ctx):
        eff = {} if st.get("InputPath", "$") is None else _path_get(st.get("InputPath", "$"), data, ctx)
        params = _apply_template(st["Parameters"], eff, ctx) if "Parameters" in st else eff
        results = [self.run(branch, copy.deepcopy(params), ctx) for branch in st["Branches"]]
        return self._finish(st, data, results, ctx)

    def _pass(self, name: str, st: dict, data, ctx):
        eff = {} if st.get("InputPath", "$") is None else _path_get(st.get("InputPath", "$"), data, ctx)
        result = st["Result"] if "Result" in st else (
            _apply_template(st["Parameters"], eff, ctx) if "Parameters" in st else eff)
        return self._finish(st, data, result, ctx)

    # -- the loop
    def run(self, definition: dict, data, ctx: dict):
        name = definition["StartAt"]
        for _ in range(500):
            st = definition["States"][name]
            kind = st["Type"]
            ctx = {**ctx, "State": {"Name": name}}
            self.visited.append(name)
            if kind == "Succeed":
                return self._finish(st, data, data, ctx)
            if kind == "Fail":
                err = _path_get(st["ErrorPath"], data, ctx) if "ErrorPath" in st else st.get("Error", "")
                cause = _path_get(st["CausePath"], data, ctx) if "CausePath" in st else st.get("Cause", "")
                raise StateError(str(err), str(cause))
            if kind == "Choice":
                view = _path_get(st.get("InputPath", "$"), data, ctx)
                nxt = next((r["Next"] for r in st["Choices"] if _rule_true(r, view, ctx)), st.get("Default"))
                if nxt is None:
                    raise StateError("States.NoChoiceMatched", name)
                name = nxt
                continue
            try:
                if kind == "Task":
                    data = self._task(name, st, data, ctx)
                elif kind == "Parallel":
                    data = self._parallel(name, st, data, ctx)
                elif kind == "Pass":
                    data = self._pass(name, st, data, ctx)
                elif kind == "Wait":
                    pass
                else:
                    raise AssertionError(f"interpreter: unsupported state type {kind!r} in {name}")
            except StateError as exc:
                catcher = next((c for c in st.get("Catch", []) if self._matches(c["ErrorEquals"], exc.error)), None)
                if catcher is None:
                    raise
                info = {"Error": exc.error, "Cause": exc.cause}
                rp = catcher.get("ResultPath", "$")
                data = info if rp == "$" else (data if rp is None else _path_set(rp, data, info))
                name = catcher["Next"]
                continue
            if st.get("End"):
                return data
            name = st["Next"]
        raise AssertionError("interpreter: more than 500 transitions (loop in the state machine?)")


def _handler_module():
    return importlib.import_module("coherence_analyzer.lambda_handler")


def lambda_invoke(function: str, payload: dict) -> dict:
    """The real handler, as Lambda would call it: JSON in, JSON out, errors as (type name, message)."""
    handler = _handler_module().handler
    context = types.SimpleNamespace(aws_request_id="test-request", function_name="flow-b-test")
    try:
        out = handler(json.loads(json.dumps(payload)), context)
    except StateError:
        raise
    except Exception as exc:                                        # noqa: BLE001 (this is what Lambda does)
        raise StateError(type(exc).__name__, json.dumps({"errorMessage": str(exc), "errorType": type(exc).__name__}))
    assert isinstance(out, dict), f"the handler must return a dict, got {type(out).__name__}"
    assert json.loads(json.dumps(out)) == out, "the handler's return value must be plain JSON"
    return out


def load_asl() -> dict:
    return json.loads(ASL_PATH.read_text(encoding="utf-8"))


def _tape_key(function: str, payload: dict) -> str:
    return json.dumps({"function": function, "payload": payload}, sort_keys=True, ensure_ascii=False)


class Recorder:
    """The real Lambda, with a tape of every (payload -> output): later tests replay the tape instead of recomputing the
    engine, and fail or flake chosen steps (the outputs are what the real handler returned)."""

    def __init__(self):
        self.tape: dict[str, dict] = {}

    def __call__(self, function: str, payload: dict) -> dict:
        out = lambda_invoke(function, payload)
        self.tape[_tape_key(function, payload)] = copy.deepcopy(out)
        return out

    def replay(self, *, fail_at: int | None = None, error: tuple[str, str] | None = None, flaky: str | None = None):
        """An invoke function serving the tape. `fail_at` = B number whose Task raises `error` (type, message);
        `flaky` = a Lambda service error name raised once before each Task's first successful call."""
        seen: set[str] = set()

        def invoke(function: str, payload: dict) -> dict:
            if fail_at is not None and _step_no(payload["step"]) == fail_at:
                name, message = error
                raise StateError(name, json.dumps({"errorMessage": message, "errorType": name}))
            key = _tape_key(function, payload)
            if flaky and key not in seen:
                seen.add(key)
                raise LambdaServiceError(flaky, "injected service error")
            return copy.deepcopy(self.tape[key])

        return invoke


def execute(execution_input: dict, invoke=lambda_invoke, asl: dict | None = None):
    """(output, machine). Raises StateError when the execution fails."""
    machine = Machine(invoke)
    ctx = {"Execution": {"Input": execution_input}}
    out = machine.run(asl or load_asl(), copy.deepcopy(execution_input), ctx)
    return out, machine


def _report_of(out: dict) -> dict:
    return out["report"] if "report" in out and "verdict" not in out else out


def _normalise(doc) -> str:
    text = json.dumps(doc, sort_keys=True, ensure_ascii=False)
    for rid in set(re.findall(r'"run_id": "([0-9A-Z]{26})"', text)):
        text = text.replace(rid, "<RUN_ID>")
    return ISO_TS.sub("<TS>", text)


class _Workdir:
    """chdir + store dir into tmp, so the local store of a run never touches the repo."""

    def __init__(self, path: Path):
        self.path, self.mp = path, pytest.MonkeyPatch()

    def __enter__(self):
        self.path.mkdir(parents=True, exist_ok=True)
        self.mp.chdir(self.path)
        self.mp.setenv("COHERENCE_ANALYZER_STORE_DIR", str(self.path))
        return self.path

    def __exit__(self, *exc):
        self.mp.undo()


def _variant_fixture(dest: Path) -> Path:
    """Robert with a benign trade (XBB.TO to 24 % from ZST.TO): no hard violation, so no resizer."""
    shutil.copytree(FIX, dest)
    intents = json.loads((dest / "intents.json").read_text(encoding="utf-8"))
    intents[0].update(ticker="XBB.TO", target_weight_pct=24, funding={"kind": "specific", "ticker": "ZST.TO"})
    (dest / "intents.json").write_text(json.dumps(intents), encoding="utf-8")
    return dest


def _input(fixture: Path = FIX, *, auto_confirm: bool = True, intent_id: str = INTENT_ID) -> dict:
    return {"fixture_dir": str(fixture), "intent_id": intent_id, "auto_confirm": auto_confirm}


# ---------------------------------------------------------------- module-scoped runs (each is a full offline Flow B)

@pytest.fixture(scope="module")
def pipeline_run(tmp_path_factory):
    with _Workdir(tmp_path_factory.mktemp("pipeline")):
        return pipeline.check(FIX, INTENT_ID, auto_confirm=True)


@pytest.fixture(scope="module")
def sfn_run(tmp_path_factory):
    wd = tmp_path_factory.mktemp("sfn1")
    rec = Recorder()
    with _Workdir(wd):
        out, machine = execute(_input(), invoke=rec)
    return {"out": out, "report": _report_of(out), "machine": machine, "dir": wd, "recorder": rec}


@pytest.fixture(scope="module")
def sfn_run_again(tmp_path_factory):
    with _Workdir(tmp_path_factory.mktemp("sfn2")):
        out, machine = execute(_input())
    return {"out": out, "report": _report_of(out), "machine": machine}


@pytest.fixture(scope="module")
def variant(tmp_path_factory):
    """Benign trade: the pipeline's report and the state machine's, each in its own store."""
    base = tmp_path_factory.mktemp("variant")
    fx = _variant_fixture(base / "robert")
    with _Workdir(base / "p"):
        expected = pipeline.check(fx, INTENT_ID, auto_confirm=True)
    with _Workdir(base / "s"):
        out, machine = execute(_input(fx))
    return {"expected": expected, "report": _report_of(out), "machine": machine}


# ---------------------------------------------------------------- ASL static helpers

def _scopes(defn: dict, where: str = "top"):
    """(where, {StartAt, States}) for the machine and, recursively, every Parallel branch."""
    yield where, defn
    for name, st in defn["States"].items():
        for i, branch in enumerate(st.get("Branches", [])):
            yield from _scopes(branch, f"{where}/{name}[{i}]")


def _tasks(defn: dict):
    """(name, state) of every Task, branches included."""
    for _, scope in _scopes(defn):
        for name, st in scope["States"].items():
            if st["Type"] == "Task":
                yield name, st


def _edges(st: dict) -> list[str]:
    out = [st["Next"]] if "Next" in st else []
    out += [st["Default"]] if "Default" in st else []
    out += [r["Next"] for r in st.get("Choices", [])]
    out += [c["Next"] for c in st.get("Catch", [])]
    return out


def _reach(scope: dict, start: str, avoid: frozenset = frozenset()) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        cur = todo.pop()
        if cur in seen or cur in avoid:
            continue
        seen.add(cur)
        todo += _edges(scope["States"][cur])
    return seen


def _payload_template(st: dict) -> dict:
    params = st.get("Parameters", {})
    return params.get("Payload", {}) if st["Resource"].startswith("arn:aws:states:::lambda:invoke") else params


def _function_ref(st: dict) -> str:
    if st["Resource"].startswith("arn:aws:states:::lambda:invoke"):
        return st.get("Parameters", {}).get("FunctionName", "")
    return st["Resource"]


def _step_of(st: dict) -> str:
    step = _payload_template(st).get("step")
    assert isinstance(step, str), f"a Task payload needs a literal `step`, got {_payload_template(st)!r}"
    return step


def _step_no(step: str) -> int:
    m = re.match(r"b([1-9])_", step)
    assert m, f"step name {step!r} must start with b<N>_ (B1..B9)"
    return int(m.group(1))


def _task_states_by_step(defn: dict, number: int) -> list[tuple[str, dict]]:
    return [(n, st) for n, st in _tasks(defn) if _step_no(_step_of(st)) == number]


def _rule_variables(rule: dict) -> list[str]:
    out = [rule["Variable"]] if "Variable" in rule else []
    for key in ("And", "Or"):
        for sub in rule.get(key, []):
            out += _rule_variables(sub)
    if "Not" in rule:
        out += _rule_variables(rule["Not"])
    return out


def _choices_on(defn: dict, needle: str) -> list[tuple[str, dict]]:
    return [(n, st) for n, st in defn["States"].items()
            if st["Type"] == "Choice" and any(needle in v.lower() for r in st["Choices"] for v in _rule_variables(r))]


# ---------------------------------------------------------------- ASL: structure

def test_asl_file_is_json_states_language_with_valid_structure():
    """SPEC §14 (Step Functions, P2): flow_b.asl.json is JSON ASL; every Next / Default / Choice / Catch target exists, the
    state types are known, and no em dash appears anywhere."""
    raw = ASL_PATH.read_text(encoding="utf-8")
    assert EM_DASH not in raw
    asl = json.loads(raw)
    assert "AWSTemplateFormatVersion" not in asl              # tests/test_store_s3.py: ONE CloudFormation JSON in deploy/aws
    assert asl["StartAt"] in asl["States"]
    for where, scope in _scopes(asl):
        assert scope["StartAt"] in scope["States"], where
        for name, st in scope["States"].items():
            assert st["Type"] in ("Task", "Pass", "Wait", "Choice", "Parallel", "Succeed", "Fail"), (where, name)
            for target in _edges(st):
                assert target in scope["States"], f"{where}/{name} points at the unknown state {target!r}"
            if st["Type"] in ("Task", "Pass", "Wait", "Parallel"):
                assert ("Next" in st) != bool(st.get("End")), f"{where}/{name}: exactly one of Next / End"
            if st["Type"] == "Choice":
                assert st["Choices"] and all("Next" in r for r in st["Choices"])
            if st["Type"] == "Parallel":
                assert len(st["Branches"]) >= 1


def test_asl_every_state_is_reachable_from_start_at():
    """No orphan state, at the top level and inside every Parallel branch (edges: Next, Default, Choices, Catch)."""
    for where, scope in _scopes(load_asl()):
        assert _reach(scope, scope["StartAt"]) == set(scope["States"]), \
            f"{where}: unreachable states {sorted(set(scope['States']) - _reach(scope, scope['StartAt']))}"


def test_asl_success_end_is_reachable_and_no_state_is_a_dead_end():
    """A Succeed state or an `End: true` state is reachable from StartAt, and a non-terminal state always has a way out."""
    asl = load_asl()
    top = _reach(asl, asl["StartAt"])
    assert any(asl["States"][n]["Type"] == "Succeed" or asl["States"][n].get("End") for n in top), \
        "no Succeed / End state reachable from StartAt"
    assert any(st["Type"] == "Fail" for st in asl["States"].values()), "no Fail state for the Catch blocks"
    for where, scope in _scopes(asl):
        ends = [n for n, st in scope["States"].items() if st["Type"] in ("Succeed", "Fail") or st.get("End")]
        assert ends, f"{where}: no terminal state"
        for name, st in scope["States"].items():
            if name not in ends:
                assert _edges(st), f"{where}/{name} is a dead end"
            if st["Type"] == "Choice":
                assert st.get("Default"), f"{where}/{name}: a Choice needs a Default (no States.NoChoiceMatched)"


def test_asl_every_task_invokes_one_lambda_with_a_step_name():
    """Task: every Task (Parallel branches included) invokes the SAME Lambda, a `${Placeholder}` that CloudFormation
    substitutes, and its payload carries a literal `step` named b<N>_... for B1, B3, B4, B5, B6, B7, B8, B9."""
    asl = load_asl()
    tasks = list(_tasks(asl))
    assert tasks
    refs = {_function_ref(st) for _, st in tasks}
    assert len(refs) == 1, f"every Task must invoke the one Lambda, got {sorted(refs)}"
    (ref,) = refs
    # SPEC-AMBIGUITY: the function is injected by DefinitionSubstitutions, never a hard-coded ARN or account id.
    assert re.fullmatch(r"\$\{[A-Za-z0-9]+\}(:\$LATEST)?", ref), ref
    for name, st in tasks:
        assert st["Resource"].startswith("arn:aws:states:::lambda:invoke") or re.fullmatch(r"\$\{[A-Za-z0-9]+\}", st["Resource"])
        if st["Resource"].startswith("arn:aws:states:::lambda:invoke"):
            assert set(st["Parameters"]) >= {"FunctionName", "Payload"}, name
    numbers = {_step_no(_step_of(st)) for _, st in tasks}
    assert FLOW_B_STEP_NUMBERS <= numbers, f"steps missing for B{sorted(FLOW_B_STEP_NUMBERS - numbers)}"
    # SPEC-AMBIGUITY: B2 is a Choice (task note), so no Task may carry a step b2_ unless it is the optional callback.
    for name, st in tasks:
        if _step_no(_step_of(st)) == 2:
            assert st["Resource"].endswith(".waitForTaskToken"), f"{name}: B2 is a Choice; a b2 Task must be the callback"


def test_asl_every_task_has_retry_on_lambda_service_errors_and_a_catch_ending_in_fail():
    """Retry on the Lambda service errors (the four AWS documents) with at least two attempts, and a Catch of
    States.ALL that stores the error under ResultPath and leads (through Pass states only) to a Fail state. The
    Parallel state is guarded the same way."""
    asl = load_asl()
    guarded = [(w, n, st) for w, scope in _scopes(asl) for n, st in scope["States"].items()
               if st["Type"] in ("Task", "Parallel")]
    assert guarded
    for where, name, st in guarded:
        scope = dict(_scopes(asl))[where]
        if st["Type"] == "Task":
            retried = {e for r in st.get("Retry", []) if r.get("MaxAttempts", 3) >= 2 for e in r["ErrorEquals"]}
            # SPEC-AMBIGUITY: stricter reading of "Retry on Lambda service errors": all four AWS-listed errors.
            assert set(LAMBDA_SERVICE_ERRORS) <= retried, f"{where}/{name}: Retry must cover {LAMBDA_SERVICE_ERRORS}"
        catches = st.get("Catch", [])
        assert catches, f"{where}/{name}: no Catch"
        last = catches[-1]
        assert "States.ALL" in last["ErrorEquals"], f"{where}/{name}: the last Catch must take States.ALL"
        assert isinstance(last.get("ResultPath"), str) and last["ResultPath"].startswith("$."), \
            f"{where}/{name}: the Catch must keep the error under ResultPath"
        for c in catches:
            cur, seen = c["Next"], set()
            while scope["States"][cur]["Type"] == "Pass" and cur not in seen:
                seen.add(cur)
                cur = scope["States"][cur]["Next"]
            assert scope["States"][cur]["Type"] == "Fail", f"{where}/{name}: Catch -> {cur} does not end in a Fail state"
    for where, scope in _scopes(asl):
        for name, st in scope["States"].items():
            if st["Type"] == "Fail":
                assert "Error" in st or "ErrorPath" in st, f"{where}/{name}: a Fail state carries an error"
                assert "Cause" in st or "CausePath" in st, f"{where}/{name}: a Fail state carries a cause"


def test_asl_b2_confirmation_is_a_choice_on_the_auto_confirm_flag():
    """SPEC §4 B2: a Choice on `$.auto_confirm` sits between B1 and B3. Confirmed -> B3; otherwise the run ends (with the
    draft) before any B3+ Task (checked by the behavioural tests below). The optional callback is documented if used."""
    asl = load_asl()
    chosen = _choices_on(asl, "auto_confirm")
    assert len(chosen) == 1, f"expected one Choice on auto_confirm, got {[n for n, _ in chosen]}"
    name, st = chosen[0]
    rules = json.dumps(st["Choices"])
    assert "BooleanEquals" in rules, "the auto_confirm rule must compare a boolean"
    b1 = [n for n, s in _task_states_by_step(asl, 1)]
    assert len(b1) == 1 and name in _reach(asl, b1[0]) and b1[0] not in _reach(asl, name), \
        "B1 must run before the B2 Choice and never after it"
    # Everything reachable from StartAt WITHOUT passing the Choice may only hold B1 (and a b2 callback Task).
    for state_name in _reach(asl, asl["StartAt"], frozenset({name})):
        st_ = asl["States"][state_name]
        assert st_["Type"] != "Parallel", f"{state_name}: B5 can be reached without passing the B2 Choice"
        if st_["Type"] == "Task":
            assert _step_no(_step_of(st_)) in (1, 2), f"{state_name} runs before the B2 confirmation"
    if "waitForTaskToken" in ASL_PATH.read_text(encoding="utf-8"):
        assert "waitForTaskToken" in README.read_text(encoding="utf-8"), "the callback must be documented in the README"


def test_asl_b5_is_a_parallel_state_with_current_and_proposed_branches():
    """SPEC §4 B5 / §14 ("parallel ... branch"): one Parallel state with at least two branches, each running a B5 Task on
    a different payload (current / proposed); no B5 Task outside it."""
    asl = load_asl()
    parallels = [(n, st) for n, st in asl["States"].items() if st["Type"] == "Parallel"]
    b5_parallel = [(n, st) for n, st in parallels
                   if any(_step_no(_step_of(t)) == 5 for b in st["Branches"] for t in b["States"].values()
                          if t["Type"] == "Task")]
    assert len(b5_parallel) == 1, "exactly one Parallel state holds the B5 Tasks"
    name, st = b5_parallel[0]
    assert len(st["Branches"]) >= 2
    payloads = []
    for branch in st["Branches"]:
        b5 = [t for t in branch["States"].values() if t["Type"] == "Task" and _step_no(_step_of(t)) == 5]
        assert len(b5) == 1, "each branch runs one B5 Task"
        payloads.append(json.dumps(_payload_template(b5[0]), sort_keys=True))
    assert len(set(payloads)) == len(payloads), "the branches must analyse different portfolios (different payloads)"
    top_level_b5 = [n for n, t in asl["States"].items() if t["Type"] == "Task" and _step_no(_step_of(t)) == 5]
    assert top_level_b5 == [], f"B5 Tasks outside the Parallel state: {top_level_b5}"


def test_asl_b7_resizer_is_behind_a_choice_on_violations():
    """SPEC §4 B7: the resizer Task runs only when the proposed portfolio has violations: a Choice whose variable names the
    violations, one branch through B7, the other skipping it straight to B8."""
    asl = load_asl()
    # SPEC-AMBIGUITY: the variable name is not specified; stricter reading: it must mention "violat".
    chosen = _choices_on(asl, "violat")
    assert len(chosen) == 1, f"expected one Choice on violations, got {[n for n, _ in chosen]}"
    name, st = chosen[0]
    ((b7, _),) = _task_states_by_step(asl, 7)
    ((b8, _),) = _task_states_by_step(asl, 8)
    targets = [r["Next"] for r in st["Choices"]] + [st["Default"]]
    assert any(b7 in _reach(asl, t, frozenset({b8})) for t in targets), "no branch of the Choice reaches B7"
    skipping = [t for t in targets if b7 not in _reach(asl, t, frozenset({b8})) and b8 in _reach(asl, t)]
    assert skipping, "no branch of the Choice goes to B8 without B7"
    preds = {n for n, s in asl["States"].items() if b7 in _edges(s)}
    assert preds <= {name}, f"B7 must be entered only through the Choice, also entered from {sorted(preds - {name})}"
    assert not any(b7 in _reach(asl, t, frozenset({name})) for t in [asl["StartAt"]]), "B7 reachable around the Choice"


# ---------------------------------------------------------------- running the state machine locally (mock mode)

def test_state_machine_run_equals_pipeline_check_on_verdict_flipped_checks_and_resizer(sfn_run, pipeline_run):
    """SPEC §4 B1-B9, §7.5, §8: executing the ASL with the Lambda handler gives the same Flow B outcome as
    `pipeline.check(robert, in_0001, auto_confirm=True)`: verdict incoherent, the same flipped checks, the same resizer
    headroom (`headroom`, D10), and the same metric deltas."""
    rep, exp = sfn_run["report"], pipeline_run
    assert rep["flow"] == "B" and ULID.match(rep["run_id"])
    assert rep["verdict"] == exp["verdict"] == "incoherent"
    assert rep["diff"]["flipped"] == exp["diff"]["flipped"] and rep["diff"]["flipped"]
    flipped = {(f["id"], f["to"]) for f in rep["diff"]["flipped"]}
    assert {("H3", "violation"), ("H4", "violation")} <= flipped                    # §15 test_hard: NVDA 10 % from ZST
    assert rep["headroom"] == exp["headroom"] and rep["headroom"]                 # D10: the user's own funding only
    assert rep["headroom"]["funding"] == exp["intent"]["funding"]
    for k, v in exp["diff"]["metrics_delta"].items():
        if k != "drivers":
            assert rep["diff"]["metrics_delta"][k] == pytest.approx(v, abs=1e-9)
    assert rep["diff"]["metrics_delta"]["drivers"] == exp["diff"]["metrics_delta"]["drivers"]
    assert rep["intent"] == exp["intent"] and rep["intent"]["intent_id"] == INTENT_ID
    assert rep["disclaimer"] == config.DISCLAIMER["fr-CA"]                          # SPEC §9.4, fixed text
    assert [c["status"] for c in rep["checks_proposed"]] == [c["status"] for c in exp["checks_proposed"]]
    assert [s["status"] for s in rep["sections"]] == [s["status"] for s in exp["sections"]]


def test_state_machine_full_report_equals_pipeline_report_up_to_run_id_and_timestamps(sfn_run, pipeline_run):
    """Stricter than the test above: the whole Flow B report is identical (the steps are the pipeline's own functions);
    only the run_id (ULID) and ISO timestamps may differ."""
    assert _normalise(sfn_run["report"]) == _normalise(pipeline_run)
    assert sfn_run["report"]["run_id"] != pipeline_run["run_id"]


def test_state_machine_is_deterministic_across_executions(sfn_run, sfn_run_again):
    """Invariant 8 / D6: two executions give the same report; no wall clock value leaks into a computed value."""
    assert _normalise(sfn_run["report"]) == _normalise(sfn_run_again["report"])
    assert sfn_run["report"]["run_id"] != sfn_run_again["report"]["run_id"]


def test_state_machine_invokes_the_flow_b_steps_in_order_with_the_pipeline_step_names(sfn_run):
    """SPEC §4: B1, B3, B4, B5 (twice: current and proposed), B6, B7, B8, B9 in this order, through the one Lambda; the
    step names are the pipeline's own step functions (`b1_parse_intent`, `b3_refresh`, ...), B5 excepted."""
    machine = sfn_run["machine"]
    numbers = [_step_no(s) for s in machine.steps]
    assert numbers == sorted(numbers), machine.steps
    assert set(numbers) == FLOW_B_STEP_NUMBERS
    assert numbers.count(5) >= 2 and all(numbers.count(n) == 1 for n in FLOW_B_STEP_NUMBERS - {5})
    for step in machine.steps:
        if _step_no(step) != 5:
            # SPEC-AMBIGUITY: "dispatches event['step'] to the pipeline step function": step name == function name.
            assert step in {f.__name__ for f in pipeline.FLOW_B_STEPS} | {"b1_parse_intent", "b2_confirm"}, step
            assert callable(getattr(pipeline, step))
    assert len({c["function"] for c in machine.calls}) == 1                        # one Lambda for every Task
    assert machine.steps[0] == "b1_parse_intent" and machine.steps[-1] == "b9_save"


def test_state_machine_saves_report_and_audit_in_the_store_b9(sfn_run):
    """SPEC §10 + §4 B9 + D6: the execution writes `reports/<run_id>.json` and `audit/<run_id>.json` (flow B, as_of, model
    id, every LLM call with its gate decision, intent draft fields) and the portfolio; the report on disk is the output."""
    rep, wd = sfn_run["report"], sfn_run["dir"]
    on_disk = json.loads((wd / "reports" / f"{rep['run_id']}.json").read_text(encoding="utf-8"))
    assert on_disk == rep
    assert [p.name for p in (wd / "reports").iterdir()] == [f"{rep['run_id']}.json"]
    audit = json.loads((wd / "audit" / f"{rep['run_id']}.json").read_text(encoding="utf-8"))
    assert audit["run_id"] == rep["run_id"] and audit["flow"] == "B"
    assert audit["verdict"] == rep["verdict"] == "incoherent"
    assert audit["as_of"] == "2026-11-12"                                             # D1, not the wall clock
    assert audit["model_id"] and audit["profile_id"] == "persona-robert"             # D6
    assert audit["llm_calls"] and all(c["node"] and c["gate"] and c["model_id"] for c in audit["llm_calls"])
    assert {c["node"] for c in audit["llm_calls"]} >= {"intent_parser"}
    assert audit["intent_id"] == INTENT_ID and audit["engine"]["seed"] == config.ENGINE_SEED
    assert (wd / "portfolios" / "robert-main.json").exists()


def test_state_machine_stops_with_the_draft_when_not_auto_confirmed(tmp_path):
    """SPEC §4 B2: with `auto_confirm` false the execution stops after B1 with the draft (what `pipeline.check` returns in
    that case); nothing after B1 runs and nothing is saved (the user has not confirmed)."""
    with _Workdir(tmp_path / "p"):
        exp = pipeline.check(FIX, INTENT_ID, auto_confirm=False)
    with _Workdir(tmp_path / "s"):
        out, machine = execute(_input(auto_confirm=False))
    assert exp["status"] == "awaiting_confirmation" and exp["draft"]
    assert machine.steps and machine.steps[0] == "b1_parse_intent"
    assert all(_step_no(s) in (1, 2) for s in machine.steps), machine.steps
    assert out["draft"] == exp["draft"] and out["draft_gate"] == exp["draft_gate"]
    assert out.get("intent", out.get("fixture_intent")) == exp["intent"]
    assert "report" not in out and "verdict" not in out and "checks_proposed" not in out
    assert not (tmp_path / "s" / "reports").exists() and not (tmp_path / "s" / "audit").exists()


def test_state_machine_skips_the_resizer_when_the_proposed_portfolio_has_no_violation(variant, sfn_run):
    """SPEC §4 B7: Robert + XBB.TO 24 % (from ZST.TO) has no hard violation: the Choice skips B7, `headroom` is null (D10),
    the report equals the pipeline's; the NVDA run (violations) did call B7."""
    steps = variant["machine"].steps
    assert not any(_step_no(s) == 7 for s in steps), steps
    assert {_step_no(s) for s in steps} == FLOW_B_STEP_NUMBERS - {7}
    assert not any(c["status"] == "violation" for c in variant["report"]["checks_proposed"])
    assert variant["report"]["headroom"] is None and variant["expected"]["headroom"] is None
    assert variant["report"]["verdict"] == variant["expected"]["verdict"] != "incoherent"
    assert _normalise(variant["report"]) == _normalise(variant["expected"])
    assert any(_step_no(s) == 7 for s in sfn_run["machine"].steps)


# ---------------------------------------------------------------- errors: Retry and Catch -> Fail, replayed from the tape

def _run_with(invoke, execution_input: dict | None = None):
    machine = Machine(invoke)
    execution_input = execution_input or _input()
    return machine, lambda: machine.run(load_asl(), copy.deepcopy(execution_input),
                                         {"Execution": {"Input": execution_input}})


@pytest.mark.parametrize("error", LAMBDA_SERVICE_ERRORS)
def test_lambda_service_errors_are_retried_and_the_execution_still_succeeds(sfn_run, error):
    """Retry: a transient Lambda service error on the first attempt of every Task is retried; the final report is the
    same as without the error (the steps are idempotent from the state machine's point of view)."""
    machine, go = _run_with(sfn_run["recorder"].replay(flaky=error))
    out = go()
    assert _normalise(_report_of(out)) == _normalise(sfn_run["report"])
    assert len(machine.calls) == 2 * len(machine.steps)              # exactly one retry per Task


def test_a_persistent_service_error_is_retried_then_fails_the_execution(sfn_run):
    """Retry is bounded: after MaxAttempts retries the Catch takes the error to the Fail state, which carries it."""
    asl = load_asl()
    ((_, st),) = _task_states_by_step(asl, 3)
    retrier = next(r for r in st["Retry"] if "Lambda.ServiceException" in r["ErrorEquals"])
    machine, go = _run_with(sfn_run["recorder"].replay(fail_at=3, error=("Lambda.ServiceException", "lambda is down")))
    with pytest.raises(StateError) as ei:
        go()
    assert sum(1 for c in machine.calls if _step_no(c["payload"]["step"]) == 3) == 1 + retrier.get("MaxAttempts", 3)
    assert "lambda is down" in ei.value.error + ei.value.cause


@pytest.mark.parametrize("number", sorted(FLOW_B_STEP_NUMBERS))
def test_a_failing_step_ends_in_a_fail_state_that_carries_the_error(sfn_run, number):
    """Catch: a non-retryable error in any of B1, B3-B9 (including a B5 branch inside the Parallel) is caught and the
    execution ends in a Fail state whose Error / Cause carry the original error; nothing runs after the failure and the
    error is not retried."""
    marker = f"boom-in-b{number}"
    machine, go = _run_with(sfn_run["recorder"].replay(fail_at=number, error=("RuntimeError", marker)))
    with pytest.raises(StateError) as ei:
        go()
    assert ei.value.error and marker in ei.value.error + ei.value.cause
    failing = [c for c in machine.calls if _step_no(c["payload"]["step"]) == number]
    assert len(failing) == 1 and machine.calls[-1] is failing[0]
    assert machine.visited[-1] in {n for _, sc in _scopes(load_asl()) for n, s in sc["States"].items()
                                   if s["Type"] == "Fail"}


def test_unknown_intent_fails_the_execution_with_the_handler_error(tmp_path):
    """The real handler raises ValueError for an unknown intent id (pipeline.b1_parse_intent): the machine ends in Fail
    with that error and message, before any LLM call, and nothing is saved."""
    with _Workdir(tmp_path):
        machine, go = _run_with(lambda_invoke, _input(intent_id="in_9999"))
        with pytest.raises(StateError) as ei:
            go()
    text = ei.value.error + ei.value.cause
    assert "ValueError" in text and "in_9999" in text
    assert [_step_no(c["payload"]["step"]) for c in machine.calls] == [1]
    assert not (tmp_path / "reports").exists()


# ---------------------------------------------------------------- the Lambda handler

def test_handler_unknown_step_raises_a_clear_error():
    """`handler({"step": <unknown>})` raises (no silent success) and the message names the step."""
    handler = _handler_module().handler
    with pytest.raises((ValueError, KeyError, LookupError)) as ei:
        handler({"step": "b99_does_not_exist"}, None)
    assert "b99_does_not_exist" in str(ei.value)


@pytest.mark.parametrize("bad", ["_now", "check", "review", "refresh", "__import__", "b2_confirm_typo", "", None, 7])
def test_handler_only_dispatches_flow_b_steps(bad):
    """Only the Flow B steps are dispatchable: not other pipeline functions or private helpers, not a missing or a
    non-string step."""
    handler = _handler_module().handler
    with pytest.raises((ValueError, KeyError, LookupError, TypeError)):
        handler({"step": bad}, None)


def test_handler_without_a_step_raises_a_clear_error():
    handler = _handler_module().handler
    with pytest.raises((ValueError, KeyError, LookupError)) as ei:
        handler({"fixture_dir": str(FIX)}, None)
    assert "step" in str(ei.value)


def test_handler_source_reads_no_wall_clock():
    """Task: no wall clock in computed values. The handler adds no timestamp of its own (the pipeline's audit/profile
    timestamps and the run id are the only clock reads, as in the CLI)."""
    src = Path(_handler_module().__file__).read_text(encoding="utf-8")
    assert not re.search(r"datetime\.(now|utcnow|today)|date\.today|time\.(time|monotonic|perf_counter)\(|\bnow\(\)", src)


# ---------------------------------------------------------------- S3 store backend from the environment

class _FakeS3:
    def __init__(self):
        self.puts: dict[tuple[str, str], bytes] = {}

    def put_object(self, **kw):
        self.puts[(kw["Bucket"], kw["Key"])] = kw["Body"]
        assert kw.get("ContentType") == "application/json"
        return {}

    def get_object(self, **kw):
        from botocore.exceptions import ClientError
        raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "absent"}}, "GetObject")


def test_handler_writes_report_and_audit_to_s3_when_the_store_backend_env_is_s3(tmp_path, monkeypatch):
    """SPEC §10 + task: the store backend comes from the environment (COHERENCE_ANALYZER_STORE=s3 in the Lambda). With the
    s3 env and a fake client, the whole execution writes `<prefix>/reports/<run_id>.json`, `<prefix>/audit/<run_id>.json`
    and the portfolio to the bucket, and nothing to the local disk."""
    fake = _FakeS3()
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE", "s3")
    monkeypatch.setenv("COHERENCE_ANALYZER_S3_BUCKET", "ca-test-bucket")
    monkeypatch.setenv("COHERENCE_ANALYZER_S3_PREFIX", "ca")
    monkeypatch.setattr(config, "COHERENCE_ANALYZER_STORE", "s3")
    monkeypatch.setattr(config, "COHERENCE_ANALYZER_S3_BUCKET", "ca-test-bucket")
    monkeypatch.setattr(config, "COHERENCE_ANALYZER_S3_PREFIX", "ca")
    monkeypatch.setattr(store, "_s3_client", lambda: fake)
    with _Workdir(tmp_path):
        monkeypatch.setenv("COHERENCE_ANALYZER_STORE", "s3")        # _Workdir does not touch it; explicit for clarity
        out, _ = execute(_input())
    rep = _report_of(out)
    keys = {k for _, k in fake.puts}
    assert {f"ca/reports/{rep['run_id']}.json", f"ca/audit/{rep['run_id']}.json", "ca/portfolios/robert-main.json"} <= keys
    assert {b for b, _ in fake.puts} == {"ca-test-bucket"}
    assert json.loads(fake.puts[("ca-test-bucket", f"ca/reports/{rep['run_id']}.json")].decode("utf-8")) == rep
    audit = json.loads(fake.puts[("ca-test-bucket", f"ca/audit/{rep['run_id']}.json")].decode("utf-8"))
    assert audit["flow"] == "B" and audit["verdict"] == rep["verdict"]
    assert not (tmp_path / "reports").exists() and not (tmp_path / "audit").exists()


# ---------------------------------------------------------------- CloudFormation (JSON, never deployed)

def _cfn() -> dict:
    return json.loads(CFN_PATH.read_text(encoding="utf-8"))


def _digest(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _mentions(obj, logical_id: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(logical_id)}(?![A-Za-z0-9])", json.dumps(obj)) is not None


def _of_type(doc: dict, rtype: str) -> dict[str, dict]:
    return {k: v for k, v in doc["Resources"].items() if v["Type"] == rtype}


def _only(doc: dict, rtype: str) -> tuple[str, dict]:
    found = _of_type(doc, rtype)
    assert len(found) == 1, f"expected exactly one {rtype}, got {sorted(found)}"
    ((name, res),) = found.items()
    return name, res


FLOW_B_HANDLER = "coherence_analyzer.lambda_handler.handler"


def _flow_b_lambda(doc: dict) -> tuple[str, dict]:
    """The Step-Functions Flow B Lambda specifically, by its handler: a later, unrelated task added a second
    AWS::Lambda::Function (`ApiFunction`, handler `coherence_analyzer.api_lambda_handler.handler`) serving the whole
    FastAPI app over a Function URL, so "the only Lambda in the template" is no longer a true premise. This file is
    about the Flow B steps Lambda only."""
    found = {k: v for k, v in _of_type(doc, "AWS::Lambda::Function").items()
             if v["Properties"].get("Handler") == FLOW_B_HANDLER}
    assert len(found) == 1, f"expected exactly one Lambda with handler {FLOW_B_HANDLER}, got {sorted(found)}"
    ((name, res),) = found.items()
    return name, res


def _stmts(policy: dict) -> list[dict]:
    st = policy["Statement"]
    return st if isinstance(st, list) else [st]


def _list(x) -> list:
    return x if isinstance(x, list) else [x]


def _role_of(doc: dict, props: dict) -> tuple[str, dict]:
    arn = props["Role"] if "Role" in props else props["RoleArn"]
    assert isinstance(arn, dict) and list(arn) == ["Fn::GetAtt"] and arn["Fn::GetAtt"][1] == "Arn", arn
    name = arn["Fn::GetAtt"][0]
    assert doc["Resources"][name]["Type"] == "AWS::IAM::Role"
    return name, doc["Resources"][name]["Properties"]


def _assume_principals(role_props: dict) -> set[str]:
    out = set()
    for s in _stmts(role_props["AssumeRolePolicyDocument"]):
        assert s["Effect"] == "Allow" and "sts:AssumeRole" in _list(s["Action"])
        out |= set(_list(s["Principal"]["Service"]))
    return out


def _inline_statements(role_props: dict) -> list[dict]:
    return [s for p in role_props.get("Policies", []) for s in _stmts(p["PolicyDocument"])]


def test_cfn_existing_logical_ids_and_properties_are_unchanged():
    """Task: the stack is already deployed. Every existing parameter, resource and output keeps its logical id and its exact
    content (sha256 of the canonical JSON taken before this task); only additions are allowed."""
    doc = _cfn()
    for section, expected in (("Parameters", EXISTING_PARAMETERS), ("Resources", EXISTING_RESOURCES),
                              ("Outputs", EXISTING_OUTPUTS)):
        for logical_id, digest in expected.items():
            assert logical_id in doc[section], f"{section}.{logical_id} was removed or renamed"
            assert _digest(doc[section][logical_id]) == digest, f"{section}.{logical_id} was modified"
    assert doc["AWSTemplateFormatVersion"] == "2010-09-09"


def test_cfn_adds_only_the_expected_resource_types_and_keeps_one_managed_policy():
    """Only a Lambda function, a state machine, IAM roles (and optionally a log group) are added. The least-privilege
    managed policy stays the only AWS::IAM::ManagedPolicy (tests/test_store_s3.py counts it), and no em dash anywhere."""
    raw = CFN_PATH.read_text(encoding="utf-8")
    assert EM_DASH not in raw
    doc = json.loads(raw)
    types_ = {v["Type"] for v in doc["Resources"].values()}
    old = {"AWS::S3::Bucket", "AWS::S3::BucketPolicy", "AWS::Bedrock::Guardrail", "AWS::Bedrock::GuardrailVersion",
           "AWS::IAM::ManagedPolicy"}
    new = {"AWS::Lambda::Function", "AWS::StepFunctions::StateMachine", "AWS::IAM::Role", "AWS::Logs::LogGroup",
           "AWS::Lambda::Url", "AWS::Lambda::Permission"}   # the unrelated API Lambda's Function URL (P9)
    assert old <= types_ and types_ <= old | new, sorted(types_ - old - new)
    _flow_b_lambda(doc)
    _only(doc, "AWS::StepFunctions::StateMachine")
    _only(doc, "AWS::IAM::ManagedPolicy")
    assert len(_of_type(doc, "AWS::Lambda::Function")) == 2, \
        "the Flow B Lambda plus the unrelated API Lambda (P9), not a third"
    assert len(_of_type(doc, "AWS::IAM::Role")) == 3, \
        "one role for the Flow B Lambda, one for the state machine, one for the unrelated API Lambda (P9)"
    for name, res in doc["Resources"].items():
        assert EM_DASH not in name and EM_DASH not in str(res.get("Properties", {}).get("Description", ""))


def test_cfn_lambda_function_runs_the_handler_with_the_live_s3_store_env():
    """SPEC §14 (Lambda hosts the steps): python3.11, the handler, the code location from parameters, and the environment
    of the live deployment: LLM live, store s3 on the stack's bucket and prefix, the guardrail id and version, the model."""
    doc = _cfn()
    fn_id, fn = _flow_b_lambda(doc)
    p = fn["Properties"]
    assert p["Runtime"] == "python3.11"
    assert p["Handler"] == "coherence_analyzer.lambda_handler.handler"
    code = p["Code"]
    assert set(code) == {"S3Bucket", "S3Key"} or {"S3Bucket", "S3Key"} <= set(code), code
    for key in ("S3Bucket", "S3Key"):
        assert isinstance(code[key], dict) and list(code[key]) == ["Ref"] and code[key]["Ref"] in doc["Parameters"], \
            f"Code.{key} must be a template parameter"
    assert all(doc["Parameters"][code[k]["Ref"]]["Type"] == "String" for k in ("S3Bucket", "S3Key"))
    env = p["Environment"]["Variables"]
    assert env["COHERENCE_ANALYZER_LLM"] == "live"
    assert env["COHERENCE_ANALYZER_STORE"] == "s3"
    assert _mentions(env["COHERENCE_ANALYZER_S3_BUCKET"], "DataBucket")
    assert _mentions(env["COHERENCE_ANALYZER_S3_PREFIX"], "S3Prefix")
    assert _mentions(env["BEDROCK_GUARDRAIL_ID"], "ExplainerGuardrail")
    assert _mentions(env["BEDROCK_GUARDRAIL_VERSION"], "ExplainerGuardrailVersion")
    model = env["BEDROCK_MODEL_ID"]
    assert isinstance(model, dict) and any(_mentions(model, n) for n in doc["Parameters"]), \
        "BEDROCK_MODEL_ID must come from a template parameter, not a literal"
    assert not [k for k in env if k.startswith("AWS_")], "AWS_* keys are reserved in the Lambda environment"
    assert 60 <= p["Timeout"] <= 900                      # the engine runs 5 000 paths per portfolio
    assert p["MemorySize"] >= 512
    assert _mentions(p["Role"], _role_of(doc, p)[0]) and fn_id not in json.dumps(p["Role"])


def test_cfn_lambda_role_reuses_the_least_privilege_policy_plus_cloudwatch_logs_only():
    """The Lambda execution role: trusts lambda.amazonaws.com, attaches the existing AppAccessPolicy (Bedrock model,
    guardrail, S3 prefix) and gets CloudWatch Logs (basic execution managed policy or an inline logs-only policy); no
    other managed policy, no wildcard action, no `*` resource."""
    doc = _cfn()
    _, fn = _flow_b_lambda(doc)
    _, role = _role_of(doc, fn["Properties"])
    assert _assume_principals(role) == {"lambda.amazonaws.com"}
    arns = role.get("ManagedPolicyArns", [])
    assert {"Ref": "AppAccessPolicy"} in arns
    basic = [a for a in arns if isinstance(a, str)]
    assert all(a.endswith("service-role/AWSLambdaBasicExecutionRole") for a in basic), basic
    assert len(arns) == 1 + len(basic)
    inline = _inline_statements(role)
    log_actions = {"logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"}
    granted = {a for s in inline for a in _list(s["Action"])}
    assert granted <= log_actions, f"the Lambda role may only add CloudWatch Logs permissions, got {sorted(granted)}"
    assert basic or granted == log_actions, "no CloudWatch Logs permission for the Lambda"
    for s in inline:
        assert s["Effect"] == "Allow" and "*" not in _list(s["Resource"])
    assert "Administrator" not in json.dumps(role) and "FullAccess" not in json.dumps(role)


def test_cfn_state_machine_points_at_the_lambda_and_matches_the_asl_file():
    """The state machine's definition reaches the Lambda ARN (DefinitionSubstitutions or an inlined DefinitionString) and
    every `${Placeholder}` of flow_b.asl.json is substituted; an inlined definition names every state of the ASL file."""
    doc = _cfn()
    fn_id, _ = _flow_b_lambda(doc)
    _, sm = _only(doc, "AWS::StepFunctions::StateMachine")
    p = sm["Properties"]
    raw = ASL_PATH.read_text(encoding="utf-8")
    placeholders = set(re.findall(r"\$\{([A-Za-z0-9_.]+)\}", raw))
    assert placeholders, "the ASL file must reference the Lambda through a ${Placeholder}"
    definition = {k: v for k, v in p.items() if k in ("DefinitionString", "DefinitionSubstitutions", "Definition",
                                                      "DefinitionS3Location")}
    assert definition, "the state machine has no definition"
    assert _mentions(definition, fn_id), "the definition must point at the Lambda function (GetAtt Arn / Ref)"
    if "DefinitionSubstitutions" in p:
        assert placeholders <= set(p["DefinitionSubstitutions"]), sorted(placeholders - set(p["DefinitionSubstitutions"]))
        assert any(_mentions(v, fn_id) for k, v in p["DefinitionSubstitutions"].items() if k in placeholders)
    if "DefinitionString" in p or "Definition" in p:
        text = json.dumps(p.get("DefinitionString", p.get("Definition")))
        names = {n for _, scope in _scopes(load_asl()) for n in scope["States"]}
        assert all(n in text for n in names), f"inlined definition lacks states {sorted(n for n in names if n not in text)}"


def test_cfn_state_machine_role_may_only_invoke_the_one_lambda():
    """The state machine role trusts states.amazonaws.com and is allowed exactly lambda:InvokeFunction, on that function
    (its ARN, optionally with a version suffix) and nothing else; no managed policy attached."""
    doc = _cfn()
    fn_id, _ = _flow_b_lambda(doc)
    _, sm = _only(doc, "AWS::StepFunctions::StateMachine")
    _, lambda_fn = _flow_b_lambda(doc)
    name, role = _role_of(doc, sm["Properties"])
    assert _role_of(doc, lambda_fn["Properties"])[0] != name, "the two roles must be different"
    assert _assume_principals(role) == {"states.amazonaws.com"}
    assert not role.get("ManagedPolicyArns"), "the state machine role must not attach managed policies"
    statements = _inline_statements(role)
    assert statements, "the state machine role has no permission at all"
    assert {a for s in statements for a in _list(s["Action"])} == {"lambda:InvokeFunction"}
    for s in statements:
        assert s["Effect"] == "Allow"
        resources = _list(s["Resource"])
        assert resources and all(_mentions(r, fn_id) for r in resources), resources
        assert "*" not in resources


def test_cfn_outputs_the_state_machine_arn_and_keeps_the_existing_outputs():
    doc = _cfn()
    sm_id, _ = _only(doc, "AWS::StepFunctions::StateMachine")
    out = doc["Outputs"]["StateMachineArn"]
    assert _mentions(out["Value"], sm_id) and out.get("Description")
    assert EM_DASH not in json.dumps(out)
    assert set(EXISTING_OUTPUTS) <= set(doc["Outputs"])
    for name, param in doc["Parameters"].items():
        if name not in EXISTING_PARAMETERS:
            assert param["Type"] == "String" and param.get("Description") and EM_DASH not in json.dumps(param), name


# ---------------------------------------------------------------- README "AWS deploy"

def _readme_section(title: str) -> str:
    text = README.read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(title)}\s*$(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, f"README has no '## {title}' section"
    return m.group(1)


def test_readme_aws_deploy_documents_packaging_updating_and_a_mermaid_state_machine_diagram():
    """README 'AWS deploy': how to package the Lambda code and update the stack, where the state machine ARN comes from,
    and a mermaid diagram that shows the Flow B steps B1..B9 (B2 confirmation, B5 parallel, B7 resizer choice)."""
    section = _readme_section("AWS deploy")
    low = section.lower()
    assert "package" in low and ("zip" in low or "s3" in low)
    assert "lambda_handler" in section
    assert "cloudformation deploy" in low or "update-stack" in low or "update-function-code" in low
    assert "StateMachineArn" in section and "flow_b.asl.json" in section
    blocks = re.findall(r"```mermaid\s*\n(.*?)```", section, re.S)
    assert blocks, "no ```mermaid block in the AWS deploy section"
    diagram = max(blocks, key=len)
    assert re.match(r"\s*(stateDiagram(-v2)?|flowchart|graph)\b", diagram)
    for n in range(1, 10):
        assert f"B{n}" in diagram, f"the diagram does not show B{n}"
    for word in ("auto_confirm", "violation", "Parallel"):
        assert word.lower() in diagram.lower(), f"the diagram does not show {word}"
    assert EM_DASH not in section


# ---------------------------------------------------------------- human-only meta file

def test_the_human_only_activity_tags_file_is_untouched_by_the_runs():
    """CLAUDE.md invariant: nothing here writes data/meta/activity_tags.csv (humans only, with a source URL)."""
    after = TAGS.read_bytes() if TAGS.exists() else None
    assert after == TAGS_BYTES

