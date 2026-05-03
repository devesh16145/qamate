"""
Agrim ATS — Pytest Configuration (conftest.py)

Provides fixtures for:
- Loading app config from config.json
- Resolving the base URL from the active environment
- Setting up video recording directories
- Setting browser timeouts to prevent infinite hangs
- Capturing screenshots on test failure
- Checkpoint-based test reporting (milestones within a single TC)
"""

import pytest
import os
import json
import re
import time
import sys


# ── Config loading ──
def _load_config():
    """Load config.json from the ATS root directory."""
    ats_root = os.environ.get("ATS_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    config_path = os.path.join(ats_root, "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ── Register custom marks to suppress warnings ──
def pytest_configure(config):
    config.addinivalue_line("markers", "tc(id): Associate a test with a Test Case ID")


# ── Checkpoint Reporting ──

def _write_checkpoints(tc_id, checkpoints):
    """Write checkpoint results to a JSON file for the runner to pick up."""
    results_dir = os.environ.get("ATS_RESULTS_DIR", "")
    if not results_dir:
        return
    cp_dir = os.path.join(results_dir, "checkpoints")
    os.makedirs(cp_dir, exist_ok=True)
    safe_id = tc_id.replace("-", "_")
    cp_file = os.path.join(cp_dir, f"{safe_id}.json")
    with open(cp_file, "w", encoding="utf-8") as f:
        json.dump({"tc_id": tc_id, "checkpoints": checkpoints}, f, indent=2)


class CheckpointRunner:
    """Runs checkpoints sequentially. Continues after failure so all checkpoints execute.
    After all checkpoints, raises the first failure."""

    def __init__(self, tc_id):
        self.tc_id = tc_id
        self.checkpoints = []
        self._first_error = None

    def run(self, name, fn, *args, **kwargs):
        """Execute a checkpoint. Returns True if passed, False if failed."""
        cp_entry = {"name": name, "status": "PASS", "error": None}
        print(f"[{self.tc_id}] >> {name}", flush=True)
        try:
            result = fn(*args, **kwargs)
            # If fn returns False explicitly, treat as skip (used for gating subsequent steps)
            if result is False:
                cp_entry["status"] = "SKIP"
                cp_entry["error"] = "Step returned False (condition not met)"
                print(f"[{self.tc_id}] SKIP {name}: condition not met", flush=True)
            else:
                print(f"[{self.tc_id}] OK {name}", flush=True)
        except Exception as e:
            cp_entry["status"] = "FAIL"
            cp_entry["error"] = str(e)[:500]
            if self._first_error is None:
                self._first_error = e
            print(f"[{self.tc_id}] FAIL {name}: {str(e)[:200]}", flush=True)
        self.checkpoints.append(cp_entry)
        # Flush checkpoints to file after each one (so partial results survive early exits)
        _write_checkpoints(self.tc_id, self.checkpoints)
        return cp_entry["status"] == "PASS"

    def skip(self, name, reason="Skipped"):
        """Record a skipped checkpoint (e.g. optional feature not present)."""
        self.checkpoints.append({"name": name, "status": "SKIP", "error": reason})
        print(f"[{self.tc_id}] SKIP {name}: {reason}", flush=True)
        _write_checkpoints(self.tc_id, self.checkpoints)

    def finalize(self):
        """Call after all checkpoints. Raises the first failure if any checkpoint failed."""
        _write_checkpoints(self.tc_id, self.checkpoints)
        if self._first_error is not None:
            raise self._first_error

    @property
    def passed(self):
        return sum(1 for c in self.checkpoints if c["status"] == "PASS")

    @property
    def failed(self):
        return sum(1 for c in self.checkpoints if c["status"] == "FAIL")

    @property
    def summary(self):
        p = self.passed
        f = self.failed
        s = len(self.checkpoints) - p - f
        return f"{p} passed, {f} failed, {s} skipped"


@pytest.fixture(autouse=True)
def checkpoints(request):
    """Provide a CheckpointRunner for every test. Automatically extracts tc_id from test name."""
    tc_name = request.node.name.split("[")[0]
    match = re.search(r'(TC_[A-Z]+_\d+)', tc_name)
    tc_id = match.group(1).replace("_", "-") if match else tc_name
    runner = CheckpointRunner(tc_id)
    yield runner
    # After the test function returns, finalize (raise if any checkpoint failed)
    runner.finalize()


# ── Fixtures ──
@pytest.fixture(scope="session")
def ats_config():
    """Full ATS configuration dictionary."""
    return _load_config()


@pytest.fixture(scope="session")
def test_user(ats_config):
    """The selected user account for this test run."""
    user_index = int(os.environ.get("ATS_USER_INDEX", "0"))
    users = ats_config.get("users", [])
    if user_index < len(users):
        return users[user_index]
    return users[0] if users else {"email": "", "password": "", "label": "Default"}


@pytest.fixture(scope="session")
def base_url(ats_config):
    """Base URL for the active environment (dev/staging)."""
    env_name = os.environ.get("ATS_ENV", "dev")
    env_config = ats_config.get("environments", {}).get(env_name, {})
    url = env_config.get("base_url", "https://supplier-dev.agrim.app/")
    return url if url.endswith("/") else url + "/"


@pytest.fixture(scope="session")
def admin_url(ats_config):
    """Admin panel URL for the active environment."""
    env_name = os.environ.get("ATS_ENV", "dev")
    env_config = ats_config.get("environments", {}).get(env_name, {})
    url = env_config.get("admin_url", "https://admin-dev.agrim.app/")
    return url if url.endswith("/") else url + "/"


@pytest.fixture(scope="session")
def results_dir():
    """Directory where this run's artifacts are stored."""
    d = os.environ.get("ATS_RESULTS_DIR", os.path.join(os.path.dirname(__file__), "results", "manual"))
    os.makedirs(d, exist_ok=True)
    return d


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, results_dir):
    """Extend playwright browser context with zoom-scaled viewport.
    In sequential mode, skip video here — sequential_page handles its own recording."""
    base_w, base_h = 1280, 720

    # Zoom calculation
    zoom_str = os.environ.get("ATS_ZOOM", "")
    vp_w, vp_h = base_w, base_h
    if zoom_str:
        try:
            zoom_pct = int(zoom_str)
            if 1 <= zoom_pct <= 500 and zoom_pct != 100:
                factor = zoom_pct / 100.0
                vp_w = round(base_w / factor)
                vp_h = round(base_h / factor)
        except ValueError:
            pass

    args = {
        **browser_context_args,
        "viewport": {"width": vp_w, "height": vp_h},
    }

    # In sequential mode, don't add video — sequential_page handles it
    if os.environ.get("ATS_EXEC_MODE") != "sequential":
        video_dir = os.path.join(results_dir, "videos")
        os.makedirs(video_dir, exist_ok=True)
        args["record_video_dir"] = video_dir
        args["record_video_size"] = {"width": base_w, "height": base_h}

    return args


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Set browser launch args."""
    return {
        **browser_type_launch_args,
        "args": ["--disable-gpu", "--no-sandbox"],
    }


@pytest.fixture(scope="session")
def sequential_page(browser_type, browser_type_launch_args, browser_context_args, test_user, base_url, results_dir):
    """Session-scoped page for sequential mode — login once, reuse for all tests.
    Returns None when not in sequential mode (tests fall back to per-test login)."""
    if os.environ.get("ATS_EXEC_MODE") != "sequential":
        yield None
        return

    browser = browser_type.launch(**browser_type_launch_args)

    # Set up video recording for the sequential session
    video_dir = os.path.join(results_dir, "videos")
    os.makedirs(video_dir, exist_ok=True)

    ctx_args = {k: v for k, v in browser_context_args.items() if k not in ("record_video_dir", "record_video_size")}
    ctx_args["record_video_dir"] = video_dir
    ctx_args["record_video_size"] = {"width": 1280, "height": 720}

    context = browser.new_context(**ctx_args)
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)

    # Login once
    page.goto(base_url + "login", wait_until="domcontentloaded")
    email_field = page.locator('input[placeholder*="Email"], input[type="email"]').first
    password_field = page.locator('input[type="password"]').first
    sign_in_button = page.locator('button:has-text("Sign In"), button[type="submit"]').first
    email_field.wait_for(state="visible", timeout=15000)
    email_field.click()
    page.keyboard.type(test_user["email"], delay=50)
    password_field.click()
    page.keyboard.type(test_user["password"], delay=50)
    sign_in_button.click()
    page.wait_for_timeout(3000)
    page.wait_for_load_state("domcontentloaded")

    yield page

    context.close()
    browser.close()


@pytest.fixture(autouse=True)
def set_page_timeouts(page):
    """Set generous but finite timeouts on every page to prevent infinite hangs."""
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)
    yield page


# ── Screenshot on failure ──
@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store test result on the item node so fixtures can access it."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, "rep_" + rep.when, rep)


@pytest.fixture(autouse=True)
def capture_screenshot_on_failure(page, request, results_dir, sequential_page):
    """After each test, capture a screenshot AND page error text if the test failed.
    Also stashes the video path for later renaming."""
    active_page = sequential_page if sequential_page is not None else page
    try:
        if active_page.video:
            request.node._video_path = active_page.video.path()
    except Exception:
        pass

    yield
    rep = getattr(request.node, "rep_call", None)
    if rep and rep.failed:
        tc_name = request.node.name.split("[")[0]
        bracket = re.search(r'\[(.+?)\]', request.node.name)
        if bracket:
            tc_name = f"{tc_name}_{bracket.group(1)}"
        screenshot_dir = os.path.join(results_dir, "screenshots")
        os.makedirs(screenshot_dir, exist_ok=True)

        try:
            active_page.screenshot(path=os.path.join(screenshot_dir, f"{tc_name}_FAILED.png"))
        except Exception:
            pass

        # Capture visible page errors
        try:
            error_selectors = [
                '[role="alert"]',
                '[class*="toast"]',
                '[class*="error-message"]',
                '[class*="text-destructive"]',
                '.text-red-500',
                '[class*="error"]:visible',
            ]
            errors = []
            for sel in error_selectors:
                try:
                    els = active_page.locator(sel)
                    for i in range(min(els.count(), 5)):
                        text = els.nth(i).text_content()
                        if text and text.strip():
                            errors.append(f"[{sel}] {text.strip()[:300]}")
                except Exception:
                    pass

            try:
                body_text = active_page.locator("body").text_content() or ""
                for line in body_text.split("\n"):
                    line = line.strip()
                    if line and any(kw in line.lower() for kw in ["error", "fail", "invalid", "required", "cannot", "unable", "denied"]):
                        if line not in errors:
                            errors.append(f"[body] {line[:300]}")
            except Exception:
                pass

            if errors:
                error_log_path = os.path.join(screenshot_dir, f"{tc_name}_ERRORS.txt")
                with open(error_log_path, "w", encoding="utf-8") as f:
                    f.write(f"Test: {tc_name}\n")
                    f.write(f"Result: FAILED\n")
                    f.write(f"Errors found on page:\n")
                    f.write("=" * 60 + "\n")
                    for err in errors:
                        f.write(err + "\n")
        except Exception:
            pass


def _extract_tc_id(node_name):
    """Extract TC ID from pytest node name."""
    tc_name = node_name.split("[")[0]
    match = re.search(r'(TC_[A-Z]+_\d+)', tc_name)
    if match:
        return match.group(1).replace("_", "-")
    return tc_name


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """After all fixtures teardown (page closed), rename video to TC ID."""
    yield
    video_path = getattr(item, "_video_path", None)
    if not video_path:
        return

    tc_id = _extract_tc_id(item.name)
    bracket = re.search(r'\[(.+?)\]', item.name)
    variant_suffix = f"_{bracket.group(1)}" if bracket else ""
    new_path = os.path.join(os.path.dirname(video_path), f"{tc_id}{variant_suffix}.webm")

    for _ in range(10):
        if os.path.exists(video_path):
            break
        time.sleep(0.3)

    try:
        if os.path.exists(video_path):
            os.rename(video_path, new_path)
    except Exception:
        pass


# ── Test Data (supports variants) ──

def _is_variants(data):
    """Check if test data is in variants format (dict of dicts)."""
    return isinstance(data, dict) and bool(data) and all(
        isinstance(v, dict) for v in data.values()
    )


def pytest_generate_tests(metafunc):
    """Parametrize tests that have multiple data variants in test_data.json."""
    tc_name = metafunc.definition.function.__name__
    match = re.search(r'test_(TC_\w+?)_', tc_name)
    if not match:
        return
    tc_id = match.group(1).replace("_", "-")

    data_file = os.path.join(os.path.dirname(metafunc.module.__file__), "test_data.json")
    if not os.path.exists(data_file):
        return

    try:
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return

    tc_raw = data.get(tc_id, {})
    if _is_variants(tc_raw) and len(tc_raw) > 1:
        variant_names = list(tc_raw.keys())
        metafunc.parametrize("tc_data", variant_names, indirect=True, ids=variant_names)


@pytest.fixture(scope="function")
def tc_data(request):
    """Load test data for the current TC. Supports variants and flat data."""
    tc_name = request.node.name.split("[")[0]
    tc_id = None
    match = re.search(r'test_(TC_\w+?)_', tc_name)
    if match:
        tc_id = match.group(1).replace("_", "-")

    if not tc_id:
        return {}

    variant = getattr(request, 'param', None)

    test_file_path = request.module.__file__
    flow_dir = os.path.dirname(test_file_path)
    data_file = os.path.join(flow_dir, "test_data.json")

    if not os.path.exists(data_file):
        return {}

    try:
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            tc_raw = data.get(tc_id, {})

            if _is_variants(tc_raw):
                if variant and variant in tc_raw:
                    return tc_raw[variant]
                return next(iter(tc_raw.values()), {})
            else:
                return tc_raw
    except Exception:
        return {}
