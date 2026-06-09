"""
Unit tests for multi-app projects: a project can target several base URLs
(storefront + admin panel + ...). Covers apps normalization, base-URL
resolution, project creation, and the agent credential priority chain.
"""

import os

import project_store as ps
import agent_chat as ac


def test_sanitize_apps_drops_empty_and_normalizes():
    apps = ps._sanitize_apps([
        {"label": "Store", "url": "https://a.test"},
        {"label": "no url"},
        {"url": "https://b.test/", "email": "x@y.z", "password": "pw"},
        "not-a-dict",
    ])
    assert [a["url"] for a in apps] == ["https://a.test/", "https://b.test/"]
    assert apps[1]["label"] == "App 2"                       # auto-label
    assert apps[1]["credentials"] == {"email": "x@y.z", "password": "pw"}


def test_project_apps_prefers_apps_field():
    proj = {"apps": [{"label": "Store", "url": "https://s.test/"},
                     {"label": "Admin", "url": "https://a.test/"}],
            "environments": {"dev": "https://legacy.test/"}}
    apps = ps.project_apps(proj)
    assert len(apps) == 2 and apps[1]["label"] == "Admin"


def test_project_apps_synthesized_from_environment():
    proj = {"name": "Old Proj", "environments": {"dev": "https://old.test/"},
            "default_environment": "dev",
            "auth": {"credentials": {"email": "e@x.y", "password": "p"}}}
    apps = ps.project_apps(proj)
    assert apps == [{"label": "Old Proj", "url": "https://old.test/",
                     "credentials": {"email": "e@x.y", "password": "p"}}]


def test_resolve_base_url_prefers_first_app():
    proj = {"apps": [{"url": "https://primary.test/"}, {"url": "https://second.test/"}],
            "environments": {"dev": "https://env.test/"}, "default_environment": "dev"}
    assert ps.resolve_base_url(proj) == "https://primary.test/"


def test_resolve_base_url_environment_fallback():
    proj = {"environments": {"dev": "https://env.test"}, "default_environment": "dev"}
    assert ps.resolve_base_url(proj) == "https://env.test/"


def test_create_project_with_apps(tmp_path):
    root = str(tmp_path)
    proj = ps.create_project(root, "Multi App", "", apps=[
        {"label": "Store", "url": "https://s.test/"},
        {"label": "Admin", "url": "https://a.test/", "email": "adm@x.y", "password": "pw"},
    ])
    assert len(proj["apps"]) == 2
    assert proj["environments"]["dev"] == "https://s.test/"   # primary back-fills env URL
    assert os.path.isfile(os.path.join(root, "projects", proj["id"], "tests", "conftest.py"))
    again = ps.get_project(root, proj["id"])
    assert again["apps"][1]["credentials"]["email"] == "adm@x.y"


def test_credentials_priority_apps_first():
    proj = {"apps": [{"url": "https://s.test/", "email": "app@x.y", "password": "app-pw"}],
            "auth": {"credentials": {"email": "auth@x.y", "password": "auth-pw"}}}
    assert ac._resolve_credentials(proj, {}) == ("app@x.y", "app-pw")


def test_credentials_priority_auth_then_config():
    proj = {"auth": {"credentials": {"email": "auth@x.y", "password": "auth-pw"}}}
    assert ac._resolve_credentials(proj, {}) == ("auth@x.y", "auth-pw")
    cfg = {"platforms": {"seller": {"users": [{"email": "cfg@x.y", "password": "cfg-pw"}]}}}
    assert ac._resolve_credentials(None, cfg) == ("cfg@x.y", "cfg-pw")
    assert ac._resolve_credentials(None, {}) == (None, None)
