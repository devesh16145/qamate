"""
Agrim ATS — Pytest Configuration (conftest.py)

Multi-platform architecture:
- Platforms (seller, admin) each have their own URL and user list in config.json
- Login happens once per platform per session (storage_state saved)
- Each test gets a fresh page with platform cookies applied
- Every test has exactly ONE video (from its own page context)
- Sequential/parallel is just execution strategy, not platform selection
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

    def mark_passed(self, name):
        """Directly record a passed checkpoint."""
        cp_entry = {"name": name, "status": "PASS", "error": None}
        self.checkpoints.append(cp_entry)
        print(f"[{self.tc_id}] OK {name}", flush=True)
        _write_checkpoints(self.tc_id, self.checkpoints)

    def mark_failed(self, name, error_msg="Assertion Failed"):
        """Directly record a failed checkpoint and raise the error."""
        cp_entry = {"name": name, "status": "FAIL", "error": error_msg}
        self.checkpoints.append(cp_entry)
        print(f"[{self.tc_id}] FAIL {name}: {error_msg}", flush=True)
        if self._first_error is None:
            self._first_error = AssertionError(error_msg)
        _write_checkpoints(self.tc_id, self.checkpoints)
        raise self._first_error

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


# ── Platform helpers ──

def _get_platform_config(ats_config, platform_name):
    """Get platform config from the new platforms section."""
    return ats_config.get("platforms", {}).get(platform_name, {})


def _get_platform_url(ats_config, platform_name):
    """Get platform URL for the active environment."""
    env = os.environ.get("ATS_ENV", "dev")
    platform = _get_platform_config(ats_config, platform_name)
    url = platform.get("urls", {}).get(env, "")
    if not url:
        return ""
    return url if url.endswith("/") else url + "/"


def _get_platform_user(ats_config, platform_name):
    """Get the selected user for a platform."""
    env_var = f"ATS_{platform_name.upper()}_USER_INDEX"
    user_index = int(os.environ.get(env_var, "0"))
    users = _get_platform_config(ats_config, platform_name).get("users", [])
    if user_index < len(users):
        return users[user_index]
    return users[0] if users else {"email": "", "password": "", "label": "Default"}


def _do_platform_login(browser, ats_config, platform_name):
    """Login to a platform in a temporary context, return storage_state."""
    platform = _get_platform_config(ats_config, platform_name)
    user = _get_platform_user(ats_config, platform_name)
    env = os.environ.get("ATS_ENV", "dev")
    url = platform.get("urls", {}).get(env, "")
    login_path = platform.get("login_path", "login")
    full_url = url + login_path

    print(f"[{platform_name.upper()} LOGIN] Starting login to {full_url}", flush=True)
    print(f"[{platform_name.upper()} LOGIN] User: {user.get('email', 'N/A')}", flush=True)

    if not url:
        print(f"[{platform_name.upper()} LOGIN] ERROR: No URL configured for env '{env}'", flush=True)
        return {"cookies": [], "origins": []}

    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)

    print(f"[{platform_name.upper()} LOGIN] Navigating to {full_url}...", flush=True)
    page.goto(full_url, wait_until="domcontentloaded")
    # SPA apps (React/Vue) need extra time to render after domcontentloaded
    page.wait_for_timeout(5000)
    print(f"[{platform_name.upper()} LOGIN] Page loaded. URL: {page.url}", flush=True)

    # Generic login: works for both seller app and admin panel
    # Try multiple selectors in priority order
    email_field = page.locator(
        'input[name="username"], input[type="email"], input[placeholder*="Email"], input[placeholder*="email"]'
    ).first
    password_field = page.locator('input[type="password"]').first
    submit_btn = page.locator(
        'button[type="submit"], button:has-text("Sign In"), button:has-text("Sign in"), button:has-text("Log In"), button:has-text("Login")'
    ).first

    # Wait for form with generous timeout (SPA may take time to render)
    print(f"[{platform_name.upper()} LOGIN] Waiting for login form...", flush=True)
    try:
        email_field.wait_for(state="visible", timeout=20000)
    except Exception as e:
        print(f"[{platform_name.upper()} LOGIN] ERROR: Login form not found: {e}", flush=True)
        print(f"[{platform_name.upper()} LOGIN] Page URL: {page.url}", flush=True)
        context.close()
        return {"cookies": [], "origins": []}

    print(f"[{platform_name.upper()} LOGIN] Form found. Filling credentials...", flush=True)
    email_field.fill(user["email"])
    password_field.fill(user["password"])
    submit_btn.click()
    print(f"[{platform_name.upper()} LOGIN] Submit clicked. Waiting for redirect...", flush=True)

    # Wait for post-login redirect (admin SPA needs 5-6s)
    page.wait_for_timeout(6000)
    page.wait_for_load_state("domcontentloaded")
    # Extra wait for hash-based SPA routing to complete
    if "#" in login_path:
        page.wait_for_timeout(3000)

    # Verify login succeeded (URL should no longer contain login path)
    current_url = page.url
    login_indicator = login_path.replace("#", "").replace("/", "")
    if login_indicator and login_indicator.lower() in current_url.lower():
        print(f"[{platform_name.upper()} LOGIN] WARNING: Still on login page after submit: {current_url}", flush=True)
        # Retry: wait longer and check again
        page.wait_for_timeout(5000)
        current_url = page.url
        if login_indicator.lower() in current_url.lower():
            print(f"[{platform_name.upper()} LOGIN] FAILED: Still on login page after retry: {current_url}", flush=True)
        else:
            print(f"[{platform_name.upper()} LOGIN] SUCCESS (after retry): {current_url}", flush=True)
    else:
        print(f"[{platform_name.upper()} LOGIN] SUCCESS: {current_url}", flush=True)

    state = context.storage_state()
    cookie_count = len(state.get("cookies", []))
    print(f"[{platform_name.upper()} LOGIN] Captured {cookie_count} cookies", flush=True)
    page.close()
    context.close()
    return state


def _apply_login_state(page, state, url):
    """Apply saved login state (cookies + localStorage) to a page."""
    cookies = state.get("cookies", [])
    origins = state.get("origins", [])
    print(f"[LOGIN STATE] Applying to {url}: {len(cookies)} cookies, {len(origins)} origins", flush=True)
    if not cookies and not origins:
        print(f"[LOGIN STATE] WARNING: Empty login state — page will NOT be logged in!", flush=True)
    page.context.add_cookies(cookies)
    page.goto(url, wait_until="domcontentloaded")
    for origin_data in origins:
        for item in origin_data.get("localStorage", []):
            try:
                page.evaluate(
                    f"localStorage.setItem({json.dumps(item['name'])}, {json.dumps(item['value'])})"
                )
            except Exception:
                pass
    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(2000)


# ── Fixtures ──
@pytest.fixture(scope="session")
def ats_config():
    """Full ATS configuration dictionary."""
    return _load_config()


@pytest.fixture(scope="session")
def results_dir():
    """Directory where this run's artifacts are stored."""
    d = os.environ.get("ATS_RESULTS_DIR", os.path.join(os.path.dirname(__file__), "results", "manual"))
    os.makedirs(d, exist_ok=True)
    return d


@pytest.fixture(scope="session")
def seller_url(ats_config):
    """Seller app URL for the active environment."""
    return _get_platform_url(ats_config, "seller")


@pytest.fixture(scope="session")
def admin_url(ats_config):
    """Admin panel URL for the active environment."""
    return _get_platform_url(ats_config, "admin")


@pytest.fixture(scope="session")
def base_url(seller_url):
    """Seller app URL — backward compatibility alias."""
    return seller_url


@pytest.fixture(scope="session")
def test_user(ats_config):
    """Selected seller user — backward compatibility."""
    return _get_platform_user(ats_config, "seller")


@pytest.fixture(scope="session")
def admin_user(ats_config):
    """Selected admin user."""
    return _get_platform_user(ats_config, "admin")


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, results_dir):
    """Extend playwright browser context with video recording and no forced viewport."""
    args = {
        **browser_context_args,
        "no_viewport": True,
    }

    # Always enable video — each test gets its own context with video
    video_dir = os.path.join(results_dir, "videos")
    os.makedirs(video_dir, exist_ok=True)
    args["record_video_dir"] = video_dir
    args["record_video_size"] = {"width": 1920, "height": 1080}
    return args


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Launch real Chrome (not Chromium) maximized."""
    return {
        **browser_type_launch_args,
        "channel": "chrome",
        "args": [
            "--disable-gpu",
            "--no-sandbox",
            "--start-maximized",
        ],
    }


@pytest.fixture(scope="session")
def seller_login_state(browser, ats_config):
    """Login to seller app once, save state (cookies + localStorage) for reuse."""
    try:
        return _do_platform_login(browser, ats_config, "seller")
    except Exception as e:
        print(f"[WARNING] Seller login failed: {e}", flush=True)
        return {"cookies": [], "origins": []}


@pytest.fixture(scope="session")
def admin_login_state(browser, ats_config):
    """Login to admin panel once, save state for reuse."""
    print("[FIXTURE] admin_login_state invoked", flush=True)
    try:
        state = _do_platform_login(browser, ats_config, "admin")
        has_cookies = len(state.get("cookies", [])) > 0
        print(f"[FIXTURE] admin_login_state result: cookies_present={has_cookies}", flush=True)
        return state
    except Exception as e:
        print(f"[FIXTURE] admin_login_state EXCEPTION: {e}", flush=True)
        return {"cookies": [], "origins": []}


@pytest.fixture
def seller_page(page, seller_login_state, seller_url):
    """Per-test page logged into seller app. Has its own video recording."""
    _apply_login_state(page, seller_login_state, seller_url)
    return page


@pytest.fixture
def admin_page(browser, browser_context_args, admin_login_state, admin_url, request):
    """Per-test page logged into admin panel. Own browser context so it can
    coexist with seller_page in the same test."""
    has_cookies = len(admin_login_state.get("cookies", [])) > 0
    print(f"[FIXTURE] admin_page: login_state_valid={has_cookies}, admin_url={admin_url}", flush=True)
    context = browser.new_context(**browser_context_args)
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)
    _apply_login_state(page, admin_login_state, admin_url)
    print(f"[FIXTURE] admin_page: ready. Current URL: {page.url}", flush=True)
    # Track video path so screenshot/video fixtures know about this context
    try:
        if page.video:
            request.node._admin_video_path = page.video.path()
            request.node._admin_page = page
    except Exception:
        pass
    yield page
    page.close()
    context.close()


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
def capture_screenshot_on_failure(page, request, results_dir):
    """After each test, capture a screenshot AND page error text if the test failed.
    Stashes the video path for later renaming."""
    try:
        if page.video:
            request.node._video_path = page.video.path()
    except Exception:
        pass

    yield

    # Prefer admin page for screenshots if test used admin_page
    active_page = getattr(request.node, '_admin_page', page)
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
    # Prefer admin video path if test used admin_page
    video_path = getattr(item, "_admin_video_path", None) or getattr(item, "_video_path", None)
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

    # Clean up any leftover hash-named .webm files (e.g. from sequential_page
    # session recording) so only TC-named videos appear in history
    try:
        video_dir = os.path.dirname(new_path)
        if os.path.exists(video_dir):
            for f in os.listdir(video_dir):
                if f.endswith('.webm') and not re.match(r'TC[_-]', f):
                    try:
                        os.remove(os.path.join(video_dir, f))
                    except Exception:
                        pass
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
    match = re.search(r'(TC_[A-Z]+_\d+)', tc_name)
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
        target_variant = os.environ.get("ATS_VARIANT")
        if target_variant and target_variant in tc_raw:
            variant_names = [target_variant]
        else:
            variant_names = list(tc_raw.keys())
        metafunc.parametrize("tc_data", variant_names, indirect=True, ids=variant_names)


@pytest.fixture(scope="function")
def tc_data(request):
    """Load test data for the current TC. Supports variants and flat data."""
    tc_name = request.node.name.split("[")[0]
    tc_id = None
    # Use greedy match to capture full TC ID (e.g. TC_ORDERS_063, not just TC_ORDERS)
    match = re.search(r'(TC_[A-Z]+_\d+)', tc_name)
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
