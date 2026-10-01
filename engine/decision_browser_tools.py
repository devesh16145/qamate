"""Native browser bridge for the decision-owned, read-only controller."""
import json
import re
import asyncio
from urllib.parse import urlsplit

from pydantic_ai import RunContext
from decision import ChoiceDecider, safe_decision_error
from contract_review import review_contract
from decision_browser import BrowseContract, browse, candidates, origin


class BrowserBridge:
    def __init__(self, session, contract, policy="read_only", public_demo_auth=False):
        self.session, self.contract, self.bindings = session, contract, {}
        self.policy = policy
        self.public_demo_auth = public_demo_auth
        self.active_milestone = None
        self.applied_inputs = set()
        self.completed_routes = {}
        self.transition_start = None
        self.transition_step = 0
        self.transition_visits = []
        self.inputs = {slot.name: slot.value for slot in contract.inputs}

    def snapshot(self):
        obs = self.session.inspect(160)
        if self.active_milestone is not None and self.transition_start is not None and obs.get("ok"):
            page_key = self._page_key(obs.get("url", ""))
            previous = self._page_key(self.transition_visits[-1]["url"]) if self.transition_visits else self.transition_start
            step = self.session.steps[-1]["id"] if self.session.steps else 0
            if page_key != previous and step > self.transition_step:
                self.transition_visits.append({"url": obs["url"], "afterStep": step})
        targets = []
        for el in self.session.by_ref.values():
            if el.get("visible", True) and el.get("node_id"):
                target = dict(el)
                current = el.get("value")
                if el.get("role") == "combobox" and el.get("tag") == "button":
                    current = el.get("name", "").strip()
                    target["value"] = current
                target["matches_inputs"] = [name for name, value in self.inputs.items() if current == value]
                target["bound_inputs"] = [name for name, node in self.bindings.items() if node == el["node_id"]]
                targets.append(target)
        return {**obs, "targets": targets, "public_demo_auth": self.public_demo_auth}

    @staticmethod
    def _page_key(url):
        parsed = urlsplit(url)
        return (origin(url), parsed.path or "/", parsed.fragment.split("?")[0])

    @staticmethod
    def _relative_route(url):
        parsed = urlsplit(url)
        return parsed._replace(scheme="", netloc="", path="/" + parsed.path.lstrip("/")).geturl()

    def act(self, action, value=None):
        obs = self.snapshot()
        # Recheck identity, capability and origin after any input-approval pause.
        if origin(obs.get("url", "")) != origin(self.contract.start_url):
            return {"ok": False, "status": "origin_violation"}
        if action not in candidates(obs, self.contract, self.policy, self.active_milestone).values():
            return {"ok": False, "status": "stale_target", "dispatched": False}
        if action.kind == "bind":
            self.bindings[action.slot] = action.node
            return {"ok": True, "status": "field_bound", "dispatched": False}
        if action.kind == "select":
            options = self.session.by_ref[action.ref].get("options", [])
            matches = [o for o in options if not o.get("disabled") and value in {o.get("value"), o.get("label")}]
            if len(matches) != 1:
                return {"ok": False, "status": "option_not_unique"}
            value = matches[0]["value"]
        if action.kind == "pick" and value != self.inputs[action.slot]:
            return {"ok": False, "status": "approved_option_changed", "dispatched": False}
        result = self.session.act("click" if action.kind in {"pick", "open", "navigate_link"} else action.kind, action.ref, value)
        if result.get("ok") and action.kind in {"fill", "select"}:
            self.bindings[action.slot] = action.node
            self.applied_inputs.add(action.slot)
        if result.get("ok") and action.kind in {"click", "pick"}:
            for target in self.snapshot().get("targets", []):
                if target.get("role") == "combobox":
                    self.applied_inputs.update(target.get("matches_inputs", []))
        return result

    def check(self, milestone):
        if self.active_milestone is not milestone:
            self.applied_inputs.clear()
            self.transition_start = self._page_key(self.session._url())
            self.transition_step = self.session.steps[-1]["id"] if self.session.steps else 0
            self.transition_visits = []
        self.active_milestone = milestone
        observation = self.snapshot()
        if origin(observation.get("url", "")) != origin(self.contract.start_url):
            return {"ok": False, "status": "origin_violation"}
        if milestone.transition != "none":
            current = self._page_key(observation.get("url", ""))
            ready = (bool(self.transition_visits) and current != self.transition_start
                     if milestone.transition == "navigate" else
                     len(self.transition_visits) >= 2 and current == self.transition_start)
            if not ready:
                return {"ok": False, "status": "required_transition_pending",
                        "transition": milestone.transition, "observed_visits": len(self.transition_visits)}
        missing_inputs = set(milestone.inputs or []) - self.applied_inputs
        for el in observation.get("targets", []):
            if el.get("tag") in {"input", "select", "textarea"} or el.get("role") == "combobox":
                self.applied_inputs.update(el.get('matches_inputs', []))
                missing_inputs.difference_update(el.get("matches_inputs", []))
        if missing_inputs:
            return {"ok": False, "status": "required_inputs_pending", "input_slots": sorted(missing_inputs)}
        initial = len(self.session.assertions)
        try:
            for outcome in milestone.outcomes:
                if outcome.kind in {"url_matches_start", "url_matches_milestone"}:
                    expected_url = (self.contract.start_url if outcome.kind == "url_matches_start"
                                    else self.completed_routes.get(outcome.milestone_index))
                    if not expected_url:
                        return {"ok": False, "status": "referenced_milestone_not_completed"}
                    parsed_start = urlsplit(expected_url)
                    expected = parsed_start._replace(path=parsed_start.path or "/").geturl()
                    result = self.session.add_checkpoint(outcome.name, "url_equals", expected)
                    if not result.get("ok"):
                        return {"ok": False, "status": "start_url_not_observed" if outcome.kind == "url_matches_start" else "milestone_url_not_observed"}
                    # Keep the exact route but allow replay on a new fixture origin.
                    route = parsed_start._replace(scheme="", netloc="", path="/" + parsed_start.path.lstrip("/")).geturl()
                    self.session.assertions[-1].update(value=route, relative_to_base=True)
                elif outcome.kind == "observed_empty_results":
                    # Discovery assertion, not an invented business requirement.
                    lines = (observation.get("rendered_text") or {}).get("text", "").splitlines()
                    matches = [line for line in lines if re.fullmatch(r"No (?:[\w -]+ )?(?:found|results|records|matches)[.!]?", line, re.I)]
                    if len(matches) != 1:
                        return {"ok": False, "status": "empty_state_not_grounded"}
                    result = self.session.add_checkpoint(outcome.name + " (observed behavior)", "page_contains_text", matches[0])
                    if not result.get("ok"):
                        return result
                elif outcome.kind in {"links_present", "record_links_present"}:
                    prefix = outcome.value
                    if outcome.kind == "record_links_present":
                        parsed = urlsplit(observation["url"])
                        prefix = ("#" + parsed.fragment.split("?")[0] if parsed.fragment.startswith("/") else parsed.path).rstrip("/") + "/"
                        if prefix in {"/", "#/"}:
                            return {"ok": False, "status": "no_record_collection_route"}
                    # Concrete collection assertion; a heading is not row evidence.
                    selector = (f'a[href^={json.dumps(prefix)}]:visible'
                                ':not([href$="/create"]):not([href$="/new"]):not([href$="/import"])')
                    if self.session.page.locator(selector).count() < 1:
                        return {"ok": False, "status": "collection_empty"}
                    self.session.assertions.append({"type": "collection_nonempty", "selector": selector,
                        "afterStep": self.session.steps[-1]["id"] if self.session.steps else 0,
                        "description": outcome.name})
                else:
                    node = self.bindings.get(outcome.input_slot)
                    ref = next((r for r, e in self.session.by_ref.items() if e.get("node_id") == node), "") if node else ""
                    if outcome.kind == "element_has_value" and not ref:
                        return {"ok": False, "status": "input_needs_binding", "input_slot": outcome.input_slot}
                    result = self.session.add_checkpoint(outcome.name, outcome.kind, outcome.value, ref)
                    if not result.get("ok"):
                        return {"ok": False, "status": "outcome_not_observed", "outcome": outcome.model_dump()}
            parsed = urlsplit(self.session._url())
            route = "#" + parsed.fragment.split("?")[0] if parsed.fragment.startswith("/") else parsed.path
            if not route or not self.session.add_checkpoint("Observed page route (context)", "url_contains", route).get("ok"):
                return {"ok": False, "status": "route_context_unverified"}
            if milestone.transition != "none":
                for visit in self.transition_visits:
                    self.session.assertions.append({"type": "url_equals", "relative_to_base": True,
                        "value": self._relative_route(visit["url"]), "afterStep": visit["afterStep"],
                        "description": "Required observed route transition"})
            initial = None  # Commit this milestone atomically.
            index = next((i for i, item in enumerate(self.contract.milestones) if item is milestone), None)
            if index is not None:
                self.completed_routes[index] = self.session._url()
            return {"ok": True}
        finally:
            if initial is not None:
                del self.session.assertions[initial:]

    def probe(self, milestone):
        """Readiness is deterministic evidence, never a committed assertion."""
        initial = len(self.session.assertions)
        routes = dict(self.completed_routes)
        try:
            return self.check(milestone)
        finally:
            del self.session.assertions[initial:]
            self.completed_routes = routes


def register_decision_browser(agent, Deps, bro, value_gate, emit):
    @agent.tool
    async def browse_goal(ctx: RunContext[Deps], contract: BrowseContract) -> dict:
        """Delegate the ENTIRE browsing task to the decision provider.
        Supply outcomes in their required order and named approved input values,
        NEVER click/fill steps, target refs or selectors. Jev discovers targets.
        For staying on the registered entry page use url_matches_start without
        a value; never guess a login route.
        Supported: same-origin links, search/filter inputs, clear/search/filter
        buttons by default. With host-authorized session_local_forms policy only,
        also supports non-secret form fields, native selects, open options and
        create/edit/save controls in an isolated browser-local demo. Network writes
        remain blocked. Login is supported only when host settings explicitly enable
        public_demo_auth and the task supplies public demo credentials. Private login,
        uploads, arbitrary scripts and secrets are unsupported.
        Each milestone MUST include independently meaningful assertions. For a
        populated record list use links_present with its record-route prefix,
        not only a header or absence of an empty message. Do not invent a prefix;
        record_links_present derives it from the current collection route instead.
        observed_empty_results captures a uniquely observed "No ... found/results"
        message without inventing its wording. These discovery outcomes require no
        value. Pair them with page identity and exact query value outcomes.
        Value outcomes use input_slot.
        This records live outcomes only; create_test_case and run_test_case remain
        mandatory. On failure report the blocker; do not retry an uncertain action.
        """
        execution = ctx.deps.config.get("agent_execution") or {}
        if not execution.get("decision_loop_enabled") or not execution.get("hybrid_enabled"):
            return {"ok": False, "status": "disabled"}
        if ctx.deps.session._multi_app_recording is not None:
            return {"ok": False, "status": "multi_app_not_supported"}
        if getattr(ctx.deps, "decision_browser_used", False):
            return {"ok": False, "status": "contract_already_attempted", "note": "No automatic replay of uncertain actions or weakened outcomes. Start a new session for a new contract."}
        if getattr(ctx.deps, 'decision_contract_rejections', 0) >= 3:
            return {'ok': False, 'status': 'contract_coverage_retry_exhausted', 'dispatched': False}
        apps = (ctx.deps.project or {}).get("apps") or []
        if contract.start_url not in {a.get("url") for a in apps}:
            return {"ok": False, "status": "unregistered_start_url"}
        # Host requirements contain outcomes only, never actions or selectors.
        # Reject incomplete compiler output BEFORE consuming the one-run budget.
        groups = execution.get("required_outcome_groups") or []
        cursor = -1
        missing_groups = []
        for group in groups:
            required = {(o["kind"], o.get("value", "")) for o in group["outcomes"]}
            match = next((i for i, m in enumerate(contract.milestones) if i > cursor and
                          required <= {(o.kind, o.value) for o in m.outcomes}), None)
            if match is None:
                missing_groups.append(group)
            else:
                cursor = match
        if missing_groups:
            rejected = getattr(ctx.deps, 'decision_contract_rejections', 0) + 1
            ctx.deps.decision_contract_rejections = rejected
            return {"ok": False, "status": "contract_coverage_incomplete" if rejected < 3 else 'contract_coverage_retry_exhausted', "dispatched": False,
                    "missing_group": missing_groups[0]["name"], "required_outcomes": missing_groups[0]["outcomes"],
                    "missing_groups": missing_groups,
                    "note": "Compilation rejected before browser work. Preserve every listed outcome in order. After three rejected compilations report the blocker."}
        name = ((ctx.deps.config.get("llm") or {}).get("roles") or {}).get("decision")
        if not name:
            return {"ok": False, "status": "missing_decision_profile"}
        policy = execution.get("decision_loop_policy", "read_only")
        if policy not in {"read_only", "session_local_forms"}:
            return {"ok": False, "status": "unsupported_policy"}
        if policy == "session_local_forms" and contract.start_url not in execution.get("session_local_app_urls", []):
            return {"ok": False, "status": "local_forms_not_authorized"}
        if execution.get("decision_contract_review"):
            try:
                review = await asyncio.wait_for(asyncio.to_thread(review_contract,
                    getattr(ctx.deps, "decision_requirement", None), contract,
                    ChoiceDecider(ctx.deps.config, name, usage_sink=emit)), timeout=60)
            except Exception as exc:
                ctx.deps.decision_browser_used = True
                return {"ok": False, "status": "contract_review_error", "terminal": True,
                        "dispatched": False, **safe_decision_error(exc)}
            emit({"event": "decision_contract_review", **review})
            if not review["ok"]:
                ctx.deps.decision_contract_rejections = getattr(ctx.deps, "decision_contract_rejections", 0) + 1
                if review["status"] != "contract_review_rejected":
                    ctx.deps.decision_browser_used = True
                return {**review, "remaining_compilations": max(0, 3 - ctx.deps.decision_contract_rejections)}
        ctx.deps.decision_browser_used = True
        bridge = BrowserBridge(ctx.deps.session, contract, policy,
                               public_demo_auth=execution.get('public_demo_auth') is True)
        def restrict(route):
            request = route.request
            # No writes via HTTP; page navigation cannot escape the registered origin.
            blocked = request.method not in {"GET", "HEAD", "OPTIONS"}
            if request.is_navigation_request() and request.frame == bridge.session.page.main_frame:
                blocked |= origin(request.url) != origin(contract.start_url)
            route.abort() if blocked else route.continue_()
        async def perform(action):
            value = bridge.inputs.get(action.slot)
            if action.kind in {"fill", "select", "pick"}:
                value = await value_gate(ctx, action.ref, value, action.kind)
            return await bro(bridge.act, action, value)
        await bro(bridge.session.page.route, "**/*", restrict)
        try:
            await bro(bridge.session.navigate, contract.start_url)
            emit({"event": "decision_browser_contract", "contract": contract.model_dump()})
            result = await browse(contract, lambda: bro(bridge.snapshot), perform,
                                  lambda milestone: bro(bridge.check, milestone),
                                  ChoiceDecider(ctx.deps.config, name, usage_sink=emit),
                                  policy=policy, max_decisions=80 if policy == "session_local_forms" else 32,
                                  timeout=240 if policy == "session_local_forms" else 120,
                                  compact_context=execution.get("decision_context_format") == "columns",
                                  probe=lambda milestone: bro(bridge.probe, milestone), emit=emit)
            emit({"event": "decision_browser_complete", "result": result})
            ctx.deps.decision_browser_complete = bool(result.get("ok"))
            return result
        finally:
            await bro(bridge.session.page.unroute, "**/*", restrict)
