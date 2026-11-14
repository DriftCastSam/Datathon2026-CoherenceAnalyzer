"""P2.15: the state machine definition inlined in the CloudFormation template is the ASL file, verbatim.

`deploy/aws/flow_b.asl.json` is what the local interpreter in tests/test_stepfunctions.py runs; the template carries a
copy under `FlowBStateMachine.Properties.Definition` (deployable with a plain `cloudformation deploy`). They must not
drift apart.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_cfn_inline_definition_equals_the_asl_file():
    asl = json.loads((ROOT / "deploy" / "aws" / "flow_b.asl.json").read_text(encoding="utf-8"))
    cfn = json.loads((ROOT / "deploy" / "aws" / "coherence-analyzer.cfn.json").read_text(encoding="utf-8"))
    assert cfn["Resources"]["FlowBStateMachine"]["Properties"]["Definition"] == asl
