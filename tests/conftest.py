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
import contextlib

# Self-healing locator engine (engine/smart_locator.py). Added to sys.path so it
# imports under both the Electron runner (PYTHONPATH=ats_root) and bare pytest.
_ENGINE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine")
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)
from smart_locator import Healer, smart_locator as _smart_locator
import project_store


# ── Project mode (general-purpose) ───────────────────────────────────────────
# A run targets a Project only when the runner explicitly sets ATS_PROJECT_ID.
# Without it we stay in legacy mode (Agrim's config.json `platforms`), so all
# existing Agrim tests behave exactly as before.

def _ats_root():
    return os.environ.get("ATS_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _active_project():
    pid = os.environ.get("ATS_PROJECT_ID")
    if not pid:
        return None
    try:
        return project_store.get_project(_ats_root(), pid)
    except Exception:
        return None


def _project_storage_state(project):
    """A project's captured Playwright storage_state (cookies + origins), or empty."""
    if not project:
        return {"cookies": [], "origins": []}
    try:
        ss = project_store.storage_state_path(_ats_root(), project["id"])
        if os.path.exists(ss):
            with open(ss, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"cookies": [], "origins": []}


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

_SEVERITIES = ("critical", "normal", "minor")

_DEFAULT_CRITERIA = {
    "mode": "critical_only",       # critical_only | all | threshold
    "threshold_pct": 90.0,
    "treat_skipped_as": "ignore",  # ignore | fail | pass
    "default_severity": "critical",
}


def _criteria_with_defaults(raw):
    """Merge config pass_criteria over safe defaults and validate."""
    c = dict(_DEFAULT_CRITERIA)
    if isinstance(raw, dict):
        for k in _DEFAULT_CRITERIA:
            if raw.get(k) is not None:
                c[k] = raw[k]
    if c["mode"] not in ("critical_only", "all", "threshold"):
        c["mode"] = "critical_only"
    if c["default_severity"] not in _SEVERITIES:
        c["default_severity"] = "critical"
    if c["treat_skipped_as"] not in ("ignore", "fail", "pass"):
        c["treat_skipped_as"] = "ignore"
    try:
        c["threshold_pct"] = float(c["threshold_pct"])
    except Exception:
        c["threshold_pct"] = 90.0
    return c


def _write_checkpoints(tc_id, checkpoints, verdict=None, criteria=None):
    """Persist checkpoint results (and, once known, the criteria verdict) so the
    runner / results loader / UI can show per-checkpoint detail. Rewritten after
    every checkpoint so partial results survive an early exit. Falls back to a
    manual results dir when ATS_RESULTS_DIR is unset (never silently dropped).
    tc_id carries any variant suffix, so parametrized variants don't collide."""
    results_dir = os.environ.get("ATS_RESULTS_DIR") or os.path.join(
        os.path.dirname(__file__), "results", "manual")
    cp_dir = os.path.join(results_dir, "checkpoints")
    os.makedirs(cp_dir, exist_ok=True)
    safe_id = tc_id.replace("-", "_")
    cp_file = os.path.join(cp_dir, f"{safe_id}.json")
    payload = {"tc_id": tc_id, "checkpoints": checkpoints}
    if verdict is not None:
        payload["verdict"] = verdict
    if criteria is not None:
        payload["criteria"] = criteria
    with open(cp_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


class CheckpointRunner:
    """Runs checkpoints sequentially, continuing after failure so every checkpoint
    executes. The verdict is decided by the configured pass criteria rather than
    'first exception wins':
      - critical_only : only a failed CRITICAL checkpoint fails the test;
                        normal/minor failures become non-blocking warnings
      - all           : any failed checkpoint fails the test
      - threshold     : pass if >= threshold_pct of checkpoints pass AND every
                        critical one passes
    Skipped checkpoints are handled per treat_skipped_as (ignore|fail|pass)."""

    def __init__(self, tc_id, criteria=None):
        self.tc_id = tc_id
        self.checkpoints = []
        self._first_error = None
        self._first_critical_error = None
        self.criteria = _criteria_with_defaults(criteria)
        self.verdict = None
        self._finalized = False

    def _default_severity(self):
        return self.criteria.get("default_severity", "critical")

    def run(self, name, fn, *args, severity=None, **kwargs):
        """Execute a checkpoint. `severity` is critical|normal|minor; critical
        is blocking, normal/minor are warnings under critical_only mode.
        Returns True if the checkpoint passed."""
        sev = severity if severity in _SEVERITIES else self._default_severity()
        cp_entry = {"name": name, "status": "PASS", "error": None, "severity": sev}
        print(f"[{self.tc_id}] >> {name}", flush=True)
        try:
            result = fn(*args, **kwargs)
            # An explicit False return means "condition not met" → skip (used to gate later steps)
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
            if sev == "critical" and self._first_critical_error is None:
                self._first_critical_error = e
            tag = "FAIL" if sev == "critical" else f"WARN[{sev}]"
            print(f"[{self.tc_id}] {tag} {name}: {str(e)[:200]}", flush=True)
        cp_entry["ts"] = int(time.time() * 1000)   # step end — used to bucket network calls per step
        self.checkpoints.append(cp_entry)
        _write_checkpoints(self.tc_id, self.checkpoints)
        return cp_entry["status"] == "PASS"

    @contextlib.contextmanager
    def step(self, name, severity=None):
        """Context manager wrapping ONE recorded step. Records `name` as a checkpoint —
        PASS if the block completes, FAIL (with the exception text) if it raises — then
        re-raises so the test still fails at that step. This is what gives generated /
        recorded tests a per-step execution timeline (instead of 'No step checkpoints
        recorded') when they die mid-flow."""
        sev = severity if severity in _SEVERITIES else self._default_severity()
        print(f"[{self.tc_id}] >> {name}", flush=True)
        try:
            yield
        except Exception as e:
            self.checkpoints.append({"name": name, "status": "FAIL", "error": str(e)[:500],
                                     "severity": sev, "ts": int(time.time() * 1000)})
            if self._first_error is None:
                self._first_error = e
            if sev == "critical" and self._first_critical_error is None:
                self._first_critical_error = e
            print(f"[{self.tc_id}] FAIL {name}: {str(e)[:200]}", flush=True)
            _write_checkpoints(self.tc_id, self.checkpoints)
            raise
        else:
            self.checkpoints.append({"name": name, "status": "PASS", "error": None,
                                     "severity": sev, "ts": int(time.time() * 1000)})
            print(f"[{self.tc_id}] OK {name}", flush=True)
            _write_checkpoints(self.tc_id, self.checkpoints)

    def mark_passed(self, name):
        """Directly record a passed checkpoint."""
        self.checkpoints.append({"name": name, "status": "PASS", "error": None, "severity": "critical", "ts": int(time.time() * 1000)})
        print(f"[{self.tc_id}] OK {name}", flush=True)
        _write_checkpoints(self.tc_id, self.checkpoints)

    def mark_failed(self, name, error_msg="Assertion Failed"):
        """Directly record a hard (critical) failure and raise immediately."""
        self.checkpoints.append({"name": name, "status": "FAIL", "error": error_msg, "severity": "critical", "ts": int(time.time() * 1000)})
        print(f"[{self.tc_id}] FAIL {name}: {error_msg}", flush=True)
        if self._first_error is None:
            self._first_error = AssertionError(error_msg)
        if self._first_critical_error is None:
            self._first_critical_error = AssertionError(error_msg)
        _write_checkpoints(self.tc_id, self.checkpoints)
        raise self._first_critical_error

    def skip(self, name, reason="Skipped"):
        """Record a skipped checkpoint (e.g. optional feature not present)."""
        self.checkpoints.append({"name": name, "status": "SKIP", "error": reason, "severity": "minor", "ts": int(time.time() * 1000)})
        print(f"[{self.tc_id}] SKIP {name}: {reason}", flush=True)
        _write_checkpoints(self.tc_id, self.checkpoints)

    def _evaluate(self):
        """Decide the verdict from the configured criteria. Returns (verdict, error_or_None)."""
        fails = [c for c in self.checkpoints if c["status"] == "FAIL"]
        crit_fails = [c for c in fails if c.get("severity") == "critical"]
        skips = [c for c in self.checkpoints if c["status"] == "SKIP"]
        treat_skip = self.criteria.get("treat_skipped_as", "ignore")
        mode = self.criteria.get("mode", "critical_only")
        skip_fails = len(skips) if treat_skip == "fail" else 0

        if mode == "all":
            failed = (len(fails) + skip_fails) > 0
        elif mode == "threshold":
            considered = [c for c in self.checkpoints if c["status"] != "SKIP"]
            if treat_skip != "ignore":
                considered = considered + skips
            total = max(1, len(considered))
            passes = sum(1 for c in considered
                         if c["status"] == "PASS" or (c["status"] == "SKIP" and treat_skip == "pass"))
            pct = passes / total * 100.0
            failed = pct < self.criteria.get("threshold_pct", 90.0) or len(crit_fails) > 0
        else:  # critical_only
            failed = len(crit_fails) > 0 or skip_fails > 0

        if failed:
            err = self._first_critical_error or self._first_error or AssertionError(
                f"Pass criteria '{mode}' not met: {len(crit_fails)} critical failure(s), "
                f"{len(fails)} total failure(s), {len(skips)} skipped.")
            return "FAIL", err
        non_critical_fails = [c for c in fails if c.get("severity") != "critical"]
        return ("PASS_WITH_WARNINGS" if non_critical_fails else "PASS"), None

    def finalize(self, raise_on_fail=True):
        """Decide and persist the verdict. Idempotent. Raises the criteria
        failure iff the verdict is FAIL and raise_on_fail is True."""
        if self._finalized:
            return self.verdict
        verdict, err = self._evaluate()
        self.verdict = verdict
        self._finalized = True
        _write_checkpoints(self.tc_id, self.checkpoints, verdict=verdict, criteria=self.criteria)
        if err is not None and raise_on_fail:
            raise err
        return verdict

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
def checkpoints(request, ats_config):
    """Provide a CheckpointRunner for every test, wired to the configured pass
    criteria. tc_id includes any variant suffix so per-variant checkpoint files
    don't overwrite each other."""
    tc_id = _tc_id_with_variant(request.node)
    runner = CheckpointRunner(tc_id, criteria=(ats_config or {}).get("pass_criteria"))
    request.node._checkpoint_runner = runner
    yield runner
    # Normally finalized by the pytest_runtest_call hook (so a criteria FAIL is
    # reported as a call-phase FAILURE, not a teardown error). Safety net for
    # paths where that hook didn't run; never raise during teardown.
    if not runner._finalized:
        runner.finalize(raise_on_fail=False)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Apply the checkpoint criteria verdict to the test's CALL phase, so a
    criteria FAIL is reported as a clean FAILED (consistent live and in history)
    rather than a passed call + teardown error."""
    outcome = yield
    runner = getattr(item, "_checkpoint_runner", None)
    if runner is None or runner._finalized:
        return
    if outcome.excinfo is not None:
        # The test body itself raised — persist the verdict but let that
        # primary exception stand as the failure.
        runner.finalize(raise_on_fail=False)
        return
    try:
        runner.finalize(raise_on_fail=True)
    except Exception as e:
        if hasattr(outcome, "force_exception"):
            outcome.force_exception(e)   # attribute the FAIL to the call phase
        else:
            raise


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
    # Try multiple selectors in priority order.
    # NOTE: the dev seller login page renders TWO copies of the form (one hidden,
    # a responsive duplicate). A bare .first can resolve to the HIDDEN copy, so we
    # fill an empty form and the submit silently no-ops ("Still on login page after
    # submit"). Scope every control to the VISIBLE one with .filter(visible=True).
    email_field = page.locator(
        'input[name="username"], input[type="email"], input[placeholder*="Email"], input[placeholder*="email"]'
    ).filter(visible=True).first
    password_field = page.locator('input[type="password"]').filter(visible=True).first
    submit_btn = page.locator(
        'button[type="submit"], button:has-text("Sign In"), button:has-text("Sign in"), button:has-text("Log In"), button:has-text("Login")'
    ).filter(visible=True).first

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


# ── Playwright Trace recording ──────────────────────────────────────────────
# A trace is a zip capturing a full DOM snapshot, network log, console, and
# before/after screenshot for every action — opened with `playwright show-trace`
# for time-travel debugging. This is the industry-standard way to diagnose an
# E2E failure (far richer than a single screenshot + video). Traces are saved
# to results/<timestamp>/traces/<tc_id>.zip. All trace handling is wrapped in
# try/except: a tracing problem must NEVER fail or mask a real test result.

_BROWSER_NAMES = {"chromium", "firefox", "webkit"}


def _tc_id_with_variant(node):
    """Hyphenated TC id plus any *data* variant suffix (e.g. TC-CATALOG-010_negative).

    pytest-playwright always parametrizes browser_name, so node names look like
    `test_TC_X_001[chromium]` or `test_TC_X_001[chromium-negative]` when a data
    variant is also present. We strip the browser-name token(s) so the artifact
    name stays clean (TC-X-001) while a real data variant is preserved."""
    tc_name = node.name.split("[")[0]
    m = re.search(r'(TC_[A-Z]+(?:_[A-Z]+|_\d+)+)', tc_name)
    tc_id = m.group(1).replace("_", "-") if m else tc_name
    bracket = re.search(r'\[(.+?)\]', node.name)
    if bracket:
        tokens = [t for t in bracket.group(1).split("-") if t and t.lower() not in _BROWSER_NAMES]
        if tokens:
            tc_id += "_" + "-".join(tokens)
    return tc_id


def _trace_mode(ats_config):
    """Resolve tracing mode: ATS_TRACING env override > config.json > default."""
    mode = os.environ.get("ATS_TRACING") or ats_config.get("tracing", {}).get("mode", "retain-on-failure")
    mode = str(mode).strip().lower()
    return mode if mode in ("on", "off", "retain-on-failure") else "retain-on-failure"


def _should_save_trace(mode, failed):
    """Given the mode and whether the test failed, should the trace be kept?"""
    if mode == "off":
        return False
    if mode == "on":
        return True
    return bool(failed)  # retain-on-failure


def _start_trace(context, title, mode):
    """Begin tracing on a context. Returns True if started. Fail-safe."""
    if mode == "off":
        return False
    try:
        context.tracing.start(title=title, screenshots=True, snapshots=True, sources=True)
        return True
    except Exception as e:
        print(f"[TRACE] start failed: {e}", flush=True)
        return False


def _finalize_trace(context, save, target_path):
    """Stop tracing; write the zip if save else discard. Fail-safe."""
    try:
        if save:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            context.tracing.stop(path=target_path)
            print(f"[TRACE] Saved {target_path}", flush=True)
        else:
            context.tracing.stop()
    except Exception as e:
        print(f"[TRACE] stop failed: {e}", flush=True)


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


# ── Manual-input gate: pause the test for a value only a human can supply ────
# (an OTP that arrives on a phone, a captcha answer, ...). The runner watches
# <results>/manual_input/ and surfaces the prompt in the app; the user's answer
# is written to the response file and the test resumes.

def _await_manual_response(resp_path, timeout_s):
    """Poll for the response file. Returns the dict, or None on timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if os.path.exists(resp_path):
            try:
                with open(resp_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass  # written half-way — retry
        time.sleep(0.5)
    return None


@pytest.fixture
def manual_input(request, results_dir):
    """Ask the human for a value mid-test (e.g. the OTP sent to their phone):

        otp = manual_input("Enter the OTP sent to +91-98xxx")
        page.get_by_role("textbox", name="OTP").fill(otp)

    The run PAUSES with an amber prompt in the app until the value is typed.
    Generated tests use this automatically when a recorded fill value carries the
    '__MANUAL__:<prompt>' sentinel. Timeout (ATS_MANUAL_INPUT_TIMEOUT, default
    180s) fails the test; ATS_NO_MANUAL_INPUT=1 (unattended/CI/agent-verify runs)
    skips it instead — a human-gated test must never rot a pipeline."""
    tc_name = request.node.name

    def _ask(prompt, timeout_s=None):
        if os.environ.get("ATS_NO_MANUAL_INPUT"):
            pytest.skip(f"requires manual input ({prompt}) — run attended from the app")
        timeout_s = float(timeout_s or os.environ.get("ATS_MANUAL_INPUT_TIMEOUT") or 180)
        mi_dir = os.path.join(results_dir, "manual_input")
        os.makedirs(mi_dir, exist_ok=True)
        rid = os.urandom(6).hex()
        resp_path = os.path.join(mi_dir, f"{rid}.response.json")
        with open(os.path.join(mi_dir, f"{rid}.request.json"), "w", encoding="utf-8") as f:
            json.dump({"id": rid, "prompt": str(prompt), "tc": tc_name,
                       "response_path": resp_path}, f)
        resp = _await_manual_response(resp_path, timeout_s)
        if resp is None:
            pytest.fail(f"manual input timed out after {int(timeout_s)}s: {prompt}")
        if resp.get("cancel"):
            pytest.fail(f"manual input cancelled by the user: {prompt}")
        return str(resp.get("value", ""))

    return _ask


@pytest.fixture(scope="session")
def active_project():
    """The Project this run targets, or None for legacy (Agrim platforms) mode."""
    return _active_project()


@pytest.fixture(scope="session")
def seller_url(ats_config, active_project):
    """Base app URL for the active environment. In project mode this is the
    project's URL; otherwise the Agrim seller-app URL (backward compatible)."""
    if active_project:
        return project_store.resolve_base_url(active_project, os.environ.get("ATS_ENV"))
    return _get_platform_url(ats_config, "seller")


@pytest.fixture(scope="session")
def admin_url(ats_config, active_project):
    """Admin panel URL. Projects MAY define a separate admin app (project.json
    "admin": {url, storage_state, credentials}) — honor it, or admin tests run
    against the seller origin and die on its login page (run-4 BENCH-009: the
    agent fought this for 188 tool calls). Generic projects without an admin
    block keep the base-URL fallback; legacy mode uses the platform admin URL."""
    if active_project:
        admin_app = (active_project.get("admin") or {})
        if admin_app.get("url"):
            return admin_app["url"]
        return project_store.resolve_base_url(active_project, os.environ.get("ATS_ENV"))
    return _get_platform_url(ats_config, "admin")


@pytest.fixture(scope="session")
def base_url(seller_url):
    """Base app URL — alias of seller_url (project-aware)."""
    return seller_url


@pytest.fixture(autouse=True)
def _project_auth(page, active_project, seller_url):
    """In project mode, apply the project's captured login (storage_state) to the
    page so synthesized/explored tests run authenticated. No-op in legacy mode
    or when the project has no captured auth."""
    if active_project:
        state = _project_storage_state(active_project)
        if state.get("cookies") or state.get("origins"):
            try:
                _apply_login_state(page, state, seller_url)
            except Exception as e:
                print(f"[PROJECT AUTH] failed: {e}", flush=True)
    yield


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
def admin_page(browser, browser_context_args, admin_login_state, admin_url, request, results_dir, ats_config, capture_network):
    """Per-test page logged into admin panel. Own browser context so it can
    coexist with seller_page in the same test."""
    has_cookies = len(admin_login_state.get("cookies", [])) > 0
    print(f"[FIXTURE] admin_page: login_state_valid={has_cookies}, admin_url={admin_url}", flush=True)
    context = browser.new_context(**browser_context_args)
    # Feed admin-context API calls into the same per-test network recorder (best-effort).
    try:
        if capture_network is not None:
            capture_network.attach(context)
    except Exception:
        pass
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)
    # Trace this context too — admin tests are the most failure-prone, so a
    # full DOM/network trace is especially valuable here.
    mode = _trace_mode(ats_config)
    tc_id = _tc_id_with_variant(request.node)
    admin_trace_on = _start_trace(context, f"{tc_id} [admin]", mode)
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
    # Finalize the admin trace BEFORE closing the context (stop requires a live context).
    if admin_trace_on:
        rep = getattr(request.node, "rep_call", None)
        save = _should_save_trace(mode, bool(rep and rep.failed))
        _finalize_trace(context, save, os.path.join(results_dir, "traces", f"{tc_id}.zip"))
    page.close()
    context.close()


@pytest.fixture(autouse=True)
def set_page_timeouts(page):
    """Set generous but finite timeouts on every page to prevent infinite hangs."""
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)
    yield page


@pytest.fixture(autouse=True)
def manage_trace(page, request, results_dir, ats_config):
    """Record a Playwright trace for the default browser context (seller / generic
    tests), saved to results/<timestamp>/traces/<tc_id>.zip per the configured
    mode. Admin-panel tests record their own trace inside admin_page, so here we
    only persist the default-context trace when that page was actually used —
    avoiding a noise trace of a blank, unused default tab on pure-admin tests."""
    mode = _trace_mode(ats_config)
    tc_id = _tc_id_with_variant(request.node)
    started = _start_trace(page.context, tc_id, mode)

    yield

    if not started:
        return
    rep = getattr(request.node, "rep_call", None)
    failed = bool(rep and rep.failed)
    save = _should_save_trace(mode, failed)

    name = tc_id
    if save and getattr(request.node, "_admin_page", None) is not None:
        # The admin context's trace (saved by admin_page) is the meaningful one.
        # Keep this default-context trace only if the seller/default page was
        # actually navigated; otherwise it's a blank unused tab → discard.
        try:
            blank = page.url in ("about:blank", "", "chrome://newtab/")
        except Exception:
            blank = True
        if blank:
            save = False
        else:
            name = f"{tc_id}_seller"
    _finalize_trace(page.context, save, os.path.join(results_dir, "traces", f"{name}.zip"))


# ── Network capture: store the API calls (method/url/payload/response/timing) each test made,
# so run History can show the step-wise log WITH the request/response behind each step. Mirrors
# the trace fixture's shape; fully fail-safe (a capture problem must never fail/slow a test) and
# config-gated (config.json "network" or ATS_NETWORK). One JSON per test: network/<tc_id>.json.
class _NetworkRecorder:
    def __init__(self, cfg):
        self.calls = []
        self.api_only = bool(cfg.get("api_only", True))
        self.max_body = int(cfg.get("max_body_kb", 20)) * 1024
        self._start = {}   # request -> started epoch ms

    def _is_api(self, req):
        try:
            if req.resource_type in ("xhr", "fetch"):
                return True
            u = (req.url or "").lower()
            return ("/api/" in u) or ("/graphql" in u) or u.endswith(".json")
        except Exception:
            return False

    def attach(self, context):
        context.on("request", self._on_request)
        context.on("requestfailed", self._on_failed)
        context.on("response", self._on_response)

    def _cap(self, s):
        if s is None:
            return None
        return s if len(s) <= self.max_body else s[:self.max_body] + "...(truncated)"

    def _post(self, req):
        try:
            return self._cap(req.post_data)
        except Exception:
            return None

    def _on_request(self, req):
        try:
            if not self.api_only or self._is_api(req):
                self._start[req] = int(time.time() * 1000)
        except Exception:
            pass

    def _on_failed(self, req):
        st = self._start.pop(req, None)
        if st is None:
            return
        try:
            self.calls.append({"method": req.method, "url": req.url, "resource_type": req.resource_type,
                               "status": None, "ok": False, "failure": str(getattr(req, "failure", ""))[:200],
                               "request_body": self._post(req), "response_body": None,
                               "started_ms": st, "duration_ms": int(time.time() * 1000) - st})
        except Exception:
            pass

    def _on_response(self, resp):
        try:
            req = resp.request
        except Exception:
            return
        st = self._start.pop(req, None)
        if st is None:
            return
        body = None
        try:
            ctype = (resp.headers.get("content-type") or "").lower()
            if any(k in ctype for k in ("json", "text", "xml", "javascript")) or self._is_api(req):
                raw = resp.body()
                if raw:
                    body = self._cap(raw.decode("utf-8", "replace"))
        except Exception:
            body = None
        try:
            self.calls.append({"method": req.method, "url": req.url, "resource_type": req.resource_type,
                               "status": resp.status, "ok": resp.ok, "request_body": self._post(req),
                               "response_body": body, "started_ms": st,
                               "duration_ms": int(time.time() * 1000) - st})
        except Exception:
            pass

    def write(self, path):
        if not self.calls:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.calls.sort(key=lambda c: c.get("started_ms") or 0)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"calls": self.calls}, f, indent=2, default=str)
        except Exception:
            pass


def _network_enabled(ats_config):
    on = (os.environ.get("ATS_NETWORK") or "").lower()
    if on == "on":
        return True
    if on == "off":
        return False
    return bool(((ats_config or {}).get("network", {}) or {}).get("capture", True))


def _start_network(context, ats_config):
    """Attach a recorder to a context (returns it, or None if disabled / on error)."""
    if not _network_enabled(ats_config):
        return None
    try:
        rec = _NetworkRecorder(((ats_config or {}).get("network", {}) or {}))
        rec.attach(context)
        return rec
    except Exception:
        return None


@pytest.fixture(autouse=True)
def capture_network(page, request, results_dir, ats_config):
    """Record the API/XHR calls (request payload + response body + timing) of the default
    browser context to results/<ts>/network/<tc_id>.json. admin_page attaches its own context
    to the SAME recorder (it requests this fixture), so one file covers the whole test."""
    rec = _start_network(page.context, ats_config)
    yield rec
    try:
        if rec is not None:
            tc_id = _tc_id_with_variant(request.node)
            rec.write(os.path.join(results_dir, "network", f"{tc_id}.json"))
    except Exception:
        pass


@pytest.fixture
def healer(request, results_dir):
    """Per-test heal recorder. Writes results/<ts>/heals/<tc_id>.json only if a
    locator actually drifted — so the run report can show "N locators
    auto-healed" and (later) offer to commit the repaired locator."""
    tc_id = _tc_id_with_variant(request.node)
    h = Healer(tc_id)
    yield h
    try:
        if h.events:
            h.write(os.path.join(results_dir, "heals", f"{tc_id}.json"))
    except Exception:
        pass


@pytest.fixture
def heal(healer):
    """Self-healing locator factory. Use it for the parts of a flow most prone
    to UI drift (buttons, CTAs, dynamic rows) instead of a raw page.locator():

        heal(seller_page, 'page.get_by_role("button", name="Save")').resolve().click()

    The returned object resolves through primary -> derived fallbacks ->
    fingerprint scan, recording any heal. Pass explicit `fallbacks=[...]` /
    `fingerprint={...}` (captured at record/synthesis time) for best accuracy."""
    def _heal(page, primary, **kwargs):
        kwargs.setdefault("healer", healer)
        return _smart_locator(page, primary, **kwargs)
    return _heal


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

        # Save full visible page text so non-vision LLMs (e.g. MiMo) can read what was on screen
        try:
            page_text = active_page.inner_text("body") or ""
            if page_text.strip():
                with open(os.path.join(screenshot_dir, f"{tc_name}_FAILED_TEXT.txt"), "w", encoding="utf-8") as _ft:
                    _ft.write(page_text[:12000])
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
    match = re.search(r'(TC_[A-Z]+(?:_[A-Z]+|_\d+)+)', tc_name)
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
    match = re.search(r'(TC_[A-Z]+(?:_[A-Z]+|_\d+)+)', tc_name)
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
    if _is_variants(tc_raw) and len(tc_raw) > 1 and "tc_data" in metafunc.fixturenames:
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
    match = re.search(r'(TC_[A-Z]+(?:_[A-Z]+|_\d+)+)', tc_name)
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
