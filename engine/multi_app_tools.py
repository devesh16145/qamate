"""Explicitly enabled provider-neutral multi-app recording tools."""
from pydantic_ai import RunContext
from goal_controller import GoalAction
from decision import ChoiceDecider
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field


class MultiAppCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(min_length=1)
    value: str


def register_multi_app_tools(agent, deps_type, bro, value_gate, event_sink=None):
    # Runtime annotation must be concrete for the tool schema builder.
    context_type = RunContext[deps_type]

    def enabled(deps):
        return bool((deps.config.get("agent_execution") or {}).get("multi_app_enabled"))

    def decision_owned(deps):
        return bool((deps.config.get('agent_execution') or {}).get('multi_app_decision_loop_enabled'))

    async def prepare(ctx, definition):
        return definition if enabled(ctx.deps) else None

    def hybrid(deps):
        return bool((deps.config.get("agent_execution") or {}).get("hybrid_enabled"))

    async def prepare_goal(ctx, definition):
        recorder = getattr(ctx.deps.session, "_multi_app_recording", None)
        return definition if enabled(ctx.deps) and hybrid(ctx.deps) and not getattr(recorder, "decision_disabled", False) and not getattr(recorder, "preflight_blocked", False) else None

    async def with_observation(recorder, result):
        """Return newly grounded refs with the outcome; never undo or retry a mutation."""
        result = dict(result)
        binding = getattr(recorder, "current_binding", None)
        if binding is not None and not recorder.tainted:
            try:
                result["observation"] = await bro(recorder.observe, binding.app, binding.actor)
            except Exception:
                result["observation_error"] = "Fresh observation unavailable; call multi_app_observe before acting"
        return result

    @agent.instructions
    async def multi_app_guidance(ctx: context_type) -> str:
        if not enabled(ctx.deps):
            return ""
        if decision_owned(ctx.deps):
            return 'Use browse_workflow for all browsing. No planner-selected refs/actions or fallback. Export only after every required outcome is observed; independently replay before delivery.'
        return ("Experimental multi-app recording is available for explicitly requested cross-app tasks. "
                "Use multi_app_catalog for saved app/actor IDs. First multi_app_observe starts a separate "
                "isolated recording; thereafter only multi_app_* tools drive its browsers. Capture the "
                "created record ID before switching apps. Reuse fresh refs from returned observation; "
                "call observe only to switch apps or refresh missing/changed state. No literal "
                "password/OTP recording or shared login state. When hybrid is enabled route click/fill "
                "through multi_app_execute_goal; capture and exact-text assertions use multi_app_act. "
                "A goal stays in one app/actor, never switches apps, and does not prove an outcome. "
                "Use at most a few outcome milestones; update them only after verification or a blocker, "
                "batched with useful work. No separate active/done updates per action; multi-app goals "
                "do not accept plan_step. "
                "multi_app_create_test exports to a NEW flow; run_test_case must pass "
                "before delivery. Unsupported targets or auth are blockers, not permission to use the "
                "legacy recorder. Uncertain mutations require a new session; report the reason.")

    @agent.tool(prepare=prepare, sequential=True)
    async def multi_app_catalog(ctx: context_type) -> dict:
        """List this project's app/actor IDs without credentials. Multi-app is
        experimental, isolated, data-testid-only. Hybrid click/fill uses the
        configured replaceable decision profile. Observe starts a separate recording; use only
        multi_app_* browser tools thereafter and replay exported tests.
        """
        if not enabled(ctx.deps):
            return {"ok": False, "status": "disabled"}
        apps = (ctx.deps.project or {}).get("apps") or []
        return {"ok": True, "apps": [{key: app.get(key) for key in ("id", "label", "url", "actors")} for app in apps],
                "note": "Missing IDs/actors require saving Project Settings; no shared auth is imported"}

    @agent.tool(prepare=prepare, sequential=True)
    async def multi_app_observe(ctx: context_type, app: str, actor: str) -> dict:
        """Opt-in isolated app/actor recording. Use IDs from project settings.
        First call opens a fresh session, never the shared login capture. Returns
        unique visible data-testid refs. Other app switches invalidate old refs.
        Only multi_app_* tools record this flow. Password/OTP fills unsupported.
        """
        if decision_owned(ctx.deps):
            return {'ok': False, 'status': 'decision_workflow_required'}
        if not enabled(ctx.deps):
            return {"ok": False, "status": "disabled"}
        def observe():
            from multi_app_recording import MultiAppRecording
            session = ctx.deps.session
            recorder = getattr(session, "_multi_app_recording", None)
            if recorder is None:
                recorder = MultiAppRecording(session.browser, ctx.deps.project)
                # Validate before switching surfaces; a typo cannot trap a session.
                recorder._binding(app, actor)
                session._multi_app_recording = recorder
            return recorder.observe(app, actor)
        try:
            return await bro(observe)
        except Exception as exc:
            return {"ok": False, "status": "multi_app_observation_failed", "error": type(exc).__name__,
                    "detail": str(exc) if type(exc) is ValueError else "Browser observation unavailable",
                    "note": "Check registered app/actor and browser state; do not fall back to legacy browser tools"}

    @agent.tool(prepare=prepare, sequential=True)
    async def multi_app_act(ctx: context_type, op: str, ref: str,
                            value: str | None = None, capture: str | None = None) -> dict:
        """Record one grounded click/fill/capture/expect_text. Capture a visible record ID
        with a name before operating on its row in another app; row correlation is
        harness-owned. expect_text requires exact expected text. Reuse returned observation refs.
        Live assertions are not independent verification. Password/OTP fills unsupported.
        """
        if decision_owned(ctx.deps):
            return {'ok': False, 'status': 'decision_workflow_required'}
        if not enabled(ctx.deps):
            return {"ok": False, "status": "disabled"}
        recorder = getattr(ctx.deps.session, "_multi_app_recording", None)
        if recorder is None:
            return {"ok": False, "status": "observe_first"}
        if op in {"click", "fill"} and getattr(recorder, "preflight_blocked", False):
            return {"ok": False, "status": "preflight_retry_exhausted", "terminal": True}
        if op in {"click", "fill"} and hybrid(ctx.deps) and not recorder.decision_disabled:
            if recorder.recovery_actions <= 0:
                return {"ok": False, "status": "controller_required", "note": "Use multi_app_execute_goal with current refs"}
            recorder.recovery_actions -= 1
        if op == "fill" and value is not None:
            value = await value_gate(ctx, ref, value, "fill")
        try:
            result = await bro(recorder.act, op, ref, value, capture)
            return await with_observation(recorder, result)
        except Exception as exc:
            return {"ok": False, "status": "multi_app_action_failed", "error": type(exc).__name__,
                    "tainted": recorder.tainted,
                    "detail": str(exc) if type(exc) is ValueError else "Action/assertion did not complete successfully",
                    "note": "Re-observe for stale refs. Uncertain mutations block export; password/OTP fills are unsupported."}

    @agent.tool(prepare=prepare_goal, sequential=True)
    async def multi_app_execute_goal(ctx: context_type, goal: str, actions: list[GoalAction]) -> dict:
        """Bounded decision-provider routing (up to eight grounded click/fill candidates)
        inside ONE observed app/actor. Uses the configured decision role, including Jev.
        Values stay in the harness; no generated targets or app switches. Stops on stale
        state, uncertainty or no progress. Capture IDs and assert exact outcomes separately
        with refs from the returned observation. No extra observe needed unless switching apps
        or the page changes. Execution is NOT independent verification.
        """
        if decision_owned(ctx.deps):
            return {'ok': False, 'status': 'decision_workflow_required'}
        if not enabled(ctx.deps) or not hybrid(ctx.deps):
            return {"ok": False, "status": "disabled"}
        recorder = getattr(ctx.deps.session, "_multi_app_recording", None)
        if recorder is None:
            return {"ok": False, "status": "observe_first"}
        if getattr(recorder, "preflight_blocked", False):
            return {"ok": False, "status": "preflight_retry_exhausted", "terminal": True}
        if recorder.decision_disabled:
            return {"ok": False, "status": "decision_disabled", "fallback": True}
        name = ((ctx.deps.config.get("llm") or {}).get("roles") or {}).get("decision")
        if not name:
            recorder.decision_disabled = True
            result = {"ok": False, "status": "not_configured", "terminal": True, "fallback": True}
        else:
            from multi_app_goal import execute_recording_goal
            async def authorize(action):
                return await value_gate(ctx, action.ref, action.value, "fill")
            try:
                result = await execute_recording_goal(recorder, goal, actions, bro,
                    ChoiceDecider(ctx.deps.config, name, usage_sink=event_sink), authorize)
            except Exception as exc:
                # No silent retry loop when adapter configuration or browser state is broken.
                recorder.decision_disabled = True
                result = {"ok": False, "status": "controller_error", "error": type(exc).__name__, "fallback": True}
        from multi_app_goal import goal_feedback
        if not result.get("trace") and result.get("status") in {"invalid_goal", "duplicate_action", "stale_ref", "unsupported_fill"}:
            recorder.preflight_failures = getattr(recorder, "preflight_failures", 0) + 1
            if recorder.preflight_failures >= 3:
                recorder.preflight_blocked = True
                result = {**result, "cause": result["status"], "status": "preflight_retry_exhausted", "terminal": True}
        else:
            recorder.preflight_failures = 0
        result = goal_feedback(result)
        if getattr(recorder, "preflight_blocked", False):
            result["note"] = "Three invalid goals exhausted preflight recovery. No mutation was dispatched. Report the blocker; a new session is required, not another observe/retry loop."
        if event_sink:
            event_sink({"event": "goal_execution", "scope": "multi_app", "result": result})
            if result.get("fallback"):
                event_sink({"event": "controller_fallback", "scope": "multi_app", "reason": result["status"]})
        if result.get("fallback"):
            result["note"] = "Multi-app decision routing disabled for this session. Report fallback; regular multi_app_act remains available."
        result = dict(result)
        result["trace"] = [{k: row[k] for k in ("kind", "ref", "ok")} for row in result.get("trace", [])]
        return await with_observation(recorder, result)

    @agent.tool(prepare=prepare, sequential=True)
    async def multi_app_check(ctx: context_type, checks: Annotated[list[MultiAppCheck], Field(min_length=1, max_length=10)]) -> dict:
        """Record up to ten exact-text outcome assertions on current refs in one call.
        Read-only, no clicks/fills; capture record IDs separately. Returns per-check results
        and fresh observation. Local assertions do not replace exported-test replay.
        """
        if decision_owned(ctx.deps):
            return {'ok': False, 'status': 'decision_workflow_required'}
        if not enabled(ctx.deps):
            return {"ok": False, "status": "disabled"}
        recorder = getattr(ctx.deps.session, "_multi_app_recording", None)
        if recorder is None:
            return {"ok": False, "status": "observe_first"}
        if recorder.tainted:
            return {"ok": False, "status": "uncertain_recording", "verified": False}
        results = []
        try:
            for check in checks:
                try:
                    await bro(recorder.act, "expect_text", check.ref, check.value, preserve_refs=True)
                    results.append({"ref": check.ref, "ok": True})
                except Exception as exc:
                    results.append({"ref": check.ref, "ok": False, "error": type(exc).__name__})
        finally:
            await bro(recorder._clear_refs)
        return await with_observation(recorder, {"ok": all(row["ok"] for row in results), "checks": results, "verified": False})

    @agent.tool(prepare=prepare, sequential=True)
    async def multi_app_create_test(ctx: context_type, tc_id: str, flow_id: str, description: str) -> dict:
        """Export the complete observed multi-app recording to a NEW project flow.
        Requires an outcome assertion. Export is unverified: use run_test_case and
        independent replay before claiming success. No literal passwords or OTPs.
        """
        if decision_owned(ctx.deps) and not getattr(ctx.deps, 'decision_workflow_complete', False):
            return {'ok': False, 'status': 'outcomes_incomplete'}
        if not enabled(ctx.deps):
            return {"ok": False, "status": "disabled"}
        recorder = getattr(ctx.deps.session, "_multi_app_recording", None)
        if recorder is None:
            return {"ok": False, "status": "observe_first"}
        try:
            result = await bro(recorder.export, ctx.deps.ats_root, tc_id, flow_id, description)
            result["assumed_values"] = list(ctx.deps.session.assumptions)
            return result
        except Exception as exc:
            return {"ok": False, "status": "multi_app_export_failed", "error": type(exc).__name__,
                    "note": "Use a new flow/TC ID, passing outcome assertion, and unchanged project bindings; never deliver as verified"}
