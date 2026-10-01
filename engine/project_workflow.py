"""Project-bound multi-app recording export and replay validation.

This is an opt-in authoring API, not a claim of independent verification.
"""
import json
import re
from pathlib import Path

import project_store
from multi_app import MultiAppReplay, Workflow


def validate_project_workflow(project, workflow):
    workflow = Workflow.model_validate(workflow.model_dump() if isinstance(workflow, Workflow) else workflow)
    if not project or not project.get("id"):
        raise ValueError("Multi-app replay requires an explicit project")
    apps = project_store.project_apps(project)
    if not apps or any(not app.get("id") for app in apps):
        raise ValueError("Save project settings to assign stable app IDs first")
    if len({app["id"] for app in apps}) != len(apps):
        raise ValueError("Project contains duplicate app IDs")
    for app in apps:
        actors = app.get("actors")
        if not isinstance(actors, list) or not actors or any(
            not isinstance(actor, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", actor) for actor in actors
        ) or len(actors) != len(set(actors)):
            raise ValueError("Project has invalid actor registrations")
    for binding in workflow.bindings:
        app = next((app for app in apps if app["id"] == binding.app), None)
        if app is None or binding.actor not in app.get("actors", []):
            raise ValueError("Workflow references an unregistered app/actor")
        # Pin the complete base URL, not just the origin; an edit can otherwise
        # silently move a recording into a different tenant on the same host.
        if binding.url != app["url"]:
            raise ValueError("Recorded app URL differs from project settings; review and re-record")
    return workflow


class ProjectReplay(MultiAppReplay):
    def __init__(self, browser, project):
        super().__init__(browser)
        self.project = project

    def context_options(self, binding):
        app = next(a for a in project_store.project_apps(self.project) if a['id'] == binding.app)
        states = app.get('auth_states') or {}
        path = states.get(binding.actor)
        if not path:
            if app.get('requires_auth'):
                raise ValueError('Registered actor has no configured authentication state')
            return {}
        path = Path(path)
        if not path.is_absolute() or not path.is_file():
            raise ValueError('Registered actor authentication state is unavailable')
        registered = [Path(p).resolve() for a in project_store.project_apps(self.project)
                      for p in (a.get('auth_states') or {}).values()]
        if registered.count(path.resolve()) != 1:
            raise ValueError('Authentication state cannot be shared between app/actor bindings')
        # The state is loaded locally by Playwright, never included in a tool result,
        # recorded workflow or provider prompt. It remains scoped to this context.
        return {'storage_state': str(path)}

    def run(self, workflow):
        return super().run(validate_project_workflow(self.project, workflow))


def export_project_workflow(ats_root, project_id, flow_id, tc_id, description, workflow):
    """Create a NEW flow using the existing suite/metadata discovery convention.

    Existing flows/tests are never overwritten. Export is not verification.
    Values in a supplied workflow are saved verbatim; callers must not embed
    secrets. Auth-state imports and credential placeholders are not supported.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", project_id):
        raise ValueError("Invalid project ID")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", flow_id):
        raise ValueError("Invalid flow ID")
    if not re.fullmatch(r"TC-[A-Z]+(?:-[A-Z]+|-\d+)+", tc_id):
        raise ValueError("Invalid test case ID")
    root = Path(ats_root).resolve()
    project_path = Path(project_store.project_dir(str(root), project_id)).resolve()
    if not project_path.is_relative_to(root / "projects"):
        raise ValueError("Project path escapes project storage")
    project = project_store.get_project(str(root), project_id)
    if not project or project.get("id") != project_id:
        raise ValueError("Project identity does not match its storage directory")
    workflow = validate_project_workflow(project, workflow)
    assertions = [step for step in workflow.steps if step.op == "expect_text"]
    if not assertions:
        raise ValueError("A saved test requires at least one outcome assertion")
    suite = Path(project_store.tests_root(str(root), project_id)).resolve()
    if not suite.is_relative_to(project_path) or not suite.is_dir():
        raise ValueError("An explicit project-owned test suite is required")
    flows = (suite / "flows").resolve()
    if not flows.is_relative_to(suite):
        raise ValueError("Flow path escapes project suite")
    for metadata in flows.rglob("test_cases.json"):
        entries = json.loads(metadata.read_text(encoding="utf-8"))
        if any(entry.get("tc_id") == tc_id for entry in entries):
            raise ValueError("Test case ID already exists in this project")
    flow = flows / flow_id
    function = "test_" + tc_id.replace("-", "_")
    filename = function.lower() + ".py"
    code = f'''"""Project-bound multi-app replay. Exported, not independently verified."""
import json
from pathlib import Path


def {function}(multi_app_replay, active_project, checkpoints):
    if not active_project or active_project.get("id") != {project_id!r}:
        raise ValueError("This recording belongs to a different project")
    workflow = json.loads(Path(__file__).with_name("workflow.json").read_text(encoding="utf-8"))
    try:
        result = multi_app_replay.run(workflow)
    except Exception:
        checkpoints.mark_failed("Multi-app workflow", "Replay failed; see pytest failure")
        raise
    for step in result["completed"]:
        if step["op"] == "expect_text":
            checkpoints.mark_passed(f"Step {{step['step']}}: {{step['app']}} / {{step['actor']}} outcome")
'''
    compile(code, filename, "exec")
    metadata = [{"tc_id": tc_id, "module": flow_id, "description": description,
                 "preconditions": "Explicit isolated app/actor sessions; login steps required if applicable",
                 "expected_result": "All recorded cross-app outcome assertions pass",
                 "checkpoints": [f"{s.app} / {s.actor}: {s.test_id}" for s in assertions],
                 "workflow_format": "multi_app_v1"}]
    # Upgrade only the exact generated legacy shim, never user-customized code.
    project_store.ensure_tests_scaffold(str(root), project_id)
    flow.mkdir(parents=False, exist_ok=False)
    (flow / "__init__.py").write_text("", encoding="utf-8")
    (flow / filename).write_text(code, encoding="utf-8")
    (flow / "workflow.json").write_text(workflow.model_dump_json(indent=2), encoding="utf-8")
    (flow / "test_cases.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return {"status": "success", "verified": False, "flow_dir": str(flow), "test_file": str(flow / filename),
            "tc_id": tc_id, "message": "Exported only; independent replay is required before delivery"}
