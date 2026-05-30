"""
Agrim ATS — Project Store
=========================

A **Project** is the unit a customer points the tool at: one web app, its
environments, how to log in, which LLM provider to use, and its PRD. This is the
generalization that turns the (formerly Agrim-hardcoded) engine into a tool any
team can run against their own app.

Layout (all under <ats_root>/projects/):
    projects/
      _index.json                 -> {"active": "<project-id>"}
      <project-id>/
        project.json              -> the project (schema below)
        auth/storage_state.json   -> captured login (Playwright storage_state)
        app_model.json            -> App Explorer output (Phase 1)
        requirements.json         -> PRD Extractor output (Phase 2)

project.json schema (schema_version 1):
{
  "schema_version": 1,
  "id": "acme-app",
  "name": "Acme App",
  "created_at": "2026-05-29T...",
  "environments": { "dev": "https://dev.acme.test/", "prod": "https://acme.test/" },
  "default_environment": "dev",
  "auth": {
    "type": "none" | "storage_state" | "login_steps",
    "storage_state": "auth/storage_state.json",   # relative to the project dir
    "login_path": "login",                          # optional, for heuristic login
    "credentials": { "email": "", "password": "" }  # optional, only if type==login_steps
  },
  "llm_ref": "default",        # which entry in config.json "llm.providers" to use
  "prd_path": null,            # absolute or project-relative path to a PRD .md
  "rules": {                   # opt-in, per-project behaviour (Phase 4)
    "mui_quirks": false,
    "broken_selector_fixes": false
  }
}

Node (main.js) owns CRUD from the UI; this module is the Python read-side used by
conftest / runner / app_explorer. Both agree on the schema above. Pure stdlib.
"""

import os
import re
import json
import datetime

SCHEMA_VERSION = 1


# ── Paths ────────────────────────────────────────────────────────────────────

def projects_dir(ats_root):
    return os.path.join(ats_root, "projects")


def _index_path(ats_root):
    return os.path.join(projects_dir(ats_root), "_index.json")


def project_dir(ats_root, project_id):
    return os.path.join(projects_dir(ats_root), project_id)


def project_file(ats_root, project_id):
    return os.path.join(project_dir(ats_root, project_id), "project.json")


def storage_state_path(ats_root, project_id):
    """Absolute path to a project's captured login state (may not exist yet)."""
    return os.path.join(project_dir(ats_root, project_id), "auth", "storage_state.json")


def app_model_path(ats_root, project_id):
    return os.path.join(project_dir(ats_root, project_id), "app_model.json")


def requirements_path(ats_root, project_id):
    return os.path.join(project_dir(ats_root, project_id), "requirements.json")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "project"


def _unique_id(ats_root, base):
    """Return a project id based on `base`, suffixed if it already exists."""
    existing = {p["id"] for p in list_projects(ats_root)}
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def _normalize_url(url):
    url = (url or "").strip()
    if url and not url.endswith("/"):
        url += "/"
    return url


# ── CRUD ─────────────────────────────────────────────────────────────────────

def list_projects(ats_root):
    """All projects (sorted by name). Missing/empty projects dir → []."""
    d = projects_dir(ats_root)
    if not os.path.isdir(d):
        return []
    out = []
    for entry in sorted(os.listdir(d)):
        pf = project_file(ats_root, entry)
        if os.path.exists(pf):
            proj = _read_json(pf)
            if isinstance(proj, dict) and proj.get("id"):
                out.append(proj)
    out.sort(key=lambda p: (p.get("name") or p.get("id") or "").lower())
    return out


def get_project(ats_root, project_id):
    if not project_id:
        return None
    return _read_json(project_file(ats_root, project_id))


def create_project(ats_root, name, base_url, environment="dev", **extra):
    """Create a new project and make it active. Returns the project dict."""
    pid = _unique_id(ats_root, slugify(name))
    base = _normalize_url(base_url)
    proj = {
        "schema_version": SCHEMA_VERSION,
        "id": pid,
        "name": name or pid,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "environments": {environment: base},
        "default_environment": environment,
        "auth": {
            "type": extra.get("auth_type", "none"),
            "storage_state": "auth/storage_state.json",
            "login_path": extra.get("login_path", "login"),
            "credentials": extra.get("credentials", {"email": "", "password": ""}),
        },
        "llm_ref": extra.get("llm_ref", "default"),
        "prd_path": extra.get("prd_path"),
        "rules": {"mui_quirks": False, "broken_selector_fixes": False},
    }
    _write_json(project_file(ats_root, pid), proj)
    set_active_project_id(ats_root, pid)
    return proj


def update_project(ats_root, project_id, patch):
    """Shallow-merge `patch` into a project (one level deep for dict values)."""
    proj = get_project(ats_root, project_id)
    if proj is None:
        return None
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(proj.get(k), dict):
            proj[k] = {**proj[k], **v}
        else:
            proj[k] = v
    _write_json(project_file(ats_root, project_id), proj)
    return proj


def delete_project(ats_root, project_id):
    import shutil
    d = project_dir(ats_root, project_id)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    if get_active_project_id(ats_root) == project_id:
        remaining = list_projects(ats_root)
        set_active_project_id(ats_root, remaining[0]["id"] if remaining else None)
    return True


# ── Active project ───────────────────────────────────────────────────────────

def get_active_project_id(ats_root):
    idx = _read_json(_index_path(ats_root), {}) or {}
    active = idx.get("active")
    # Validate it still exists; otherwise fall back to the first project.
    if active and os.path.exists(project_file(ats_root, active)):
        return active
    projs = list_projects(ats_root)
    return projs[0]["id"] if projs else None


def set_active_project_id(ats_root, project_id):
    _write_json(_index_path(ats_root), {"active": project_id})


def get_active_project(ats_root):
    return get_project(ats_root, get_active_project_id(ats_root))


# ── Resolution helpers (used by conftest / runner / explorer) ─────────────────

def resolve_base_url(project, environment=None):
    """The base URL for a project's environment (falls back to default env)."""
    if not project:
        return ""
    envs = project.get("environments", {}) or {}
    env = environment or project.get("default_environment")
    url = envs.get(env) or (next(iter(envs.values()), "") if envs else "")
    return _normalize_url(url)


def has_storage_state(ats_root, project_id):
    return os.path.exists(storage_state_path(ats_root, project_id))


# ── CLI (so Node/scripts can drive it without duplicating logic) ──────────────

def _emit(obj):
    print(json.dumps(obj, default=str))


def main(argv):
    if len(argv) < 2:
        _emit({"status": "error", "message": "usage: project_store.py <list|get|create|update|set-active|delete> [args]"})
        return 1
    ats_root = os.environ.get("ATS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = argv[1]

    if cmd == "list":
        _emit({"status": "success", "active": get_active_project_id(ats_root), "projects": list_projects(ats_root)})
    elif cmd == "get":
        _emit({"status": "success", "project": get_project(ats_root, argv[2] if len(argv) > 2 else get_active_project_id(ats_root))})
    elif cmd == "create":
        # project_store.py create <name> <base_url> [environment]
        if len(argv) < 4:
            _emit({"status": "error", "message": "usage: create <name> <base_url> [environment]"})
            return 1
        env = argv[4] if len(argv) > 4 else "dev"
        _emit({"status": "success", "project": create_project(ats_root, argv[2], argv[3], env)})
    elif cmd == "update":
        # project_store.py update <id> <json-patch>
        if len(argv) < 4:
            _emit({"status": "error", "message": "usage: update <id> <json-patch>"})
            return 1
        _emit({"status": "success", "project": update_project(ats_root, argv[2], json.loads(argv[3]))})
    elif cmd == "set-active":
        set_active_project_id(ats_root, argv[2])
        _emit({"status": "success", "active": argv[2]})
    elif cmd == "delete":
        delete_project(ats_root, argv[2])
        _emit({"status": "success"})
    else:
        _emit({"status": "error", "message": f"unknown command: {cmd}"})
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
