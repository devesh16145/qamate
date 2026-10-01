"""Local two-origin replay, no public app or LLM calls."""
import json
import os
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from multi_app import MultiAppReplay


@pytest.fixture
def apps():
    state = {"approved_at": None, "propagate": True, "wrong": False, "created": False,
             "record": "ORDER-" + uuid.uuid4().hex}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_GET(self):
            if (self.path == "/create" and self.server.consumer) or (urlsplit(self.path).path == "/approve" and not self.server.consumer):
                self.send_response(403)
                self.end_headers()
                return
            if self.path == "/create":
                state["created"] = True
                body = state["record"]
            elif urlsplit(self.path).path == "/approve":
                record_id = parse_qs(urlsplit(self.path).query).get("id", [None])[0]
                if state["created"] and record_id == state["record"]:
                    state["approved_at"] = time.monotonic()
                body = "ok"
            elif self.path == "/state":
                ready = state["propagate"] and state["approved_at"] is not None and time.monotonic() - state["approved_at"] > .25
                body = json.dumps({"status": "Approved" if ready else "Pending"})
            else:
                record = "OTHER" if state["wrong"] and self.server.consumer else state["record"]
                create = '''<button data-testid="create" onclick="fetch('/create').then(r=>r.text()).then(id=>document.querySelector('output').textContent=id)">Create</button>
                <output data-testid="record"></output>''' if not self.server.consumer else ""
                approve = '''<button data-testid="approve" onclick="fetch('/approve?id='+encodeURIComponent(this.parentElement.dataset.recordId))">Approve</button>''' if self.server.consumer else ""
                body = '''<h1>%s</h1>%s<div data-record-id="%s">%s<span data-testid="status">Pending</span></div>
                <script>setInterval(async()=>{let r=await fetch('/state');let s=await r.json();
                document.querySelector('[data-testid=status]').textContent=s.status},50)</script>''' % (
                    "Approval console" if self.server.consumer else "Record creator", create, record, approve)
            self.send_response(200)
            self.send_header("Content-Type", "application/json" if self.path == "/state" else "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

    servers = []
    threads = []
    for consumer in (False, True):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.consumer = consumer
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        threads.append(thread)
    try:
        yield state, [f"http://127.0.0.1:{s.server_port}/" for s in servers]
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads: thread.join()


@pytest.fixture
def replay():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        with MultiAppReplay(browser) as runtime:
            yield runtime
        browser.close()


def flow(urls):
    def step(app, op, **kw):
        return {"app": app, "actor": "user", "op": op, "timeout_ms": 1200, **kw}
    return {"bindings": [{"app": app, "actor": "user", "url": url} for app, url in zip(("producer", "consumer"), urls)],
            "steps": [step("producer", "open"), step("producer", "click", test_id="create"),
                      step("producer", "capture", test_id="record", capture="order"),
                      step("consumer", "open"), step("consumer", "click", test_id="approve", record="order"),
                      step("producer", "expect_text", test_id="status", record="order", value="Approved")]}


def test_correlated_record_propagates(apps, replay):
    state, urls = apps
    result = replay.run(flow(urls))
    assert result["status"] == "passed" and len(result["completed"]) == 6
    assert replay.captures["order"] == {"value": state["record"], "app": "producer", "actor": "user"}
    assert time.monotonic() - state["approved_at"] >= .25


@pytest.mark.parametrize("fault", ["wrong", "propagate"])
def test_negative_controls_fail(apps, replay, fault):
    state, urls = apps
    state[fault] = fault == "wrong"
    from playwright.sync_api import TimeoutError
    with pytest.raises((AssertionError, TimeoutError)):
        replay.run(flow(urls))
    if fault == "wrong": assert state["approved_at"] is None


def test_actor_contexts_are_isolated_and_new_run_resets(apps, replay):
    _, urls = apps
    bindings = [{"app": "producer", "actor": actor, "url": urls[0]} for actor in ("seller", "admin")]
    workflow = {"bindings": bindings, "steps": [{"app": b["app"], "actor": b["actor"], "op": "open"} for b in bindings]}
    replay.run(workflow)
    seller, admin = [replay.pages[("producer", actor)] for actor in ("seller", "admin")]
    seller.evaluate("localStorage.setItem('token','seller'); document.cookie='auth=seller'")
    assert admin.evaluate("localStorage.getItem('token')") is None
    assert admin.evaluate("document.cookie") == ""
    replay.run(workflow)
    assert replay.pages[("producer", "seller")].evaluate("document.cookie") == ""


def test_cross_origin_navigation_is_blocked(apps, replay):
    _, urls = apps
    data = flow(urls)
    data["steps"] = data["steps"][:1]
    replay.run(data)
    from playwright.sync_api import Error
    with pytest.raises(Error):
        replay.pages[("producer", "user")].goto(urls[1])
    replay.close()
    assert not replay.pages and not replay.contexts and not replay.captures


def test_exported_project_test_independently_replays_and_detects_failure(apps, tmp_path, monkeypatch, replay):
    """Exercise the REAL shared fixtures and JUnit-authoritative verifier."""
    import shutil
    from pathlib import Path
    import project_store
    from project_workflow import export_project_workflow
    from agent_eval import run_test_once
    import agent_eval
    outputs = []
    original_run = agent_eval.subprocess.run

    def capture_run(*args, **kwargs):
        result = original_run(*args, **kwargs)
        outputs.append((result.stdout or "") + (result.stderr or ""))
        return result

    monkeypatch.setattr(agent_eval.subprocess, "run", capture_run)

    state, urls = apps
    source = Path(__file__).resolve().parents[2]
    (tmp_path / "tests").mkdir()
    (tmp_path / "engine").mkdir()
    shutil.copy2(source / "tests" / "conftest.py", tmp_path / "tests" / "conftest.py")
    shared = tmp_path / "tests" / "conftest.py"
    shared.write_text(shared.read_text(encoding="utf-8") + '''

# Test-only tripwire: opt-in multi-app replay must never read shared auth.
def _project_storage_state(project):
    raise AssertionError("Shared project auth was accessed by multi-app replay")
''', encoding="utf-8")
    for name in ("smart_locator.py", "project_store.py", "config_loader.py", "multi_app.py", "project_workflow.py"):
        shutil.copy2(source / "engine" / name, tmp_path / "engine" / name)
    (tmp_path / "config.json").write_text(json.dumps({"tracing": {"mode": "off"}, "network": {"enabled": False}}))
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --tracing=off\n")
    project = project_store.create_project(str(tmp_path), "Replay", "", apps=[
        {"id": app, "url": url, "actors": ["user"]} for app, url in zip(("producer", "consumer"), urls)])
    # Poison the legacy shared capture: the multi-app fixture must not load it.
    auth = Path(project_store.storage_state_path(str(tmp_path), project["id"]))
    auth.parent.mkdir(exist_ok=True)
    auth.write_text(json.dumps({"cookies": [], "origins": [{"origin": urls[0].rstrip('/'),
        "localStorage": [{"name": "shared_auth", "value": "must-not-load"}]}]}))
    from multi_app_recording import MultiAppRecording
    from playwright.sync_api import expect
    with MultiAppRecording(replay.browser, project) as recording:
        def target(app, test_id):
            observed = recording.observe(app, "user")
            return next(t["ref"] for t in observed["targets"] if t["test_id"] == test_id)
        recording.act("click", target("producer", "create"))
        expect(recording.pages[("producer", "user")].get_by_test_id("record")).not_to_have_text("")
        recording.act("capture", target("producer", "record"), capture="order")
        recording.act("click", target("consumer", "approve"))
        recording.act("expect_text", target("producer", "status"), value="Approved")
        result = recording.export(str(tmp_path), "TC-MULTI-001", "multi_app", "Cross-app propagation")
        assert not result["verified"]
        assert recording.steps[-2].record == "order"
    env = {"ATS_ROOT": str(tmp_path), "ATS_PROJECT_ID": project["id"], "ATS_NO_MANUAL_INPUT": "1",
           "ATS_RESULTS_DIR": str(tmp_path / "results"), "PYTHONPATH": str(tmp_path / "engine")}
    for attempt in range(2):
        state.update(approved_at=None, created=False, record="ORDER-" + uuid.uuid4().hex)
        ok, summary = run_test_once(result["flow_dir"], result["tc_id"], env=env, cwd=str(tmp_path), timeout=60)
        assert ok, summary + "\n" + outputs[-1]
    state.update(approved_at=None, created=False, propagate=False)
    ok, summary = run_test_once(result["flow_dir"], result["tc_id"], env=env, cwd=str(tmp_path), timeout=60)
    assert not ok and "failed" in summary.lower(), summary


@pytest.fixture
def recording(apps, replay, tmp_path):
    import project_store
    from multi_app_recording import MultiAppRecording
    _, urls = apps
    project = project_store.create_project(str(tmp_path), "Record", "", apps=[
        {"id": app, "url": url, "actors": ["user"]} for app, url in zip(("producer", "consumer"), urls)])
    with MultiAppRecording(replay.browser, project) as recorder:
        yield recorder


def test_recording_rejects_replaced_nodes_and_cross_app_refs(recording):
    observed = recording.observe("producer", "user")
    ref = next(t["ref"] for t in observed["targets"] if t["test_id"] == "create")
    page = recording.pages[("producer", "user")]
    page.get_by_test_id("create").evaluate("el => el.replaceWith(el.cloneNode(true))")
    with pytest.raises(ValueError, match="Target changed"):
        recording.act("click", ref)
    observed = recording.observe("producer", "user")
    ref = next(t["ref"] for t in observed["targets"] if t["test_id"] == "create")
    recording.observe("consumer", "user")
    with pytest.raises(ValueError, match="stale"):
        recording.act("click", ref)
    assert len(recording.steps) == 2


def test_recording_rejects_secrets_and_false_assertions(recording):
    recording.observe("producer", "user")
    page = recording.pages[("producer", "user")]
    page.evaluate("document.body.insertAdjacentHTML('beforeend', '<input type=password data-testid=secret><span data-testid=outcome>Actual</span>')")
    refs = {t["test_id"]: t["ref"] for t in recording.observe("producer", "user")["targets"]}
    with pytest.raises(ValueError, match="Password/OTP"):
        recording.act("fill", refs["secret"], value="do-not-record")
    assert page.get_by_test_id("secret").input_value() == ""
    with pytest.raises(AssertionError):
        recording.act("expect_text", refs["outcome"], value="Wrong")
    assert len(recording.steps) == 1


def test_uncertain_mutation_blocks_export(recording, monkeypatch, tmp_path):
    observed = recording.observe("producer", "user")
    ref = next(t["ref"] for t in observed["targets"] if t["test_id"] == "create")
    def fail(*args):
        raise RuntimeError("Action may have partially executed")
    monkeypatch.setattr(recording, "execute_step", fail)
    with pytest.raises(RuntimeError):
        recording.act("click", ref)
    with pytest.raises(ValueError, match="uncertain"):
        recording.export(str(tmp_path), "TC-MULTI-001", "blocked", "Blocked")


def test_output_is_not_a_fill_target_and_editability_is_rechecked(recording):
    from goal_controller import GoalAction
    from multi_app_recording import UnsupportedFill
    observed = recording.observe("producer", "user")
    target = next(t for t in observed["targets"] if t["test_id"] == "record")
    assert target["tag"] == "output" and not target["fillable"]
    with pytest.raises(UnsupportedFill, match="not an editable input"):
        recording.goal_identity([GoalAction("fill", target["ref"], "invented")])
    assert not recording.tainted and len(recording.steps) == 1
    page = recording.pages[("producer", "user")]
    page.evaluate("document.body.insertAdjacentHTML('beforeend', '<input data-testid=editable><input readonly data-testid=readonly><input type=checkbox data-testid=check><textarea data-testid=textarea></textarea>')")
    targets = {t["test_id"]: t for t in recording.observe("producer", "user")["targets"]}
    assert targets["editable"]["fillable"] and targets["textarea"]["fillable"]
    assert not targets["readonly"]["fillable"] and not targets["check"]["fillable"]
    page.get_by_test_id("editable").evaluate("el => el.readOnly = true")
    with pytest.raises(UnsupportedFill, match="no longer editable"):
        recording.act("fill", targets["editable"]["ref"], value="blocked")
    assert page.get_by_test_id("editable").input_value() == "" and not recording.tainted
