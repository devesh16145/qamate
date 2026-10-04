"""Fast explorer engine (qm_*) against real browsers and the local Dispatch Desk app.

Covers the core promise: steps authored live through qm_runtime.Flow, written as a
test by qm_testgen, pass (a) the fast in-process replay and (b) a real pytest run with
QAmate's conftest, because both execute the identical calls.
"""
import functools
import http.server
import json
import os
import subprocess
import sys
import threading
import time

import pytest
from playwright.sync_api import sync_playwright

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_ROOT = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)
from qm_observe import observe
from qm_runtime import Flow, relative_url
from qm_selectors import locator_for
from qm_steps import execute
from qm_testgen import write_test
from qm_verify import replay


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/slow-data"):
            time.sleep(0.7)   # an API call slower than the settle quiet window
            body = json.dumps({"msg": "Report ready"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


@pytest.fixture(scope="module")
def server():
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Handler, directory=os.path.join(ENGINE, "fixtures")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/"
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        yield b
        b.close()


class Author:
    """Minimal stand-in for the explorer: find the element for an intent in the live
    observation, take its stable locator, execute through Flow, record the step."""

    def __init__(self, page, base_url):
        self.page, self.base_url = page, base_url
        self.flow = Flow(page, base_url=base_url)
        self.steps, self.data = [], {}

    def _target(self, role, name):
        obs = observe(self.page)
        matches = [e for e in obs.elements if e.role == role and e.label == name]
        assert len(matches) == 1, f"{role} {name!r}: {len(matches)} matches"
        loc = locator_for(self.page, matches[0])
        assert loc["unique"], loc
        return loc["python"]

    def do(self, op, role=None, name=None, *, label=None, **extra):
        step = {"op": op, "name": label or name, **extra}
        if role:
            step["target"] = self._target(role, name)
        before = self.page.url
        if step.get("data_key"):
            self.data[step["data_key"]] = step["value"]
        execute(self.flow, step, self.data)
        # The effect seen live becomes the replay's wait condition.
        if op in ("click", "press") and self.page.url != before and "expect_url" not in step:
            step["expect_url"] = relative_url(self.page.url)
        self.steps.append(step)
        return step


def author_transfer(page, base_url):
    a = Author(page, base_url)
    a.do("goto", value="operations.html#/transfers", label="Open transfers")
    a.do("click", "link", "New transfer")
    a.do("fill", "textbox", "Transfer name", value="Batch 7", data_key="transfer_name")
    a.do("select", "combobox", "Region", value="North")
    a.do("select", "combobox", "Destination", value="Delhi")
    a.do("fill", "spinbutton", "Units", value="7", data_key="units")
    a.do("click", "button", "Choose service")
    a.do("click", "button", "Express", label="Choose Express service")
    a.do("expect_page_text", value="Service: Express", label="Express service chosen")
    a.do("click", "button", "Save transfer")
    a.do("expect_page_text", value="Destination: Delhi", label="Saved transfer shows its destination")
    return a


def test_authored_flow_records_effects_and_replays_in_fresh_context(browser, server):
    context = browser.new_context()
    page = context.new_page()
    started = time.monotonic()
    a = author_transfer(page, server)
    authoring_s = time.monotonic() - started
    context.close()

    by_name = {s["name"]: s for s in a.steps}
    # The async save (650 ms timer) was waited for, and the URL change became the
    # replay's wait condition.
    assert by_name["New transfer"]["expect_url"] == "/operations.html#/transfers/new"
    assert by_name["Save transfer"]["expect_url"].startswith("/operations.html#/transfers/")
    assert a.steps[3]["target"] == 'page.get_by_role("combobox", name="Region")'

    result = replay(browser, a.steps, a.data, base_url=server)
    assert result["ok"], result["failed_step"]
    print(f"\nauthoring {authoring_s:.2f}s for {len(a.steps)} steps; fresh replay {result['ms']} ms")
    assert authoring_s < 20 and result["ms"] < 15000


def test_generated_test_passes_under_pytest_with_qamate_conftest(browser, server, tmp_path):
    import project_store as ps
    root = tmp_path / "data"
    project = ps.create_project(str(root), "Dispatch", server)
    tests_root = ps.ensure_tests_scaffold(str(root), project["id"])

    context = browser.new_context()
    a = author_transfer(context.new_page(), server)
    context.close()
    path = write_test(tests_root, "transfers", "TC-TRANSFER-001", a.steps, a.data,
                      description="Create a transfer and see its details",
                      expected="The saved transfer shows North / Delhi")
    source = open(path, encoding="utf-8").read()
    assert 'flow.fill(page.get_by_role("textbox", name="Transfer name"), tc_data["transfer_name"], "Transfer name")' in source
    assert "wait_for_timeout" not in source and "networkidle" not in source
    data = json.load(open(os.path.join(os.path.dirname(path), "test_data.json")))
    assert data["TC-TRANSFER-001"] == {"transfer_name": "Batch 7", "units": "7"}

    env = {**os.environ, "ATS_ROOT": str(root), "ATS_APP_ROOT": APP_ROOT, "ATS_PROJECT_ID": project["id"],
           "PYTHONPATH": APP_ROOT, "ATS_VIDEO": "off", "ATS_TRACING": "off", "ATS_NETWORK": "off",
           "ATS_RESULTS_DIR": str(tmp_path / "results"), "ATS_NO_MANUAL_INPUT": "1"}
    junit = tmp_path / "junit.xml"
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "pytest", os.path.dirname(path), "-q", "-p", "no:cacheprovider",
                           "-c", os.path.join(APP_ROOT, "pytest.ini"), "--rootdir", str(root),
                           f"--junitxml={junit}", "--browser", "chromium"],
                          cwd=str(root), env=env, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    from verification import verified_junit
    assert verified_junit(str(junit), "TC-TRANSFER-001")
    checkpoints = json.load(open(next((tmp_path / "results" / "checkpoints").glob("*.json"))))
    names = [c["name"] for c in (checkpoints.get("checkpoints") if isinstance(checkpoints, dict) else checkpoints)]
    assert "Save transfer" in names and "Flow completed - all steps passed" in names
    print(f"\npytest replay {time.monotonic() - started:.1f}s")


def test_settle_waits_for_slow_api_without_fixed_sleeps(browser, server):
    page = browser.new_page()
    page.goto(server + "operations.html#/transfers")
    page.set_content('<button onclick="fetch(\'/slow-data\').then(r=>r.json()).then(d=>{document.querySelector(\'output\').textContent=d.msg})">'
                     'Load report</button><output></output>')
    flow = Flow(page)
    started = time.monotonic()
    flow.click(page.get_by_role("button", name="Load report"), "Load report")
    elapsed = time.monotonic() - started
    assert page.locator("output").inner_text() == "Report ready"   # no explicit wait needed
    assert 0.6 < elapsed < 2.5, elapsed
    page.close()


def test_replay_reports_the_failing_step(browser, server):
    steps = [{"op": "goto", "value": "operations.html#/transfers", "name": "Open"},
             {"op": "click", "target": 'page.get_by_role("link", name="Archive")', "name": "Open archive"}]
    result = replay(browser, steps, base_url=server, timeout_ms=1500)
    assert not result["ok"]
    assert result["failed_step"]["index"] == 1 and result["failed_step"]["name"] == "Open archive"
    assert "Archive" in result["failed_step"]["error"]


def test_masked_and_numeric_inputs_end_up_exactly_as_typed(browser):
    page = browser.new_page()
    page.set_content('<input aria-label="Units" type="number" value="0">'
                     '<input aria-label="Phone" oninput="const d=this.value.replace(/\\D/g,\'\').slice(0,10);'
                     'this.value=d.length>6?`(${d.slice(0,3)}) ${d.slice(3,6)}-${d.slice(6)}`:d">')
    flow = Flow(page)
    flow.fill(page.get_by_label("Units"), "7", "Units")
    assert page.get_by_label("Units").input_value() == "7"
    flow.fill(page.get_by_label("Phone"), "5551234567", "Phone")
    assert page.get_by_label("Phone").input_value() == "(555) 123-4567"
    page.close()
