"""
Persistent input registry — stores successful form-field values per URL route.

Keyed by the URL fragment / path (so dev and staging share the same entries).
Sensitive fields (password, token, otp, etc.) are skipped automatically.
Stored at:  projects/<project_id>/input_registry.json
            (falls back to <ats_root>/.input_registry.json for legacy / no-project use)
"""

import json
import os
import re
from datetime import datetime, timezone

# Fields to never store (security / ephemeral)
_SENSITIVE_PATTERNS = re.compile(
    r"password|passwd|secret|token|otp|pin|cvv|card.?num|ssn|auth.?code",
    re.I,
)
# Selector patterns that indicate sensitive inputs
_SENSITIVE_SELECTORS = {
    'input[type="password"]',
    "input[type='password']",
    'input[name="password"]',
    'input[name="passwd"]',
}


def _registry_path(ats_root: str, project_id: str | None) -> str:
    if project_id:
        return os.path.join(ats_root, "projects", project_id, "input_registry.json")
    return os.path.join(ats_root, ".input_registry.json")


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def _save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _normalize_url(url: str) -> str:
    """Keep only the fragment/path — strip origin so dev/staging share entries."""
    if not url:
        return "unknown"
    # Hash-routed SPAs: keep #/path
    m = re.search(r"(#/.+)", url)
    if m:
        return m.group(1).split("?")[0]  # drop query string
    # Plain paths: keep /path
    m = re.search(r"(https?://[^/]+)(/[^?#]*)", url)
    if m:
        return m.group(2) or "/"
    return url[:120]


def is_sensitive(field_name: str, selector: str = "") -> bool:
    return bool(
        _SENSITIVE_PATTERNS.search(field_name or "")
        or _SENSITIVE_PATTERNS.search(selector or "")
        or selector in _SENSITIVE_SELECTORS
    )


def record(
    ats_root: str,
    project_id: str | None,
    url: str,
    field_name: str,
    value: str,
    field_type: str = "text",
    selector: str = "",
    note: str = "",
) -> bool:
    """Append a successful input to the registry. Returns True if recorded, False if skipped."""
    if not value or not value.strip():
        return False
    if is_sensitive(field_name, selector):
        return False

    path = _registry_path(ats_root, project_id)
    data = _load(path)
    key = _normalize_url(url)
    entries = data.setdefault(key, [])

    # Update in-place if same field+value already exists (no duplicates)
    for entry in entries:
        if entry.get("field") == field_name and entry.get("value") == value.strip():
            entry["ts"] = _now()
            if note:
                entry["note"] = note
            _save(path, data)
            return True

    # New entry
    entry: dict = {
        "field": field_name,
        "type": field_type,
        "value": value.strip(),
        "ts": _now(),
    }
    if note:
        entry["note"] = note
    if selector and selector != field_name:
        entry["selector"] = selector
    entries.append(entry)
    _save(path, data)
    return True


def get(ats_root: str, project_id: str | None, url_filter: str = "") -> dict:
    """Read the registry. If url_filter is given, return only matching routes."""
    path = _registry_path(ats_root, project_id)
    data = _load(path)
    if not url_filter:
        return data
    q = url_filter.lower()
    return {k: v for k, v in data.items() if q in k.lower()}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
