"""S3 store backend + the AWS CloudFormation template (P1.3). SPEC §10, §14, §6.1; docs/spec-decisions.md D6.

Offline by construction: no network and no AWS call. moto is not installed, so S3 traffic goes through
``botocore.stub.Stubber`` on a real boto3 client built with dummy credentials (region us-east-1). The fixture below
makes ``boto3.client(...)`` and ``boto3.Session(...).client(...)`` hand that stubbed client to the store, so the
store must build its client at call time through boto3 (an import-time client would bypass the stub). Every
put/get is asserted with exact ``Bucket`` / ``Key`` / ``Body`` / ``ContentType`` parameters.

The CloudFormation part parses the JSON template under ``deploy/aws/`` and pins the bucket, bucket policy, IAM
policy and outputs (the guardrail resources are pinned in tests/test_guardrail.py).
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from coherence_analyzer import config, store

ROOT = Path(__file__).resolve().parents[2]
BUCKET = "ca-test-bucket"
EM_DASH = "\u2014"

PROFILE = {"meta": {"profile_id": "amira", "version": 3, "name": "Amira Côté"},
           "values": {"notes": "Pas tout en tech – équilibré"}, "risk": {"declared": "low_moderate"}}
PORTFOLIO = {"portfolio_id": "pf_robert", "as_of": "2026-11-12", "currency": "CAD",
             "weights_pct": {"XBB.TO": 60.0, "ZST.TO": 40.0}, "weights": {"XBB.TO": 0.6, "ZST.TO": 0.4}}
REPORT = {"run_id": "01HX0000000000000000000001", "verdict": "coherent", "unknowns": ["H6"],
          "explanation": {"mode": "template", "headline": "Portefeuille cohérent avec votre profil."}}
AUDIT = {"run_id": "01HX0000000000000000000001", "flow": "C", "verdict": "coherent",
         "llm_calls": [], "engine": {"seed": 7, "n": 5000}}


def json_bytes(doc: dict) -> bytes:
    """What the local backend writes (SPEC §10; store._dump): 2-space indent, UTF-8, trailing newline."""
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _body(data: bytes) -> StreamingBody:
    return StreamingBody(io.BytesIO(data), len(data))


# ---------------------------------------------------------------- fixtures

class S3Env:
    def __init__(self, monkeypatch, tmp_path: Path):
        self.monkeypatch = monkeypatch
        self.tmp = tmp_path
        session = boto3.session.Session(aws_access_key_id="testing", aws_secret_access_key="testing",
                                        region_name="us-east-1")
        self.client = session.client("s3")           # built BEFORE boto3 is patched
        self.stub = Stubber(self.client)
        self.stub.activate()
        client = self.client

        def fake_client(service_name, *args, **kwargs):
            assert service_name == "s3", f"the store asked for a {service_name!r} client"
            return client

        monkeypatch.setattr(boto3, "client", fake_client)
        monkeypatch.setattr(boto3.session.Session, "client",
                            lambda self_, service_name, *a, **k: fake_client(service_name))
        for name, obj in vars(store).items():          # a module-level client cache would defeat the stub
            if "client" in name and hasattr(obj, "cache_clear"):
                obj.cache_clear()
        self.configure("s3", BUCKET, "")

    def configure(self, backend: str, bucket: str | None, prefix: str | None) -> None:
        """Set the backend, bucket and prefix through the environment and the config module (read either way)."""
        mp = self.monkeypatch
        pairs = {"COHERENCE_ANALYZER_STORE": backend, "COHERENCE_ANALYZER_S3_BUCKET": bucket,
                 "COHERENCE_ANALYZER_S3_PREFIX": prefix}
        for name, value in pairs.items():
            if value is None:
                mp.delenv(name, raising=False)
            else:
                mp.setenv(name, value)
            for mod in (config, store):
                mp.setattr(mod, name, value or "", raising=False)

    def put(self, key: str, doc_bytes: bytes) -> None:
        self.stub.add_response("put_object", {}, {"Bucket": BUCKET, "Key": key, "Body": doc_bytes,
                                                  "ContentType": "application/json"})

    def get(self, key: str, data: bytes) -> None:
        self.stub.add_response("get_object", {"Body": _body(data), "ContentLength": len(data)},
                               {"Bucket": BUCKET, "Key": key})

    def missing(self, key: str) -> None:
        self.stub.add_client_error("get_object", service_error_code="NoSuchKey",
                                   service_message="The specified key does not exist.", http_status_code=404,
                                   expected_params={"Bucket": BUCKET, "Key": key})

    def done(self) -> None:
        self.stub.assert_no_pending_responses()


@pytest.fixture
def s3(monkeypatch, tmp_path):
    for var, val in (("AWS_ACCESS_KEY_ID", "testing"), ("AWS_SECRET_ACCESS_KEY", "testing"),
                     ("AWS_DEFAULT_REGION", "us-east-1"), ("AWS_REGION", "us-east-1")):
        monkeypatch.setenv(var, val)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    local = tmp_path / "local_store"
    local.mkdir()
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(local))
    monkeypatch.chdir(local)
    env = S3Env(monkeypatch, tmp_path)
    yield env
    env.stub.deactivate()


# ---------------------------------------------------------------- local backend stays as it is

def test_importing_store_does_not_import_boto3():
    # SPEC §6.1 / bedrock docstring: boto3 is lazy; the local backend (default) must never need it.
    code = ("import sys; import coherence_analyzer.store; "
            "assert 'boto3' not in sys.modules, 'boto3 imported at store import time'")
    res = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "COHERENCE_ANALYZER_LLM": "mock", "PYTHONPATH": str(ROOT)})
    assert res.returncode == 0, res.stderr


def test_local_backend_is_byte_identical_and_never_touches_boto3(monkeypatch, tmp_path):
    # SPEC §10: layout; the local backend is the default and stays byte-identical.
    monkeypatch.setitem(sys.modules, "boto3", None)            # any boto3 import now raises ImportError
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(tmp_path))
    for mod in (config, store):
        monkeypatch.setattr(mod, "COHERENCE_ANALYZER_STORE", "local", raising=False)
    store.save_profile(PROFILE)
    store.save_portfolio(PORTFOLIO)
    store.save_report(REPORT)
    store.save_audit(AUDIT)
    portfolio_stored = {k: v for k, v in PORTFOLIO.items() if k != "weights"}
    assert (tmp_path / "profiles/amira/profile.v3.json").read_bytes() == json_bytes(PROFILE)
    assert (tmp_path / "portfolios/pf_robert.json").read_bytes() == json_bytes(portfolio_stored)
    assert (tmp_path / f"reports/{REPORT['run_id']}.json").read_bytes() == json_bytes(REPORT)
    assert (tmp_path / f"audit/{AUDIT['run_id']}.json").read_bytes() == json_bytes(AUDIT)
    assert store.load_report(REPORT["run_id"]) == REPORT
    assert store.load_audit(AUDIT["run_id"]) == AUDIT
    with pytest.raises(FileNotFoundError):
        store.load_report("01HX0000000000000000009999")
    with pytest.raises(FileNotFoundError):
        store.load_audit("01HX0000000000000000009999")


# ---------------------------------------------------------------- s3 writes (SPEC §10 layout)

SAVES = [
    ("save_profile", PROFILE, "profiles/amira/profile.v3.json", PROFILE),
    ("save_portfolio", PORTFOLIO, "portfolios/pf_robert.json", {k: v for k, v in PORTFOLIO.items() if k != "weights"}),
    ("save_report", REPORT, f"reports/{REPORT['run_id']}.json", REPORT),
    ("save_audit", AUDIT, f"audit/{AUDIT['run_id']}.json", AUDIT),
]


@pytest.mark.parametrize("prefix,key_prefix", [("", ""), ("ca/dev", "ca/dev/"), ("ca/dev/", "ca/dev/")])
@pytest.mark.parametrize("fn,doc,key,stored", SAVES, ids=[s[0] for s in SAVES])
def test_s3_save_puts_exact_bucket_key_body_contenttype(s3, fn, doc, key, stored, prefix, key_prefix):
    # SPEC §10 same layout keys under COHERENCE_ANALYZER_S3_PREFIX (a trailing slash must not double up);
    # task: ContentType application/json, body = the same JSON bytes as local.
    s3.configure("s3", BUCKET, prefix)
    s3.put(key_prefix + key, json_bytes(stored))
    getattr(store, fn)(doc)
    s3.done()


def test_s3_body_equals_the_bytes_the_local_backend_writes(s3, monkeypatch, tmp_path):
    # Cross-check with the real local writer, not with a re-implementation (non-ASCII text included).
    s3.configure("local", None, None)
    store.save_profile(PROFILE)
    local_bytes = (tmp_path / "local_store/profiles/amira/profile.v3.json").read_bytes()
    assert "Côté".encode("utf-8") in local_bytes
    s3.configure("s3", BUCKET, "")
    s3.put("profiles/amira/profile.v3.json", local_bytes)
    store.save_profile(PROFILE)
    s3.done()


def test_s3_save_portfolio_stores_percent_weights_only(s3):
    # store.py docstring: the fraction view `weights` is a loader convenience, never stored.
    s3.put("portfolios/pf_robert.json", json_bytes({k: v for k, v in PORTFOLIO.items() if k != "weights"}))
    store.save_portfolio(PORTFOLIO)
    s3.done()


def test_s3_save_never_writes_to_the_local_disk(s3, tmp_path):
    s3.put("profiles/amira/profile.v3.json", json_bytes(PROFILE))
    s3.put(f"reports/{REPORT['run_id']}.json", json_bytes(REPORT))
    store.save_profile(PROFILE)
    store.save_report(REPORT)
    s3.done()
    assert list((tmp_path / "local_store").rglob("*")) == []


# ---------------------------------------------------------------- s3 reads

def test_s3_load_report_and_audit_get_the_exact_key(s3):
    s3.configure("s3", BUCKET, "ca/dev")
    s3.get(f"ca/dev/reports/{REPORT['run_id']}.json", json_bytes(REPORT))
    s3.get(f"ca/dev/audit/{AUDIT['run_id']}.json", json_bytes(AUDIT))
    assert store.load_report(REPORT["run_id"]) == REPORT
    assert store.load_audit(AUDIT["run_id"]) == AUDIT
    s3.done()


@pytest.mark.parametrize("fn,folder", [("load_report", "reports"), ("load_audit", "audit")])
def test_s3_load_missing_key_raises_filenotfounderror_like_local(s3, fn, folder):
    # Task: load_report/load_audit on a missing key raise FileNotFoundError, as the local backend does.
    s3.missing(f"{folder}/01HX0000000000000000009999.json")
    with pytest.raises(FileNotFoundError):
        getattr(store, fn)("01HX0000000000000000009999")
    s3.done()


@pytest.mark.parametrize("fn,folder", [("load_report", "reports"), ("load_audit", "audit")])
def test_s3_access_denied_is_not_reported_as_missing(s3, fn, folder):
    # SPEC-AMBIGUITY: only a missing key maps to FileNotFoundError; a permissions error must stay visible.
    s3.stub.add_client_error("get_object", service_error_code="AccessDenied", service_message="Access Denied",
                             http_status_code=403,
                             expected_params={"Bucket": BUCKET, "Key": f"{folder}/01HX0000000000000000000002.json"})
    with pytest.raises(ClientError) as ei:
        getattr(store, fn)("01HX0000000000000000000002")
    assert ei.value.response["Error"]["Code"] == "AccessDenied"
    assert not isinstance(ei.value, FileNotFoundError)


# ---------------------------------------------------------------- events.jsonl: get + put (non-atomic)

# SPEC-AMBIGUITY: `read_events(path)` / `append_event(path, event)` keep their `Path` signature. On s3 the path is
# read as the §10 layout key relative to the prefix (profiles/<id>/events.jsonl), stricter than guessing at a
# local absolute path. ContentType stays application/json for every s3 write (task wording), jsonl included.
EVENTS_PATH = Path("profiles/robert/events.jsonl")
EVENTS_KEY = "profiles/robert/events.jsonl"
EV1 = {"event_id": "ev_robert_0001", "type": "question", "text": "Quelle est la durée de XBB?"}
EV2 = {"event_id": "ev_robert_0002", "type": "order_intent", "text": "J'aimerais 10 % de NVDA"}


def _line(ev: dict) -> bytes:
    return (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8")


def test_s3_append_event_is_get_then_put_of_the_whole_object(s3):
    # Task: append_event on s3 = get + put (non-atomic). Stubber enforces the order get -> put.
    s3.get(EVENTS_KEY, _line(EV1))
    s3.put(EVENTS_KEY, _line(EV1) + _line(EV2))
    store.append_event(EVENTS_PATH, EV2)
    s3.done()


def test_s3_append_event_creates_the_object_when_missing(s3):
    s3.missing(EVENTS_KEY)
    s3.put(EVENTS_KEY, _line(EV1))
    store.append_event(EVENTS_PATH, EV1)
    s3.done()


def test_s3_read_events_parses_jsonl_and_missing_means_empty(s3):
    s3.get(EVENTS_KEY, _line(EV1) + _line(EV2))
    assert store.read_events(EVENTS_PATH) == [EV1, EV2]
    s3.missing(EVENTS_KEY)
    assert store.read_events(EVENTS_PATH) == []        # same as the local backend for an absent file
    s3.done()


# ---------------------------------------------------------------- configuration errors

@pytest.mark.parametrize("fn,arg", [("save_report", REPORT), ("load_report", REPORT["run_id"])])
@pytest.mark.parametrize("bucket", [None, ""])
def test_s3_without_a_bucket_raises_a_clear_error_before_any_call(s3, fn, arg, bucket):
    # SPEC-AMBIGUITY: the error type is not specified; ValueError or RuntimeError, but not the old
    # NotImplementedError stub, and the message must name the env var to set.
    s3.configure("s3", bucket, "")
    with pytest.raises((ValueError, RuntimeError)) as ei:
        getattr(store, fn)(arg)
    assert not isinstance(ei.value, NotImplementedError)
    assert "COHERENCE_ANALYZER_S3_BUCKET" in str(ei.value)
    s3.done()                                            # nothing queued, nothing called


def test_unknown_backend_raises_a_clear_error_naming_it(s3):
    s3.configure("gcs", BUCKET, "")
    with pytest.raises((ValueError, RuntimeError)) as ei:
        store.save_report(REPORT)
    assert not isinstance(ei.value, NotImplementedError)
    msg = str(ei.value)
    assert "gcs" in msg and "local" in msg and "s3" in msg


def test_backend_is_chosen_per_call_so_local_still_works_after_s3(s3, tmp_path):
    s3.put(f"reports/{REPORT['run_id']}.json", json_bytes(REPORT))
    store.save_report(REPORT)
    s3.configure("local", None, None)
    store.save_report(REPORT)
    assert (tmp_path / "local_store" / "reports" / f"{REPORT['run_id']}.json").read_bytes() == json_bytes(REPORT)
    s3.done()


# ---------------------------------------------------------------- CloudFormation template (JSON, no deploy)

def _load_template() -> tuple[dict, str]:
    # SPEC-AMBIGUITY: the file name is not specified; the template is the one JSON file under deploy/aws/ that
    # has an AWSTemplateFormatVersion key.
    found = []
    for p in sorted((ROOT / "deploy" / "aws").glob("*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and "AWSTemplateFormatVersion" in doc:
            found.append((doc, p.read_text(encoding="utf-8")))
    assert len(found) == 1, f"expected exactly one CloudFormation JSON template in deploy/aws/, got {len(found)}"
    return found[0]


def _resources(doc: dict, rtype: str) -> dict[str, dict]:
    return {k: v for k, v in doc["Resources"].items() if v["Type"] == rtype}


def _statements(policy_doc: dict) -> list[dict]:
    st = policy_doc["Statement"]
    return st if isinstance(st, list) else [st]


def _as_list(x) -> list:
    return x if isinstance(x, list) else [x]


def _one(doc: dict, rtype: str) -> tuple[str, dict]:
    found = _resources(doc, rtype)
    assert len(found) == 1, f"expected exactly one {rtype}, got {list(found)}"
    ((name, res),) = found.items()
    return name, res


def test_template_is_json_with_no_em_dash_anywhere():
    # Task: JSON, not YAML; no em dashes in resource names or descriptions (stricter: nowhere in the file).
    doc, raw = _load_template()
    assert doc["AWSTemplateFormatVersion"] == "2010-09-09"
    assert EM_DASH not in raw
    assert "Resources" in doc and "Outputs" in doc and "Parameters" in doc


def test_template_bucket_is_versioned_private_and_encrypted():
    doc, _ = _load_template()
    _, bucket = _one(doc, "AWS::S3::Bucket")
    props = bucket["Properties"]
    assert props["VersioningConfiguration"]["Status"] == "Enabled"
    pab = props["PublicAccessBlockConfiguration"]
    assert pab == {"BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True,
                   "RestrictPublicBuckets": True}
    rules = props["BucketEncryption"]["ServerSideEncryptionConfiguration"]
    assert rules and all(r["ServerSideEncryptionByDefault"]["SSEAlgorithm"] in ("AES256", "aws:kms") for r in rules)


def test_template_bucket_policy_denies_non_tls_requests_on_bucket_and_objects():
    doc, _ = _load_template()
    bucket_id, _ = _one(doc, "AWS::S3::Bucket")
    _, policy = _one(doc, "AWS::S3::BucketPolicy")
    assert bucket_id in json.dumps(policy["Properties"]["Bucket"])
    denies = [s for s in _statements(policy["Properties"]["PolicyDocument"])
              if s["Effect"] == "Deny"
              and str(s.get("Condition", {}).get("Bool", {}).get("aws:SecureTransport")).lower() == "false"]
    assert denies, "no Deny statement on aws:SecureTransport = false"
    st = denies[0]
    assert st["Principal"] in ("*", {"AWS": "*"})
    assert any(a in ("s3:*",) for a in _as_list(st["Action"]))
    res = json.dumps(st["Resource"])
    assert bucket_id in res and "/*" in res            # the bucket itself and its objects


def test_template_declares_the_model_arn_parameter():
    doc, _ = _load_template()
    assert doc["Parameters"]["ModelArn"]["Type"] == "String"


def test_template_iam_policy_is_least_privilege():
    # SPEC §14: one role/policy, least privilege (bedrock:InvokeModel on the model ARN, the S3 prefix only).
    doc, _ = _load_template()
    bucket_id, _ = _one(doc, "AWS::S3::Bucket")
    guardrail_id, _ = _one(doc, "AWS::Bedrock::Guardrail")
    _, pol = _one(doc, "AWS::IAM::ManagedPolicy")
    statements = _statements(pol["Properties"]["PolicyDocument"])
    assert all(s["Effect"] == "Allow" for s in statements)
    for s in statements:                               # no wildcards on actions or resources
        assert all(a != "*" and not a.endswith(":*") for a in _as_list(s["Action"]))
        assert "*" not in [r for r in _as_list(s["Resource"]) if isinstance(r, str) and r == "*"]

    def find(action: str) -> list[dict]:
        return [s for s in statements if action in _as_list(s["Action"])]

    (invoke,) = find("bedrock:InvokeModel")
    inv = json.dumps(invoke["Resource"])
    assert '"ModelArn"' in inv or "${ModelArn}" in inv
    (apply_,) = find("bedrock:ApplyGuardrail")
    assert guardrail_id in json.dumps(apply_["Resource"])
    for action in ("s3:GetObject", "s3:PutObject"):
        (obj,) = find(action)
        res = json.dumps(obj["Resource"])
        assert bucket_id in res and "/" in res.replace("arn:", "")      # objects under the bucket, not the bucket
    (lst,) = find("s3:ListBucket")
    assert bucket_id in json.dumps(lst["Resource"]) and "/*" not in json.dumps(lst["Resource"])
    cond = lst["Condition"]
    assert any("s3:prefix" in v for v in cond.values()), cond    # ListBucket limited to the prefix
    allowed = {"bedrock:InvokeModel", "bedrock:ApplyGuardrail", "s3:GetObject", "s3:PutObject", "s3:ListBucket"}
    assert {a for s in statements for a in _as_list(s["Action"])} == allowed


def test_template_outputs_bucket_name_guardrail_id_and_version():
    doc, _ = _load_template()
    out = doc["Outputs"]
    assert {"BucketName", "GuardrailId", "GuardrailVersion"} <= set(out)
    bucket_id, _ = _one(doc, "AWS::S3::Bucket")
    guardrail_id, _ = _one(doc, "AWS::Bedrock::Guardrail")
    version_id, _ = _one(doc, "AWS::Bedrock::GuardrailVersion")
    assert bucket_id in json.dumps(out["BucketName"]["Value"])
    assert guardrail_id in json.dumps(out["GuardrailId"]["Value"])
    assert version_id in json.dumps(out["GuardrailVersion"]["Value"])


# ---------------------------------------------------------------- README and .env.example

def _section(text: str, title: str) -> str:
    m = re.search(rf"^(#+)\s*{re.escape(title)}\s*$", text, flags=re.M)
    assert m, f"README has no '{title}' heading"
    level = len(m.group(1))
    rest = text[m.end():]
    nxt = re.search(rf"^#{{1,{level}}}\s", rest, flags=re.M)
    return rest[:nxt.start()] if nxt else rest


def test_readme_has_an_aws_deploy_section_with_the_command_and_env_values():
    section = _section((ROOT / "README.md").read_text(encoding="utf-8"), "AWS deploy")
    assert "aws cloudformation deploy" in section
    assert "deploy/aws/" in section and "ModelArn" in section and "CAPABILITY_IAM" in section
    for token in ("COHERENCE_ANALYZER_STORE=s3", "COHERENCE_ANALYZER_S3_BUCKET", "COHERENCE_ANALYZER_S3_PREFIX",
                  "BEDROCK_GUARDRAIL_ID", "BEDROCK_GUARDRAIL_VERSION", "BEDROCK_MODEL_ID"):
        assert token in section, token


def test_readme_assumptions_document_the_non_atomic_event_append():
    section = _section((ROOT / "README.md").read_text(encoding="utf-8"), "Assumptions").lower()
    assert "append_event" in section and "non-atomic" in section and "s3" in section


def test_env_example_lists_the_s3_settings_without_secrets():
    keys = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            keys[k.strip()] = v.strip()
    assert {"COHERENCE_ANALYZER_S3_BUCKET", "COHERENCE_ANALYZER_S3_PREFIX", "COHERENCE_ANALYZER_STORE",
            "BEDROCK_GUARDRAIL_ID", "BEDROCK_GUARDRAIL_VERSION"} <= set(keys)
    assert keys["COHERENCE_ANALYZER_STORE"] == "local"            # the default stays local
    assert keys["COHERENCE_ANALYZER_S3_BUCKET"] == ""             # no real bucket name in the repo
    assert not any(k.startswith("AWS_SECRET") or k.startswith("AWS_ACCESS") for k in keys)
