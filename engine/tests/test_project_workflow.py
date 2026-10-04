import json
from pathlib import Path

import pytest
import project_store as ps
from project_workflow import export_project_workflow, validate_project_workflow, ProjectReplay


@pytest.fixture
def project_flow(tmp_path):
    project = ps.create_project(str(tmp_path), "Fixture", "", apps=[
        {"id": "store", "url": "https://store.test/", "actors": ["seller"]}])
    return project, {"bindings": [{"app": "store", "actor": "seller", "url": "https://store.test/"}],
                     "steps": [{"app": "store", "actor": "seller", "op": "open"},
                               {"app": "store", "actor": "seller", "op": "expect_text", "test_id": "status", "value": "Ready"}]}


@pytest.mark.parametrize("fault", ["actor", "url", "app", "legacy", "none", "malformed_actors"])
def test_project_validation_precedes_browser_actions(project_flow, fault):
    project, flow = project_flow
    if fault in {"actor", "app"}: flow["bindings"][0][fault] = "unknown"; [s.update({fault: "unknown"}) for s in flow["steps"]]
    if fault == "url": flow["bindings"][0]["url"] = "https://store.test/other-tenant/"
    if fault == "legacy": project["apps"][0].pop("id")
    if fault == "none": project = None
    if fault == "malformed_actors": project["apps"][0]["actors"] = "seller"
    with pytest.raises(ValueError):
        ProjectReplay(None, project).run(flow)


def test_export_is_collectable_unverified_and_non_destructive(tmp_path, project_flow):
    project, flow = project_flow
    result = export_project_workflow(str(tmp_path), project["id"], "cross_app", "TC-MULTI-001", "Approve across apps", flow)
    assert result["status"] == "success" and result["verified"] is False
    code = Path(result["test_file"]).read_text(encoding="utf-8")
    compile(code, result["test_file"], "exec")
    folder = Path(result["flow_dir"])
    assert json.loads((folder / "workflow.json").read_text())["bindings"] == flow["bindings"]
    assert json.loads((folder / "test_cases.json").read_text())[0]["tc_id"] == "TC-MULTI-001"
    for flow_id, tc_id in [("cross_app", "TC-MULTI-002"), ("other", "TC-MULTI-001")]:
        with pytest.raises((ValueError, FileExistsError)):
            export_project_workflow(str(tmp_path), project["id"], flow_id, tc_id, "Changed", flow)
    assert Path(result["test_file"]).read_text(encoding="utf-8") == code


@pytest.mark.parametrize("flow_id,tc_id", [("../escape", "TC-MULTI-001"), ("ok", "TC-MULTI-001';bad")])
def test_export_rejects_path_and_code_injection(tmp_path, project_flow, flow_id, tc_id):
    project, flow = project_flow
    with pytest.raises(ValueError):
        export_project_workflow(str(tmp_path), project["id"], flow_id, tc_id, "description", flow)


def test_export_requires_assertion(tmp_path, project_flow):
    project, flow = project_flow
    flow["steps"].pop()
    with pytest.raises(ValueError, match="outcome assertion"):
        export_project_workflow(str(tmp_path), project["id"], "empty", "TC-MULTI-001", "Empty", flow)


def test_reorder_does_not_retarget_workflow(tmp_path, project_flow):
    project, flow = project_flow
    updated = ps.update_project(str(tmp_path), project["id"], {"apps": [
        {"id": "other", "url": "https://elsewhere.test/", "actors": ["seller"]}, *project["apps"]]})
    assert validate_project_workflow(updated, flow).bindings[0].app == "store"


def test_scaffold_upgrades_only_exact_generated_legacy_shim(tmp_path):
    root = str(tmp_path)
    suite = Path(ps.ensure_tests_scaffold(root, "fixture"))
    shim = suite / "conftest.py"
    shim.write_text(ps._LEGACY_CONFTEST_SHIM.format(ats_root=root), encoding="utf-8")
    ps.ensure_tests_scaffold(root, "fixture")
    assert "spec_from_file_location" in shim.read_text(encoding="utf-8")
    custom = "# User-owned custom fixture loader\n"
    shim.write_text(custom, encoding="utf-8")
    ps.ensure_tests_scaffold(root, "fixture")
    assert shim.read_text(encoding="utf-8") == custom


def test_project_shim_loads_shared_conftest_from_app_root(tmp_path):
    """Data root != code root (packaged macOS app): shims must find the shared
    conftest in the app's code, and older shims that pointed at ATS_ROOT are upgraded."""
    import project_store as ps
    data = tmp_path / "data"
    ps.ensure_tests_scaffold(str(data), "p1")
    shim = (data / "projects" / "p1" / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert 'os.environ.get("ATS_APP_ROOT")' in shim and repr(ps._APP_ROOT) in shim
    assert (data / "projects" / "p1" / "tests" / "fixtures" / "test_upload.png").is_file()

    old = data / "projects" / "p2" / "tests" / "conftest.py"
    old.parent.mkdir(parents=True)
    old.write_text(ps._V2_CONFTEST_SHIM.format(ats_root=str(data)), encoding="utf-8")
    ps.ensure_tests_scaffold(str(data), "p2")
    assert "ATS_APP_ROOT" in old.read_text(encoding="utf-8")

    custom = data / "projects" / "p3" / "tests" / "conftest.py"
    custom.parent.mkdir(parents=True)
    custom.write_text("# user conftest\n", encoding="utf-8")
    ps.ensure_tests_scaffold(str(data), "p3")
    assert custom.read_text(encoding="utf-8") == "# user conftest\n"


def test_config_loader_uses_data_root(tmp_path, monkeypatch):
    import config_loader
    monkeypatch.setenv("ATS_ROOT", str(tmp_path))
    assert config_loader.config_path() == str(tmp_path / "config.json")
    assert config_loader.ensure_config() == str(tmp_path / "config.json")
    assert (tmp_path / "config.json").is_file()   # seeded from the app's example config
    monkeypatch.delenv("ATS_ROOT")
    assert config_loader.ats_root() == config_loader.app_root()
