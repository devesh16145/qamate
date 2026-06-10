"""
Unit tests for the manual-input gate (OTP on a phone, test on a laptop):
the __MANUAL__ sentinel codegen in generate_from_review and the agent's
mark_step_manual recording. The runtime half (fixture <-> runner <-> UI file
handshake) is exercised live; this is the offline regression net.
"""

import os
import json

import agent_chat as ac
from recorder_parser import generate_from_review


def _payload(steps, tc_id="TC-SIGNUP-009", flow="signup"):
    return {"tc_id": tc_id, "description": "OTP signup", "flowId": flow,
            "preconditions": "", "expectedResult": "", "criteria": [],
            "steps": steps, "assertions": []}


def _gen(tmp_path, steps):
    root = str(tmp_path)
    res = generate_from_review(_payload(steps), root)
    assert isinstance(res, dict) and res.get("status") != "error", res
    flow_dir = os.path.join(root, "tests", "flows", "signup")
    with open(os.path.join(flow_dir, "test_signup.py"), encoding="utf-8") as f:
        code = f.read()
    data = {}
    dp = os.path.join(flow_dir, "test_data.json")
    if os.path.exists(dp):
        with open(dp, encoding="utf-8") as f:
            data = json.load(f)
    return code, data


def _step(i, raw, stype, value="", var=""):
    return {"id": i, "rawLine": raw, "type": stype, "target": raw.split(".", 1)[0],
            "targetDescription": raw[:50], "value": value, "varName": var}


def test_manual_sentinel_generates_pausing_fill(tmp_path):
    steps = [
        _step(1, 'page.goto("https://app.test/signup")', "navigate"),
        _step(2, 'page.get_by_placeholder("Phone").fill("9876543210")', "fill",
              "9876543210", "input_1"),
        _step(3, 'page.get_by_placeholder("OTP").fill("123456")', "fill",
              "__MANUAL__:Enter the OTP sent to +91-98765", "input_2"),
    ]
    code, data = _gen(tmp_path, steps)
    assert 'manual_input("Enter the OTP sent to +91-98765")' in code
    assert "123456" not in code                       # the one-time code never replays
    assert ", manual_input):" in code                 # fixture injected into the signature
    tc = data.get("TC-SIGNUP-009", {})
    assert tc.get("input_1") == "9876543210"          # normal value -> test data
    assert "input_2" not in tc                        # gated value -> NOT test data


def test_no_sentinel_keeps_normal_codegen(tmp_path):
    steps = [
        _step(1, 'page.goto("https://app.test/signup")', "navigate"),
        _step(2, 'page.get_by_placeholder("Phone").fill("9876543210")', "fill",
              "9876543210", "input_1"),
    ]
    code, _ = _gen(tmp_path, steps)
    assert "manual_input" not in code
    assert 'tc_data.get("input_1", "")' in code


def test_manual_prompt_parens_sanitized(tmp_path):
    steps = [
        _step(1, 'page.goto("https://app.test/x")', "navigate"),
        _step(2, 'page.get_by_placeholder("OTP").fill("000")', "fill",
              "__MANUAL__:Enter the OTP (6 digits)", "input_1"),
    ]
    code, _ = _gen(tmp_path, steps)
    # Parens become brackets so the MUI numeric balanced-paren rewrite can't break.
    assert 'manual_input("Enter the OTP [6 digits]")' in code


def test_manual_sentinel_without_prompt_gets_default(tmp_path):
    steps = [
        _step(1, 'page.goto("https://app.test/x")', "navigate"),
        _step(2, 'page.get_by_placeholder("OTP").fill("000")', "fill", "__MANUAL__", "input_1"),
    ]
    code, _ = _gen(tmp_path, steps)
    assert 'manual_input("Enter the required value' in code


# ── mark_step_manual: the agent converts the last fill into a gate ────────────

def _session_with_steps(steps):
    s = ac.BrowserSession.__new__(ac.BrowserSession)
    s.steps = steps
    return s


def test_mark_step_manual_rewrites_last_fill():
    s = _session_with_steps([
        {"id": 1, "type": "navigate", "value": ""},
        {"id": 2, "type": "fill", "value": "9876543210", "varName": "input_1",
         "targetDescription": "Enter phone"},
        {"id": 3, "type": "click", "value": ""},
        {"id": 4, "type": "fill", "value": "123456", "varName": "input_2",
         "targetDescription": "Enter OTP"},
    ])
    r = s.mark_step_manual("Enter the OTP sent to +91-98765")
    assert r["ok"] and r["step_id"] == 4
    assert s.steps[3]["value"] == "__MANUAL__:Enter the OTP sent to +91-98765"
    assert s.steps[1]["value"] == "9876543210"        # earlier fill untouched


def test_mark_step_manual_without_fill_errors():
    s = _session_with_steps([{"id": 1, "type": "click", "value": ""}])
    r = s.mark_step_manual("prompt")
    assert not r["ok"] and "no fill step" in r["error"]
