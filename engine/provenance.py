"""Value provenance — the enforcement core of GUIDED mode.

Guided mode's contract: the agent may only type values that are traceable to
something the user provided. That rule is useless as playbook prose (a
rationalising LLM will route around it), so it lives here as code: the
fill/select_option tool wrappers call `check()` BEFORE acting and pause for
user input when a value has no provenance.

Legitimate sources:
  - anything the user typed this session (messages, ask_user replies)
  - text of files the user attached or that the agent read from the scoped
    context folder (PRDs carry test data)
  - the project memory file (durable app knowledge like known-good SKU names)
  - the input registry (values that worked in past sessions)
  - credentials/URLs from config.json settings
  - values the page itself displayed (autocomplete options previously seen)

Pure matching logic is in `find_provenance` (unit-tested offline).
"""
import re

_WS = re.compile(r"\s+")


def _norm(s):
    return _WS.sub(" ", (s or "").strip()).casefold()


def find_provenance(value, texts, known_values):
    """Return the source label for `value`, or None when it has no provenance.

    texts: list of (source_label, text) blobs — matched by containment.
    known_values: list of (source_label, value) — matched exactly (normalized).

    Short values (<= 4 chars, e.g. qty '10') only match texts on word
    boundaries — substring matching would let '1' pass because some sentence
    contained 'Line 1'."""
    v = _norm(value)
    if not v:
        return "empty"                      # clearing a field is not inventing data
    for label, kv in known_values or []:
        if _norm(kv) == v:
            return label
    short = len(v) <= 4
    pat = re.compile(r"(?<!\w)" + re.escape(v) + r"(?!\w)") if short else None
    for label, text in texts or []:
        t = _norm(text)
        if not t:
            continue
        if (pat.search(t) if short else v in t):
            return label
    return None


class ProvenanceTracker:
    """Accumulates legitimate value sources over a session. Cheap appends;
    check() is called only on guided-mode fills."""

    def __init__(self):
        self._texts = []          # (source_label, text)
        self._values = []         # (source_label, value)

    def add_text(self, text, source="user-message"):
        t = (text or "").strip()
        if t:
            self._texts.append((source, t[:200000]))

    def add_value(self, value, source):
        v = (value or "").strip()
        if v:
            self._values.append((source, v))

    def add_values(self, values, source):
        for v in values or []:
            self.add_value(v, source)

    def check(self, value):
        """Source label for value, or None."""
        return find_provenance(value, self._texts, self._values)


def settings_values(config):
    """Flatten the credential/URL strings from config.json platforms — values an
    agent may legitimately type (e.g. logging in with configured accounts)."""
    out = []
    for plat in ((config or {}).get("platforms") or {}).values():
        if not isinstance(plat, dict):
            continue
        for u in plat.get("users") or []:
            if isinstance(u, dict):
                for k in ("email", "username", "password", "phone"):
                    if u.get(k):
                        out.append(str(u[k]))
        for k in ("url", "base_url"):
            if plat.get(k):
                out.append(str(plat[k]))
    return out


def registry_values(ats_root, project_id):
    """All values from the project's input registry (known-good past inputs)."""
    try:
        import input_registry as _ireg
        data = _ireg.get(ats_root, project_id, "") or {}
    except Exception:
        return []
    out = []
    for entries in data.values():
        for e in entries or []:
            if isinstance(e, dict) and e.get("value"):
                out.append(str(e["value"]))
    return out
