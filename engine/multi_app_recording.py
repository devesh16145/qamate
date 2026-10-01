"""Grounded, session-local typed recording, separate from the legacy recorder."""
from copy import deepcopy
import hashlib
import json
import uuid

from multi_app import Binding, Step, Workflow, origin
from project_workflow import ProjectReplay, validate_project_workflow, export_project_workflow
from action_guard import GoalHandoffGuard

SENSITIVE = 'input[type=password], [autocomplete~=one-time-code], [autocomplete~=current-password], [autocomplete~=new-password]'
CONTROL_JS = """el => ({tag: el.tagName.toLowerCase(), input_type: el.getAttribute('type'),
    fillable: !el.disabled && !el.readOnly && el.getAttribute('aria-disabled') !== 'true' &&
        el.getAttribute('aria-readonly') !== 'true' && (el.isContentEditable || el.tagName === 'TEXTAREA' ||
        (el.tagName === 'INPUT' && !['button','checkbox','color','file','hidden','image','radio','range','reset','submit'].includes(el.type)))})"""


class UnsupportedFill(ValueError):
    pass


class MultiAppRecording(ProjectReplay):
    def __init__(self, browser, project):
        super().__init__(browser, deepcopy(project))
        self.steps = []
        self.bindings = {}
        self.refs = {}
        self.tainted = False
        self.goal_handoffs = GoalHandoffGuard()
        self.decision_disabled = False
        self.recovery_actions = 0
        self.preflight_failures = 0
        self.preflight_blocked = False
        self.current_binding = None

    def _clear_refs(self):
        for target in self.refs.values():
            try:
                target["handle"].dispose()
            except Exception:
                pass
        self.refs.clear()

    def close(self):
        self._clear_refs()
        super().close()

    def _binding(self, app, actor):
        apps = (self.project or {}).get("apps") or []
        entry = next((a for a in apps if a.get("id") == app), None)
        if entry is None:
            raise ValueError("Unknown app ID; save project settings first")
        binding = Binding(app=app, actor=actor, url=entry["url"])
        validate_project_workflow(self.project, {"bindings": [binding.model_dump()],
            "steps": [{"app": app, "actor": actor, "op": "open"}]})
        return binding

    def observe(self, app, actor):
        if self.tainted:
            raise ValueError("Recording has an uncertain mutation; start a new session")
        binding = self._binding(app, actor)
        self._clear_refs()
        if binding.key not in self.bindings:
            step = Step(app=app, actor=actor, op="open")
            validate_project_workflow(self.project, Workflow(bindings=[*self.bindings.values(), binding], steps=[*self.steps, step]))
            self.execute_step(binding, step)
            self.bindings[binding.key] = binding
            self.steps.append(step)
        page = self.pages[binding.key]
        if origin(page.url) != origin(binding.url):
            raise ValueError("Page escaped app binding")
        targets = []
        elements = page.locator("[data-testid]")
        total = elements.count()
        for index in range(min(total, 100)):
            handle = elements.nth(index).element_handle()
            if handle is None:
                continue
            data = handle.evaluate("""el => ({test_id: el.getAttribute('data-testid'),
                text: (el.innerText || '').slice(0,200),
                visible: !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden'),
                sensitive: el.matches('input[type=password], [autocomplete~=one-time-code], [autocomplete~=current-password], [autocomplete~=new-password]'),
                row: el.closest('[data-record-id]')?.getAttribute('data-record-id') ?? null})""")
            if not data.pop("visible") or not data["test_id"]:
                handle.dispose()
                continue
            row = data.pop("row")
            data.update(handle.evaluate(CONTROL_JS))
            data.update(handle.evaluate("""el => ({label:el.getAttribute('aria-label') || Array.from(el.labels || []).map(x=>x.innerText).join(' '),
                disabled:!!el.disabled || el.getAttribute('aria-disabled') === 'true'})"""))
            if not data['sensitive']:
                data['value'] = handle.evaluate("el => el.value === undefined ? null : el.value")
            capture = next((name for name, item in self.captures.items() if item["value"] == row), None) if row is not None else None
            if row is not None and capture is None:
                handle.dispose()
                continue  # Never freeze an observed dynamic row ID into a recording.
            locator = page.get_by_test_id(data["test_id"])
            if capture:
                escaped = page.evaluate("v => CSS.escape(v)", row)
                locator = page.locator('[data-record-id=' + escaped + ']').get_by_test_id(data["test_id"])
            if locator.count() != 1:
                handle.dispose()
                continue
            ref = "ma-" + uuid.uuid4().hex
            self.refs[ref] = {"handle": handle, "locator": locator, "binding": binding,
                              "test_id": data["test_id"], "record": capture, "sensitive": data["sensitive"],
                              "fillable": data["fillable"]}
            targets.append({"ref": ref, **data, "record": capture})
        self.current_binding = binding
        return {"ok": True, "app": app, "actor": actor, "targets": targets,
                "truncated": total > 100, "recorded_steps": len(self.steps),
                "note": "Only unique visible data-testid targets are supported; record rows need a captured ID. Re-observe after every action or app switch."}

    def act(self, op, ref, value=None, capture=None, *, preserve_refs=False):
        if self.tainted:
            raise ValueError("Recording has an uncertain mutation; start a new session")
        target = self.refs.get(ref)
        if target is None:
            raise ValueError("Unknown or stale multi-app ref; observe again")
        if op not in {"click", "fill", "capture", "expect_text"}:
            raise ValueError("Unsupported recording action")
        if op in {"click", "fill"} and self.preflight_blocked:
            raise ValueError("Repeated invalid goals blocked mutations; report the blocker and start a new session")
        if op == "fill" and target["sensitive"]:
            raise ValueError("Password/OTP recording requires secure per-actor auth support, not literal values")
        if op == "fill" and not target["fillable"]:
            raise UnsupportedFill("Target is not an editable text input. Do not fill output, readonly or non-text controls; choose supported actions from the observation.")
        binding = target["binding"]
        page = self.pages[binding.key]
        if origin(page.url) != origin(binding.url):
            raise ValueError("Page escaped app binding")
        locator = target["locator"]
        if locator.count() != 1 or not locator.evaluate("(el, old) => el === old && el.isConnected", target["handle"]):
            raise ValueError("Target changed since observation; observe again")
        if op == "fill" and not locator.evaluate(CONTROL_JS)["fillable"]:
            raise UnsupportedFill("Target is no longer editable; observe and replan without filling it.")
        if op == "fill" and locator.evaluate("(el, selector) => el.matches(selector)", SENSITIVE):
            raise ValueError("Password/OTP fields cannot be recorded as literal values")
        step = Step(app=binding.app, actor=binding.actor, op=op, test_id=target["test_id"],
                    record=target["record"], value=value, capture=capture)
        candidate = Workflow(bindings=list(self.bindings.values()), steps=[*self.steps, step])
        validate_project_workflow(self.project, candidate)
        try:
            self.execute_step(binding, step)
        except Exception:
            # A timed-out click/fill may have changed the app. Do not omit it
            # and export a falsely complete recording.
            if op in {"click", "fill"}:
                self.tainted = True
            raise
        finally:
            if not preserve_refs:
                self._clear_refs()
        self.steps.append(step)
        return {"ok": True, "op": op, "recorded_steps": len(self.steps), "capture": capture,
                "verified": False, "note": "Live outcome only; export and independently replay before delivery"}

    def goal_identity(self, actions):
        """Freeze one observed app/actor and stable retry keys, not transient ref IDs."""
        if self.tainted:
            raise ValueError("Recording has an uncertain mutation")
        targets = [self.refs.get(action.ref) for action in actions]
        if not targets or any(target is None for target in targets):
            raise ValueError("Stale goal target")
        binding = targets[0]["binding"]
        if any(target["binding"] != binding for target in targets):
            raise ValueError("A bounded goal cannot span app/actor bindings")
        if any(action.kind == "fill" and target["sensitive"] for action, target in zip(actions, targets)):
            raise ValueError("Password/OTP fills are unsupported")
        if any(action.kind == "fill" and not target["fillable"] for action, target in zip(actions, targets)):
            raise UnsupportedFill("A fill candidate is not an editable input. Remove that fill; do not retry it with fresh refs. Output IDs are captured after creation, not filled.")
        rows = sorted((action.kind, target["test_id"], target["record"] or "", action.value)
                      for action, target in zip(actions, targets))
        key = hashlib.sha256(json.dumps([binding.key, rows]).encode()).hexdigest()
        return binding, key

    def goal_snapshot(self, binding):
        """Refresh current grounded candidates without replacing their handles/refs.

        Raw form values never enter the decision payload. Hashes stay harness-side
        so changing an already-populated input still counts as a state change.
        """
        page = self.pages[binding.key]
        document = page.evaluate("token => window.__qamateMultiDocument ||= token", uuid.uuid4().hex)
        targets, elements, values, fingerprints = {}, [], {}, []
        for ref, target in self.refs.items():
            if target["binding"] != binding:
                continue
            locator = target["locator"]
            try:
                if locator.count() != 1 or not locator.evaluate("(el, old) => el === old && el.isConnected", target["handle"]):
                    continue
                row = locator.evaluate("""el => {const r=el.getBoundingClientRect();
                    const x=r.x+r.width/2, y=r.y+r.height/2, hit=document.elementFromPoint(x,y);
                    return {text:(el.innerText || el.getAttribute('aria-label') || '').slice(0,200),
                        visible:!!(r.width && r.height && getComputedStyle(el).visibility !== 'hidden'),
                        disabled:!!el.disabled || el.getAttribute('aria-disabled') === 'true',
                        populated:!!el.value, checked:!!el.checked,
                        rawValue:el.value || '', center_receives_pointer:!!hit && (hit===el || el.contains(hit))};} """)
                raw_value = row.pop("rawValue")
                fingerprints.append([target["test_id"], target["record"], row, raw_value])
                if not row["visible"] or row["disabled"]:
                    continue
                targets[ref] = {"node": ref, "app": binding.app, "actor": binding.actor,
                                "test_id": target["test_id"], "record": target["record"]}
                values[ref] = (row["populated"], row["checked"])
                element = {"ref": ref, "test_id": target["test_id"], **row}
                if target["record"] is not None:
                    captured = self.captures[target["record"]]
                    element["correlation"] = {"capture": target["record"], "source_app": captured["app"],
                        "source_actor": captured["actor"], "exact_row_match": True}
                elements.append(element)
            except Exception:
                continue  # Unknown/stale is never treated as a usable target.
        body = page.locator("body").inner_text(timeout=1000)[:6000]
        state_hash = hashlib.sha256(json.dumps([binding.key, page.url, document, body, fingerprints], sort_keys=True).encode()).hexdigest()
        return {"document": document, "url": page.url, "state_hash": state_hash,
                "targets": targets, "values": values,
                "page": {"app": binding.app, "actor": binding.actor, "elements": elements, "text": body}}

    def export(self, ats_root, tc_id, flow_id, description):
        if self.tainted:
            raise ValueError("Cannot export a recording with an uncertain mutation")
        workflow = Workflow(bindings=list(self.bindings.values()), steps=self.steps)
        return export_project_workflow(ats_root, self.project["id"], flow_id, tc_id, description, workflow)
