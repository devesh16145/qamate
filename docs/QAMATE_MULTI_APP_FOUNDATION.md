# Multi-app replay foundation — 2026-09-21

Implemented an opt-in Python API in `engine/multi_app.py`, project-bound export in `engine/project_workflow.py`, and experimental grounded recording tools in `engine/multi_app_tools.py`. Project settings preserve stable app IDs and let users declare actor IDs. The tool integration is locally tested but **autonomous AI multi-app authoring reliability is not yet established**. The existing single-app Jev controller is unchanged. Agent-design guidance led to explicit, validated bindings and a separate recording surface before exposing app switching to a model.

## Contract

- `Workflow` validates JSON-compatible bindings and steps before opening browsers. Unknown fields, unknown actors, duplicate captures, forward capture references, and interaction before opening an app fail validation.
- Each `(app, actor)` owns a separate browser context and page, reused within one replay. Each new replay starts fresh. No implicit credentials, storage-state imports, or shared cookies/localStorage.
- Steps: open registered URL, click, fill, capture visible record ID, and exact text assertion. Test IDs are required; arbitrary Python is not accepted.
- A `record` field references a previously captured ID and scopes the target to its `data-record-id` row. Captures retain their source app/actor and are not returned in the normal completion report.
- Assertions use bounded Playwright web-first waits. Missing or incorrect propagated state raises, rather than returning a pass.
- HTTP(S) navigation must stay on the binding's exact scheme/host/port. Embedded URL credentials are rejected. SSO redirects, popups, iframe workflows, authentication setup, and alternate row-locator strategies are not implemented.
- Navigation checks are not an outbound-network sandbox: subresource/XHR requests remain allowed. Do not treat this runtime as isolation for hostile sites.

## Usage

```python
from multi_app import MultiAppReplay, Workflow

workflow = Workflow.model_validate({
    "bindings": [
        {"app": "store", "actor": "seller", "url": "https://store.example/"},
        {"app": "admin", "actor": "approver", "url": "https://admin.example/"},
    ],
    "steps": [
        {"app": "store", "actor": "seller", "op": "open"},
        {"app": "store", "actor": "seller", "op": "click", "test_id": "create"},
        {"app": "store", "actor": "seller", "op": "capture", "test_id": "record", "capture": "order"},
        {"app": "admin", "actor": "approver", "op": "open"},
        {"app": "admin", "actor": "approver", "op": "click", "test_id": "approve", "record": "order"},
        {"app": "store", "actor": "seller", "op": "expect_text", "test_id": "status", "record": "order", "value": "Approved"},
    ],
})
with MultiAppReplay(browser) as replay:
    result = replay.run(workflow)
```

Use a context manager so failed runs also close contexts. Exceptions mean failure; only completion of every step returns `status: passed`. This does not prove that the supplied assertions cover the business requirements. Filled values and captures are caller-owned sensitive data and must not be logged indiscriminately.

## Evidence and next gate

14 schema/unit tests and five local-browser tests cover two real localhost origins with a shared fixture backend: create a fresh record, capture its ID from UI, approve that exact ID in another app, then wait for delayed status propagation. Wrong-record and disabled-propagation controls must fail. Same-origin actors cannot see each other's cookies/localStorage; a new replay starts clean. Cross-origin navigation is blocked.

Full regression: 334 tests passed; both agent/evaluator selftests passed. The strengthened backend-generated-ID fixture was then rerun separately. Existing Pydantic AI `prepare_tools` deprecation warnings remain.

## Project settings and saved-test integration

Save Project Settings to assign IDs to legacy app entries. New projects get IDs immediately. IDs survive rename, reorder and URL edits; actor IDs are a nonempty comma-separated list (default `default`). These identify isolated browser sessions, not permissions or credentials. Saving settings does not migrate shared authentication into actors.

`validate_project_workflow` requires each binding's app ID, actor ID and full base URL to match the project. Reordering is safe; a changed URL, removed app or removed actor fails closed before browser actions. The complete base URL is pinned so a change of tenant path cannot silently retarget a saved recording.

To export a supplied typed recording into a NEW project flow:

```python
from project_workflow import export_project_workflow

# workflow uses the actual app IDs/actors/URLs saved in this project.
export = export_project_workflow(
    ats_root, project_id, "cross_app_approval", "TC-MULTI-001",
    "Approve the created record and observe its status", workflow,
)
assert export["verified"] is False  # Export is never a replay verdict.
```

This writes `workflow.json`, a pytest wrapper and `test_cases.json` in the existing project-owned suite layout. It refuses existing flows, duplicate TC IDs, unsafe paths and assertion-free tests. Filled values are stored verbatim: do not embed passwords or other secrets. The agent tool below exports only its own successfully recorded steps.

The generated wrapper uses the real shared `multi_app_replay` fixture and checkpoint/JUnit verdict path. Run it with `ATS_ROOT` and `ATS_PROJECT_ID` set, or through the existing project run workflow. It never loads the legacy shared auth capture. The generated project conftest shim now loads the shared module explicitly instead of potentially importing itself; only an exact old generated shim is upgraded during export, leaving customized files untouched.

Verification for this slice: 352 full-suite tests passed, followed by 12 focused tests after an additional malformed-actor guard; both selftests passed. Both inline renderer JSX blocks and the agent UI compile. The exported two-app test passed two independent pytest/JUnit checks using new record IDs, and failed when propagation was disabled. A test-only tripwire proved the shared-auth loader was not accessed. No Electron visual smoke was performed.

## Experimental live agent recording

Enable **Settings → AI / LLM → Enable experimental multi-app recording tools**, save and restart the agent. The corresponding opt-in flag is `agent_execution.multi_app_enabled`; it defaults off. Save Project Settings first so apps have stable IDs and declared actors.

Available tools:

- `multi_app_catalog`: credential-free app/actor discovery.
- `multi_app_observe(app, actor)`: open/reuse an isolated context, record the initial navigation, return fresh refs for unique visible `data-testid` targets. At most 100 candidate nodes are inspected; truncation is explicit.
- `multi_app_act(op, ref, value?, capture?)`: grounded click, fill, record-ID capture, or exact text outcome assertion. Fill uses the existing GUIDED/AUTO provenance gate. Capture the created ID before acting on a `data-record-id` row; the harness records correlation by capture name, never by freezing that run's ID.
- `multi_app_execute_goal(goal, actions)`: with hybrid enabled, route up to eight grounded click/fill candidates in one app/actor through the configured decision provider. Direct click/fill is then limited to explicit recovery or reported fallback; captures/assertions remain direct harness operations.
- `multi_app_create_test`: export the recorded workflow, explicitly unverified. Then use `run_test_case` for the existing two-run verification gate.

Once recording starts, the prepared tool surface excludes legacy browser/recording tools; browser-thread dispatch also rejects legacy BrowserSession operations. Ref identity is checked against the current DOM node, and refs are invalidated after actions or app switches. Password and OTP controls are rejected rather than saved as literal values. This is not general secret detection: callers must not supply secrets in ordinary text fields. A possibly partial click/fill failure taints the recording and prevents export; a new session is required. Recordings are session-local, with no durable resume/undo yet.

The typed recorder and replay share the same execution primitive. A local integration test now records the real two-app flow via observed refs, exports it, passes two independent pytest/JUnit runs with new IDs, then verifies failure when propagation is disabled. Stale/replaced nodes, cross-app refs, false assertions, password fields, and uncertain-mutation export have negative controls. Full regression: **358 passed**; both selftests and renderer compilation passed. Two focused tool tests passed again after the final dynamic guidance/error-reporting changes. No paid model or Electron visual smoke was performed.

**Update:** Jev-backed bounded goals are now implemented and have a retained local live smoke with two independent replays. See [multi-app Jev evidence](QAMATE_MULTI_APP_JEV.md), including two earlier handoffs and the remaining asynchronous no-progress handoff. This does not establish autonomous AI authoring accuracy, token savings or speed gains. Remaining: secure per-actor auth setup, richer locators than `data-testid`, durable recording recovery, per-app video/trace/network artifacts (legacy default-page artifacts are not evidence for these contexts), and actual AI-authored cross-app benchmarks. No real business-system writes were used.
