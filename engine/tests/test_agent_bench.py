"""
Tests for the agent authoring benchmark (engine/agent_bench.py).

The pure core (event scoring, verdicts, aggregation, task loading, cleanup
safety) is tested directly. The subprocess driver (run_task) is exercised for
REAL against a stub agent that speaks the same NDJSON protocol as
agent_chat.py - proving init/chat/input_required/turn_complete/shutdown
handling works offline, with no browser and no LLM.
"""

import os
import sys
import json
import textwrap

import pytest

import agent_bench as ab


# ── parse_tool_summary ────────────────────────────────────────────────────────

def test_parse_clean_json():
    assert ab.parse_tool_summary('{"passed": true, "runs": 2}')["passed"] is True
    assert ab.parse_tool_summary({"passed": False})["passed"] is False
    assert ab.parse_tool_summary("") == {}
    assert ab.parse_tool_summary(None) == {}
    assert ab.parse_tool_summary("[1, 2]") == {}  # non-dict JSON


def test_parse_truncated_json_fallbacks():
    # run_test_case failures carry a 2500-char tail; the emitted summary is cut
    # at 1500 chars so json.loads fails - key probes must still work.
    cut = '{"ok": true, "passed": false, "flaky": true, "tail": "' + "x" * 2000
    r = ab.parse_tool_summary(cut)
    assert r["passed"] is False and r["flaky"] is True
    cut2 = '{"status": "success", "assumed_values": ["Test Brand", "9-Test"], "path": "' + "y" * 2000
    r2 = ab.parse_tool_summary(cut2)
    assert r2["status"] == "success"
    assert r2["assumed_values"] == ["Test Brand", "9-Test"]
    gate = '{"ok": true, "passed": false, "manual_input_gate": true, "note": "' + "z" * 2000
    assert ab.parse_tool_summary(gate)["manual_input_gate"] is True


# ── score_events ──────────────────────────────────────────────────────────────

def _ev(event, **kw):
    return dict(event=event, **kw)


def test_score_happy_path():
    evs = [
        _ev("tool_call", tool="observe", args={}),
        _ev("tool_result", tool="observe", summary="{}"),
        _ev("tool_call", tool="create_test_case", args={"tc_id": "TC-BENCH-001"}),
        _ev("tool_result", tool="create_test_case",
            summary='{"status": "success", "assumed_values": ["a"]}'),
        _ev("tool_call", tool="run_test_case", args={}),
        _ev("tool_result", tool="run_test_case", summary='{"passed": true, "runs": 2}'),
        _ev("usage", input=100, output=40, total=140),
        _ev("turn_complete", text="delivered"),
    ]
    s = ab.score_events(evs)
    assert s["completed"] and s["created"] and s["self_verified"]
    assert s["run_attempts"] == 1 and s["tool_calls"] == 3
    assert s["assumed_values"] == 1 and s["tokens"]["total"] == 140
    assert s["asks"] == 0


def test_score_fix_loop_last_run_wins():
    # fail -> fix -> pass: 2 attempts, delivered verified
    evs = [
        _ev("tool_result", tool="create_test_case", summary='{"status": "success"}'),
        _ev("tool_call", tool="run_test_case", args={}),
        _ev("tool_result", tool="run_test_case", summary='{"passed": false, "tail": "boom"}'),
        _ev("tool_call", tool="run_test_case", args={}),
        _ev("tool_result", tool="run_test_case", summary='{"passed": true}'),
        _ev("turn_complete", text="ok"),
    ]
    s = ab.score_events(evs)
    assert s["self_verified"] and s["run_attempts"] == 2
    # pass -> then a later run fails: delivered in a FAILING state
    evs2 = [
        _ev("tool_result", tool="create_test_case", summary='{"status": "success"}'),
        _ev("tool_result", tool="run_test_case", summary='{"passed": true}'),
        _ev("tool_result", tool="run_test_case", summary='{"passed": false}'),
        _ev("turn_complete", text=""),
    ]
    assert ab.score_events(evs2)["self_verified"] is False


def test_score_asks_errors_incomplete():
    evs = [
        _ev("input_required", question="which brand?"),
        _ev("input_required", question="proceed?", kind="guided_skip"),
        _ev("error", message="transient"),
    ]
    s = ab.score_events(evs)
    assert s["asks"] == 2 and s["errors"] == ["transient"]
    assert not s["completed"] and not s["created"] and not s["self_verified"]


# ── task_verdict ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("result,verdict", [
    ({"skipped": True}, "skipped"),
    ({"infra_error": "no ready"}, "infra_error"),
    ({"completed": False}, "timeout"),
    ({"completed": True, "created": False}, "not_delivered"),
    ({"completed": True, "created": True}, "delivered_unverified"),
    ({"completed": True, "created": True, "independent": {"verdict": "reliable"}}, "reliable"),
    ({"completed": True, "created": True, "independent": {"verdict": "flaky"}}, "flaky"),
    ({"completed": True, "created": True, "independent": {"verdict": "failing"}}, "failing"),
])
def test_task_verdict(result, verdict):
    assert ab.task_verdict(result) == verdict


# ── summarize_bench ───────────────────────────────────────────────────────────

def test_summarize_bench():
    results = [
        # reliable, first try
        {"completed": True, "created": True, "self_verified": True, "run_attempts": 1,
         "independent": {"verdict": "reliable"}, "wall_s": 100, "tool_calls": 10,
         "asks": 0, "assumed_values": 1, "tokens": {"total": 1000}},
        # delivered but independently flaky, needed a fix loop
        {"completed": True, "created": True, "self_verified": True, "run_attempts": 3,
         "independent": {"verdict": "flaky"}, "wall_s": 300, "tool_calls": 30,
         "asks": 1, "assumed_values": 2, "tokens": {"total": 3000}},
        # never delivered
        {"completed": True, "created": False, "wall_s": 200, "tool_calls": 20,
         "asks": 0, "assumed_values": 0, "tokens": {"total": 2000}},
        # skipped: must not count against the score
        {"skipped": True, "task_id": "BENCH-009"},
    ]
    card = ab.summarize_bench(results)
    assert card["total"] == 3 and card["skipped"] == 1
    assert card["delivered"] == 2 and card["independent_reliable"] == 1
    assert card["benchmark_score"] == round(100 / 3, 1)
    assert card["first_try"] == 1          # the 3-attempt task is not first-try
    assert card["asks_total"] == 1 and card["assumed_values_total"] == 3
    assert card["avg_tool_calls"] == 20.0 and card["tokens_total"] == 6000
    assert card["verdicts"]["not_delivered"] == 1


def test_summarize_empty():
    card = ab.summarize_bench([])
    assert card["total"] == 0 and card["benchmark_score"] == 0.0


# ── task loading + the shipped task list ──────────────────────────────────────

def test_load_shipped_tasks_file():
    tasks, defaults = ab.load_bench_tasks(ab.DEFAULT_TASKS)
    assert len(tasks) >= 3
    assert "project" in defaults
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), "bench task ids must be unique"
    for t in tasks:
        assert t["flow_id"].startswith("bench_")
        assert t["tc_id"].startswith("TC-BENCH-")
        # the prompt must dictate where the test goes - scoring depends on it
        assert t["tc_id"] in t["prompt"] and t["flow_id"] in t["prompt"]


def test_load_rejects_bad_tasks(tmp_path):
    bad = tmp_path / "tasks.json"
    bad.write_text(json.dumps({"tasks": [{"id": "X", "tc_id": "TC-X-1", "flow_id": "bench_x"}]}))
    with pytest.raises(SystemExit):
        ab.load_bench_tasks(str(bad))   # missing prompt
    bad.write_text(json.dumps({"tasks": [{"id": "X", "tc_id": "TC-X-1",
                                          "flow_id": "catalog", "prompt": "p"}]}))
    with pytest.raises(SystemExit):
        ab.load_bench_tasks(str(bad))   # flow not bench_-prefixed: cleanup safety


# ── cleanup safety ────────────────────────────────────────────────────────────

def test_clean_bench_flows_only_touches_bench_dirs(tmp_path):
    flows = tmp_path / "flows"
    (flows / "bench_catalog").mkdir(parents=True)
    (flows / "bench_catalog" / "test_bench_catalog.py").write_text("# t")
    (flows / "catalog").mkdir()
    (flows / "catalog" / "test_catalog.py").write_text("# real")
    (flows / "bench_note.txt").write_text("a FILE with the prefix stays")
    removed = ab.clean_bench_flows(str(tmp_path))
    assert removed == ["bench_catalog"]
    assert not (flows / "bench_catalog").exists()
    assert (flows / "catalog" / "test_catalog.py").exists()   # real suite untouched
    assert (flows / "bench_note.txt").exists()


def test_clean_bench_flows_missing_root(tmp_path):
    assert ab.clean_bench_flows(str(tmp_path / "nope")) == []


# ── subprocess driver against a protocol-faithful stub agent ─────────────────

STUB = textwrap.dedent("""\
    import sys, json
    def emit(d):
        print(json.dumps(d), flush=True)
    emit({"event": "started"})
    mode = None
    for line in sys.stdin:
        cmd = json.loads(line)
        a = cmd.get("action")
        if a == "init":
            mode = cmd.get("scenario") or "__SCENARIO__"
            if mode == "init_error":
                emit({"event": "error", "message": "browser failed to start: boom"})
            else:
                emit({"event": "ready", "session_id": "sess-stub", "url": "https://x"})
        elif a == "chat":
            msg = cmd.get("message", "")
            if mode == "hang":
                pass  # never completes -> driver must time out
            elif mode == "terminal":
                emit({"event": "error", "terminal": True, "message": "model unavailable"})
            elif "do not ask again" in msg:
                # this is the canned reply to our input_required pause
                emit({"event": "tool_call", "tool": "create_test_case", "args": {}})
                emit({"event": "tool_result", "tool": "create_test_case",
                      "summary": json.dumps({"status": "success", "assumed_values": ["v1"]})})
                emit({"event": "tool_call", "tool": "run_test_case", "args": {}})
                emit({"event": "tool_result", "tool": "run_test_case",
                      "summary": json.dumps({"passed": True, "runs": 2})})
                emit({"event": "usage", "input": 10, "output": 5, "total": 15})
                emit({"event": "turn_complete", "text": "delivered"})
            else:
                emit({"event": "tool_call", "tool": "observe", "args": {}})
                emit({"event": "input_required", "question": "which value?"})
        elif a == "shutdown":
            emit({"event": "bye"})
            break
""")


def _write_stub(tmp_path, scenario="ok"):
    p = tmp_path / "stub_agent.py"
    p.write_text(STUB.replace("__SCENARIO__", scenario), encoding="utf-8")
    return str(p)


def _task():
    return {"id": "BENCH-T", "tc_id": "TC-BENCH-900", "flow_id": "bench_stub",
            "difficulty": "easy", "app": "seller", "prompt": "author the thing"}


def test_run_task_full_protocol(tmp_path):
    stub = _write_stub(tmp_path)
    events_path = tmp_path / "events" / "BENCH-T.jsonl"
    def check_durable(event):
        persisted = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        assert persisted[-1] == event
    r = ab.run_task(_task(), str(tmp_path), project_id=None, env_name="dev",
                    ready_timeout=30, turn_timeout=30, agent_script=stub,
                    events_path=str(events_path),
                    on_event=check_durable,
                    stderr_path=str(tmp_path / "stderr.log"))
    assert r["infra_error"] is None
    assert r["session_id"] == "sess-stub"
    assert r["completed"] and r["created"] and r["self_verified"]
    assert r["asks"] == 1                      # input_required was auto-answered
    assert r["assumed_values"] == 1 and r["tokens"]["total"] == 15
    assert ab.task_verdict(r) == "delivered_unverified"   # no independent leg here
    lines = [json.loads(l) for l in events_path.read_text(encoding="utf-8").splitlines()]
    assert any(e["event"] == "turn_complete" for e in lines)
    assert all("_ts" in e for e in lines)


def test_run_task_init_error_is_infra(tmp_path):
    stub = _write_stub(tmp_path, scenario="init_error")
    r = ab.run_task(_task(), str(tmp_path), project_id=None, env_name="dev",
                    ready_timeout=30, turn_timeout=10, agent_script=stub)
    assert r["infra_error"] and "boom" in r["infra_error"]
    assert ab.task_verdict(r) == "infra_error"


def test_run_task_hang_times_out(tmp_path):
    stub = _write_stub(tmp_path, scenario="hang")
    r = ab.run_task(_task(), str(tmp_path), project_id=None, env_name="dev",
                    ready_timeout=30, turn_timeout=4, agent_script=stub)
    assert r["infra_error"] is None and not r["completed"]
    assert ab.task_verdict(r) == "timeout"


def test_terminal_error_finishes_without_waiting_for_timeout(tmp_path):
    stub = _write_stub(tmp_path, scenario="terminal")
    result = ab.run_task(_task(), str(tmp_path), project_id=None, env_name="dev",
                         ready_timeout=30, turn_timeout=60, agent_script=stub)
    assert result["completed"] and result["terminal_error"]
    assert ab.task_verdict(result) == "model_error"
    assert result["wall_s"] < 15


def test_child_env_defaults_dev_seller_index(monkeypatch):
    monkeypatch.delenv("ATS_SELLER_USER_INDEX", raising=False)
    assert "ATS_SELLER_USER_INDEX" not in ab._child_env("/r", "dev")
    monkeypatch.setenv("ATS_SELLER_USER_INDEX", "2")
    assert ab._child_env("/r", "dev")["ATS_SELLER_USER_INDEX"] == "2"
    monkeypatch.delenv("ATS_SELLER_USER_INDEX", raising=False)
    assert "ATS_SELLER_USER_INDEX" not in ab._child_env("/r", "staging")


def test_score_usage_est_survives_killed_turn():
    # a timed-out turn never emits 'usage' — the wrapper's per-request estimate
    # is the only cost record (nine such tasks once hid ~100M+ real spend)
    evs = [_ev("tool_call", tool="observe", args={}),
           _ev("usage_est", requests=1, input_est_total=18000, last_request_est=18000),
           _ev("tool_call", tool="click", args={}),
           _ev("usage_est", requests=2, input_est_total=39000, last_request_est=21000)]
    s = ab.score_events(evs)
    assert s["input_est_total"] == 39000 and s["requests"] == 2
    assert s["tokens"]["total"] == 0          # provider usage never arrived
    assert s["usage_incomplete"] is True     # zero is not a measured token total


def test_summarize_tokens_incl_est():
    results = [
        {"completed": True, "created": True, "tokens": {"total": 1000}},       # reported
        {"completed": False, "tokens": {"total": 0}, "input_est_total": 5000}, # killed turn
        {"skipped": True},
    ]
    card = ab.summarize_bench(results)
    assert card["tokens_total"] == 1000
    assert card["tokens_total_incl_est"] == 6000


def test_independent_replays_have_distinct_artifact_roots(tmp_path, monkeypatch):
    seen = []
    def run(*args, **kwargs):
        seen.append(kwargs["env"]["ATS_RESULTS_DIR"])
        return True, "1 passed"
    monkeypatch.setattr(ab.agent_eval, "run_test_once", run)
    one = ab.verify_independent(str(tmp_path), str(tmp_path / "tests"), _task(), "dev", None)
    two = ab.verify_independent(str(tmp_path), str(tmp_path / "tests"), _task(), "dev", None)
    assert len(set(seen)) == 4
    assert one["artifact_dirs"] == seen[:2] and two["artifact_dirs"] == seen[2:]
    assert one["runs"] == [True, True]
