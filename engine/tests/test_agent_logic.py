"""
Unit tests for the conversational agent's engine logic (engine/agent_chat.py).

The agent's autonomy rests on a handful of pure / file-backed helpers: the
record-time locator-uniqueness gate, the assertion lint, the pytest-verdict
parser, the history trimmer, the suite delete, attachment handling, and the
scoped-folder containment check. None of these need a browser or an LLM, so we
test them directly and fast. This is the regression net that lets us change the
agent with confidence — the half of "production grade" the live verify loop and
the eval harness (agent_eval.py) cannot give us offline.
"""

import os
import json

import pytest

import agent_chat as ac


# ──────────────────────────────────────────────────────────────────────────────
# _is_junk_name — what the scoped-folder browser hides (mirrored in main.js)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,junk", [
    ("", True),
    (".git", True),
    (".DS_Store", True),
    (".env", True),
    ("node_modules", True),
    ("venv", True),
    ("__pycache__", True),
    ("src", False),
    ("test_data.json", False),
    ("README.md", False),
])
def test_is_junk_name(name, junk):
    assert ac._is_junk_name(name) is junk


# ──────────────────────────────────────────────────────────────────────────────
# _scoped_path — path containment (a traversal here would let the agent read
# outside the project's context folder)
# ──────────────────────────────────────────────────────────────────────────────

def test_scoped_path_stays_inside(tmp_path):
    base = str(tmp_path)
    got = ac._scoped_path(base, "sub/file.txt")
    assert got == os.path.normpath(os.path.join(base, "sub", "file.txt"))


def test_scoped_path_empty_rel_returns_base(tmp_path):
    base = str(tmp_path)
    assert ac._scoped_path(base, "") == os.path.normpath(base)


def test_scoped_path_traversal_that_resolves_inside_is_allowed(tmp_path):
    base = str(tmp_path)
    # sub/../file.txt collapses back inside base
    assert ac._scoped_path(base, os.path.join("sub", "..", "file.txt")) == \
        os.path.normpath(os.path.join(base, "file.txt"))


@pytest.mark.parametrize("rel", [
    os.path.join("..", "evil.txt"),
    os.path.join("..", "..", "Windows", "system32"),
    os.path.join("sub", "..", "..", "escape"),
])
def test_scoped_path_escape_returns_none(tmp_path, rel):
    assert ac._scoped_path(str(tmp_path), rel) is None


def test_scoped_path_empty_base_returns_none():
    assert ac._scoped_path("", "anything") is None


# ──────────────────────────────────────────────────────────────────────────────
# _search_context — filename + content grep that prunes junk dirs
# ──────────────────────────────────────────────────────────────────────────────

def _write(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def test_search_context_finds_content_and_skips_junk(tmp_path):
    base = str(tmp_path)
    _write(os.path.join(base, "readme.md"), "intro\nthe needle is here\nmore")
    _write(os.path.join(base, "sub", "deep.md"), "needle deep")
    _write(os.path.join(base, "notes.txt"), "nothing relevant")
    _write(os.path.join(base, ".git", "config"), "needle")          # junk dir
    _write(os.path.join(base, "node_modules", "x.js"), "needle")    # junk dir

    res = ac._search_context(base, "needle", content=True)
    files = {h["file"] for h in res["content_matches"]}
    assert "readme.md" in files
    assert any(f.endswith("deep.md") for f in files)
    # Junk directories must never be searched.
    assert not any(".git" in f or "node_modules" in f for f in files)


def test_search_context_name_match(tmp_path):
    base = str(tmp_path)
    _write(os.path.join(base, "readme.md"), "x")
    res = ac._search_context(base, "readme", content=False)
    assert "readme.md" in res["name_matches"]
    assert res["content_matches"] == []


# ──────────────────────────────────────────────────────────────────────────────
# extract_file_text / _read_text_file — attachment + context reads
# ──────────────────────────────────────────────────────────────────────────────

def test_extract_text_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello world", encoding="utf-8")
    text, truncated = ac.extract_file_text(str(p))
    assert text == "hello world"
    assert truncated is False


def test_extract_unsupported_returns_none(tmp_path):
    p = tmp_path / "pic.png"
    p.write_bytes(b"\x89PNG\r\n")
    text, truncated = ac.extract_file_text(str(p))
    assert text is None


def test_read_text_file_truncation(tmp_path):
    p = tmp_path / "big.txt"
    p.write_text("hello", encoding="utf-8")
    text, truncated = ac._read_text_file(str(p), limit=3)
    assert text == "hel"
    assert truncated is True


# ──────────────────────────────────────────────────────────────────────────────
# _attachment_paths — normalize the IPC attachments field
# ──────────────────────────────────────────────────────────────────────────────

def test_attachment_paths_variants():
    assert ac._attachment_paths(None) == []
    assert ac._attachment_paths([None, "", {}]) == []
    assert ac._attachment_paths([os.path.join("a", "b.txt")]) == \
        [(os.path.join("a", "b.txt"), "b.txt")]
    assert ac._attachment_paths([{"path": "/a/c.png", "name": "pic"}]) == \
        [("/a/c.png", "pic")]
    # path without an explicit display name falls back to basename
    assert ac._attachment_paths([{"path": "/a/c.png"}]) == [("/a/c.png", "c.png")]


# ──────────────────────────────────────────────────────────────────────────────
# _build_user_prompt — plain str when text-only, list w/ BinaryContent for images
# ──────────────────────────────────────────────────────────────────────────────

def test_build_prompt_no_attachments_is_passthrough():
    assert ac._build_user_prompt("hello", None) == "hello"
    assert ac._build_user_prompt("hi", []) == "hi"


def test_build_prompt_inlines_document_text(tmp_path):
    doc = tmp_path / "spec.md"
    doc.write_text("ACCEPTANCE: the page shows Orders", encoding="utf-8")
    out = ac._build_user_prompt("test this", [str(doc)])
    assert isinstance(out, str)                       # text-only -> plain string
    assert "ACCEPTANCE: the page shows Orders" in out
    assert "[doc: spec.md]" in out


def test_build_prompt_image_becomes_binary_part(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    # vision=True required — without it images are intentionally skipped for text-only models
    out = ac._build_user_prompt("look", [str(img)], vision=True)
    assert isinstance(out, list) and len(out) >= 2    # [body, BinaryContent]
    assert isinstance(out[0], str) and "[image: shot.png]" in out[0]
    assert isinstance(out[1], ac.BinaryContent)


def test_build_prompt_image_skipped_when_no_vision(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    out = ac._build_user_prompt("look", [str(img)])   # vision=False default
    assert isinstance(out, str)
    assert "not sent" in out


def test_build_prompt_missing_file_noted_text_only(tmp_path):
    out = ac._build_user_prompt("go", [str(tmp_path / "ghost.txt")])
    assert isinstance(out, str)
    assert "[not found: ghost.txt]" in out


# ──────────────────────────────────────────────────────────────────────────────
# _is_closed_error — the browser-recovery trigger
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg,closed", [
    ("Target page, context or browser has been closed", True),
    ("Browser has been disconnected", True),
    ("Page crashed", True),
    ("Timeout 30000ms exceeded waiting for selector", False),
    ("strict mode violation: locator resolved to 3 elements", False),
])
def test_is_closed_error(msg, closed):
    assert ac._is_closed_error(Exception(msg)) is closed


# ──────────────────────────────────────────────────────────────────────────────
# _pytest_summary — what run_test_case shows the user / agent as the verdict
# ──────────────────────────────────────────────────────────────────────────────

def test_pytest_summary_passed_banner():
    assert ac._pytest_summary("======= 1 passed in 0.50s =======") == "1 passed in 0.50s"


def test_pytest_summary_failed_banner():
    out = ac._pytest_summary("==== 1 failed, 2 passed in 3.1s ====")
    assert out == "1 failed, 2 passed in 3.1s"


def test_pytest_summary_falls_back_to_bare_line():
    assert ac._pytest_summary("some logs\n1 passed in 0.1s\n") == "1 passed in 0.1s"


def test_pytest_summary_returns_last_banner():
    text = "==== 1 failed in 1s ====\nrerun\n==== 1 passed in 1s ===="
    assert ac._pytest_summary(text) == "1 passed in 1s"


def test_pytest_summary_empty():
    assert ac._pytest_summary("nothing interesting here") == ""


# ──────────────────────────────────────────────────────────────────────────────
# _find_failure_artifacts — the FAILED screenshot fed to the vision loop
# ──────────────────────────────────────────────────────────────────────────────

def test_find_failure_artifacts(tmp_path):
    sdir = tmp_path / "screenshots"
    sdir.mkdir()
    (sdir / "test_x_FAILED.png").write_bytes(b"png")
    (sdir / "test_x_ERRORS.txt").write_text("boom: element not found", encoding="utf-8")
    shot, err, page_text = ac._find_failure_artifacts(str(tmp_path))
    assert shot.endswith("_FAILED.png")
    assert "boom" in err
    assert page_text is None   # no _FAILED_TEXT.txt written


def test_find_failure_artifacts_with_page_text(tmp_path):
    sdir = tmp_path / "screenshots"
    sdir.mkdir()
    (sdir / "tc_FAILED.png").write_bytes(b"png")
    (sdir / "tc_FAILED_TEXT.txt").write_text("page body here", encoding="utf-8")
    shot, err, page_text = ac._find_failure_artifacts(str(tmp_path))
    assert shot.endswith("_FAILED.png")
    assert err is None
    assert page_text == "page body here"


def test_find_failure_artifacts_empty(tmp_path):
    assert ac._find_failure_artifacts(str(tmp_path)) == (None, None, None)
    assert ac._find_failure_artifacts(str(tmp_path / "nope")) == (None, None, None)


# ──────────────────────────────────────────────────────────────────────────────
# _lint_recording — assertion quality gate (no checkpoints / dynamic values)
# ──────────────────────────────────────────────────────────────────────────────

def test_lint_flags_missing_checkpoints():
    warns = ac._lint_recording([{"id": 1}], [])
    assert any("No verification checkpoints" in w for w in warns)


def test_lint_clean_when_stable_assertion():
    warns = ac._lint_recording([{"id": 1}], [{"value": "Orders", "description": "x"}])
    assert warns == []


@pytest.mark.parametrize("value", [
    "PO #1234567",          # long number
    "Created 2026-06-02",   # date
    "placed at 14:35",      # time
    "id ab12cd34-1234",     # uuid-ish
])
def test_lint_flags_dynamic_values(value):
    warns = ac._lint_recording([{"id": 1}], [{"value": value, "description": "x"}])
    assert any("DYNAMIC" in w for w in warns)


# ──────────────────────────────────────────────────────────────────────────────
# _delete_test_case — suite CRUD (mirrors main.js delete-test); must remove the
# entry from test_cases.json, test_data.json AND the test_<flow>.py function only
# ──────────────────────────────────────────────────────────────────────────────

def _make_flow(root, flow_id="demo"):
    fdir = os.path.join(root, "tests", "flows", flow_id)
    os.makedirs(fdir, exist_ok=True)
    with open(os.path.join(fdir, "test_cases.json"), "w", encoding="utf-8") as f:
        json.dump([{"tc_id": "TC-DEMO-001", "description": "one"},
                   {"tc_id": "TC-DEMO-002", "description": "two"}], f)
    with open(os.path.join(fdir, "test_data.json"), "w", encoding="utf-8") as f:
        json.dump({"TC-DEMO-001": {"k": 1}, "TC-DEMO-002": {"k": 2}}, f)
    with open(os.path.join(fdir, f"test_{flow_id}.py"), "w", encoding="utf-8") as f:
        f.write(
            'import pytest\n\n\n'
            '@pytest.mark.tc("TC-DEMO-001")\n'
            'def test_TC_DEMO_001(page):\n    page.goto("https://x/")\n\n\n'
            '@pytest.mark.tc("TC-DEMO-002")\n'
            'def test_TC_DEMO_002(page):\n    page.goto("https://y/")\n'
        )
    return fdir


def test_delete_test_case_removes_everywhere(tmp_path):
    root = str(tmp_path)
    fdir = _make_flow(root)
    res = ac._delete_test_case(root, "demo", "TC-DEMO-001")
    assert res["ok"] is True
    assert set(res["removed"]) >= {"test_cases.json", "test_data.json", "test_demo.py"}

    cases = json.load(open(os.path.join(fdir, "test_cases.json"), encoding="utf-8"))
    assert [c["tc_id"] for c in cases] == ["TC-DEMO-002"]

    data = json.load(open(os.path.join(fdir, "test_data.json"), encoding="utf-8"))
    assert "TC-DEMO-001" not in data and "TC-DEMO-002" in data

    code = open(os.path.join(fdir, "test_demo.py"), encoding="utf-8").read()
    assert "test_TC_DEMO_001" not in code      # deleted function gone
    assert "test_TC_DEMO_002" in code          # sibling untouched


def test_delete_test_case_missing(tmp_path):
    root = str(tmp_path)
    _make_flow(root)
    res = ac._delete_test_case(root, "demo", "TC-DEMO-999")
    assert res["ok"] is False


def test_delete_test_case_unknown_flow(tmp_path):
    res = ac._delete_test_case(str(tmp_path), "ghost", "TC-X-1")
    assert res["ok"] is False


# ──────────────────────────────────────────────────────────────────────────────
# _trim_dangling_tool_calls — keeps history valid after a tool-budget cutoff
# ──────────────────────────────────────────────────────────────────────────────

def _msgs():
    from pydantic_ai.messages import (
        ModelResponse, ModelRequest, TextPart, ToolCallPart, ToolReturnPart)
    return ModelResponse, ModelRequest, TextPart, ToolCallPart, ToolReturnPart


def test_trim_drops_trailing_unreturned_tool_call():
    ModelResponse, ModelRequest, TextPart, ToolCallPart, ToolReturnPart = _msgs()
    msgs = [
        ModelResponse(parts=[TextPart(content="ok")]),
        ModelResponse(parts=[ToolCallPart(tool_name="click", args={}, tool_call_id="c1")]),
    ]
    trimmed = ac._trim_dangling_tool_calls(msgs)
    assert len(trimmed) == 1                       # the dangling call response is dropped


def test_trim_keeps_returned_tool_call():
    ModelResponse, ModelRequest, TextPart, ToolCallPart, ToolReturnPart = _msgs()
    msgs = [
        ModelResponse(parts=[ToolCallPart(tool_name="click", args={}, tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="click", content={"ok": True}, tool_call_id="c1")]),
        ModelResponse(parts=[TextPart(content="done")]),
    ]
    trimmed = ac._trim_dangling_tool_calls(msgs)
    assert len(trimmed) == 3                        # nothing dangling -> unchanged


def test_trim_only_drops_the_unreturned_tail():
    ModelResponse, ModelRequest, TextPart, ToolCallPart, ToolReturnPart = _msgs()
    msgs = [
        ModelResponse(parts=[ToolCallPart(tool_name="click", args={}, tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="click", content={"ok": True}, tool_call_id="c1")]),
        ModelResponse(parts=[ToolCallPart(tool_name="fill", args={}, tool_call_id="c2")]),
    ]
    trimmed = ac._trim_dangling_tool_calls(msgs)
    assert len(trimmed) == 2                        # c2 (unreturned) tail dropped, c1 kept


# ──────────────────────────────────────────────────────────────────────────────
# BrowserSession.record logic — the one-shot-accuracy core (no real browser)
# ──────────────────────────────────────────────────────────────────────────────

class _Loc:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n


class _FakePage:
    """Just enough of a Playwright page for to_locator(...).count()."""
    def __init__(self, counts):
        self._counts = counts

    def locator(self, sel):
        return _Loc(self._counts.get(("css", sel), 0))

    def get_by_test_id(self, value):
        return _Loc(self._counts.get(("test_id", value), 0))


def _session_with(counts):
    sess = ac.BrowserSession()
    sess.page = _FakePage(counts)
    return sess


def test_record_strategy_keeps_unique_primary():
    sess = _session_with({("css", "button.add"): 1})
    el = {"primary": {"by": "css", "value": "button.add"}, "test_id": "add-btn", "name": "Add"}
    strat, _loc, warn, nth = sess._record_strategy(el)
    assert strat == {"by": "css", "value": "button.add"}
    assert warn is None and nth is None


def test_record_strategy_upgrades_ambiguous_primary_to_unique_testid():
    sess = _session_with({("css", "button.add"): 3, ("test_id", "add-btn"): 1})
    el = {"primary": {"by": "css", "value": "button.add"}, "test_id": "add-btn", "name": "Add"}
    strat, _loc, warn, nth = sess._record_strategy(el)
    assert strat == {"by": "test_id", "value": "add-btn"}   # upgraded for a cold-run-stable hit
    assert warn is None and nth is None


def test_record_strategy_warns_when_ambiguous_and_no_testid():
    sess = _session_with({("css", "button.add"): 4})
    el = {"primary": {"by": "css", "value": "button.add"}, "name": "Add"}
    strat, _loc, warn, nth = sess._record_strategy(el)
    assert strat == {"by": "css", "value": "button.add"}
    assert warn and "4 elements" in warn and nth is None


def test_record_strategy_warns_when_testid_also_ambiguous():
    sess = _session_with({("css", "button.add"): 3, ("test_id", "add-btn"): 2})
    el = {"primary": {"by": "css", "value": "button.add"}, "test_id": "add-btn", "name": "Add"}
    strat, _loc, warn, nth = sess._record_strategy(el)
    assert strat == {"by": "css", "value": "button.add"}
    assert warn is not None and nth is None


def test_record_navigate_dedupes_consecutive():
    sess = ac.BrowserSession()
    sess._record_navigate("https://a/")
    sess._record_navigate("https://a/")     # duplicate consecutive -> ignored
    assert len(sess.steps) == 1
    sess._record_navigate("https://b/")
    assert len(sess.steps) == 2


def test_add_checkpoint_attaches_to_last_step():
    sess = ac.BrowserSession()
    sess.steps = [{"id": 7, "targetDescription": "Click Orders"}]
    sess.add_checkpoint("Orders shown", "url_contains", "/orders")
    cp = sess.assertions[-1]
    assert cp["afterStep"] == 7
    assert cp["type"] == "url_contains" and cp["value"] == "/orders"


def test_create_test_guards_empty_flow(tmp_path):
    sess = ac.BrowserSession()
    sess.steps = [{"id": 1, "targetDescription": "Navigate"}]   # <=1 step => nothing real recorded
    res = sess.create_test(str(tmp_path), {"name": "App"}, "TC-X-1", "demo", "desc")
    assert res["status"] == "error"


# -- create_test collect-check: uncollectable generated code caught offline ----

def _mk_flow(tmp_path, code):
    flow = tmp_path / "tests" / "flows" / "demo"
    flow.mkdir(parents=True)
    (flow / "__init__.py").write_text("")
    (flow / "test_demo.py").write_text(code, encoding="utf-8")
    return str(tmp_path)


def test_collect_check_clean_file_passes(tmp_path):
    root = _mk_flow(tmp_path, "def test_TC_DEMO_001():\n    pass\n")
    err = ac.BrowserSession._collect_check(root, os.path.join(root, "tests"),
                                           "demo", "TC-DEMO-001")
    assert err == ""


def test_collect_check_syntax_error_reported(tmp_path):
    # the bench smoke's attempt-1 failure class: a stray quote/newline in a
    # generated locator -> file does not even collect.
    root = _mk_flow(tmp_path, 'def test_TC_DEMO_001():\n    x = "broken\n')
    err = ac.BrowserSession._collect_check(root, os.path.join(root, "tests"),
                                           "demo", "TC-DEMO-001")
    assert err != "" and ("error" in err.lower() or "SyntaxError" in err)


def test_collect_check_no_match_reported(tmp_path):
    root = _mk_flow(tmp_path, "def test_TC_OTHER_001():\n    pass\n")
    err = ac.BrowserSession._collect_check(root, os.path.join(root, "tests"),
                                           "demo", "TC-DEMO-001")
    assert "NO test matched" in err
