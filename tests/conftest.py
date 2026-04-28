"""
Agrim ATS — Pytest Configuration (conftest.py)

Provides fixtures for:
- Loading app config from config.json
- Resolving the base URL from the active environment
- Setting up video recording directories
- Setting browser timeouts to prevent infinite hangs
- Capturing screenshots on test failure
"""

import pytest
import os
import json


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


# ── Fixtures ──
@pytest.fixture(scope="session")
def ats_config():
    """Full ATS configuration dictionary."""
    return _load_config()


@pytest.fixture(scope="session")
def base_url(ats_config):
    """Base URL for the active environment (dev/staging)."""
    env_name = os.environ.get("ATS_ENV", "dev")
    env_config = ats_config.get("environments", {}).get(env_name, {})
    url = env_config.get("base_url", "https://supplier-dev.agrim.app/")
    return url if url.endswith("/") else url + "/"


@pytest.fixture(scope="session")
def results_dir():
    """Directory where this run's artifacts are stored."""
    d = os.environ.get("ATS_RESULTS_DIR", os.path.join(os.path.dirname(__file__), "results", "manual"))
    os.makedirs(d, exist_ok=True)
    return d


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, results_dir):
    """Extend playwright browser context with video recording and zoom-scaled viewport."""
    video_dir = os.path.join(results_dir, "videos")
    os.makedirs(video_dir, exist_ok=True)

    # Zoom: scale viewport so more content fits at lower zoom levels.
    # e.g. 75% zoom → viewport = 1280/0.75 × 720/0.75 = 1707×960
    #      60% zoom → viewport = 1280/0.60 × 720/0.60 = 2133×1200
    base_w, base_h = 1280, 720
    zoom_str = os.environ.get("ATS_ZOOM", "")
    if zoom_str:
        try:
            zoom_pct = int(zoom_str)
            if 1 <= zoom_pct <= 500 and zoom_pct != 100:
                factor = zoom_pct / 100.0
                vp_w = round(base_w / factor)
                vp_h = round(base_h / factor)
            else:
                vp_w, vp_h = base_w, base_h
        except ValueError:
            vp_w, vp_h = base_w, base_h
    else:
        vp_w, vp_h = base_w, base_h

    return {
        **browser_context_args,
        "viewport": {"width": vp_w, "height": vp_h},
        "record_video_dir": video_dir,
        "record_video_size": {"width": base_w, "height": base_h},
    }


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Set browser launch args."""
    return {
        **browser_type_launch_args,
        "args": ["--disable-gpu", "--no-sandbox"],
    }


@pytest.fixture(autouse=True)
def set_page_timeouts(page):
    """Set generous but finite timeouts on every page to prevent infinite hangs."""
    page.set_default_timeout(30000)           # 30s for actions (click, fill, etc.)
    page.set_default_navigation_timeout(30000) # 30s for goto/navigation
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
    """After each test, capture a screenshot if the test failed."""
    yield
    rep = getattr(request.node, "rep_call", None)
    if rep and rep.failed:
        screenshot_dir = os.path.join(results_dir, "screenshots")
        os.makedirs(screenshot_dir, exist_ok=True)
        tc_name = request.node.name.split("[")[0]
        try:
            page.screenshot(path=os.path.join(screenshot_dir, f"{tc_name}_FAILED.png"))
        except Exception:
            pass  # Page may already be closed

# ── Test Data ──
@pytest.fixture(scope="function")
def tc_data(request):
    """Load test data for the current TC from the flow's test_data.json."""
    tc_name = request.node.name.split("[")[0]
    # Extract TC ID (e.g. test_TC_CATALOG_001_... -> TC-CATALOG-001)
    tc_id = None
    import re
    match = re.search(r'test_(TC_\w+?)_', tc_name)
    if match:
        tc_id = match.group(1).replace("_", "-")
    
    if not tc_id:
        return {}

    test_file_path = request.module.__file__
    flow_dir = os.path.dirname(test_file_path)
    data_file = os.path.join(flow_dir, "test_data.json")

    if os.path.exists(data_file):
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get(tc_id, {})
        except Exception:
            return {}
    return {}
