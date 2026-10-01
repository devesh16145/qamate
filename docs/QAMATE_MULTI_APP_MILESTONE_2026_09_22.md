# Multi-app authoring milestone — 2026-09-22

This bundles recovery, context handling, grounded form controls, batched outcome checks and broader evaluation. It is not completion of the full Jev rollout or proof of performance on complex public apps. Both multi-app and hybrid execution remain opt-in, with replaceable planner/decision profiles.

## Runtime changes

- **Grounded form controls:** observations expose tag, input type and editability. Output IDs, readonly fields and non-text controls cannot be mistaken for fill targets. Invalid fills are rejected before spending a decision request; editability is checked again at execution. Password/OTP restrictions remain.
- **Explicit execution evidence:** goal results distinguish no dispatch, completed traced actions, partial goals and uncertain mutations. An unknown dispatch count is null, not zero. Neither execution nor local assertions means replay verification.
- **Bounded recovery:** three consecutive invalid goals (bad candidates/stale refs/unsupported fills) block further mutations and hide the goal tool. This does not unlock direct-action fallback. Valid controller requests reset this preflight counter; existing decision-handoff guards and the 0.80 confidence threshold remain unchanged.
- **Fewer required planner round trips:** goals and successful direct capture/assertion actions return a freshly grounded observation in the same response. Reuse its refs instead of calling observe again; switching app/actor still requires observe. Auto-observation does not retry mutations, silently change apps, or convert an action failure into success. If observation fails, the action result is retained and the planner must explicitly refresh.
- **Batched outcome checks:** `multi_app_check` records up to ten exact-text assertions on current refs, reporting each result. It never clicks/fills, keeps failed checks visible, invalidates old refs afterwards and returns fresh refs. Independent exported-test replay is still mandatory.
- **Mode-specific context:** only the known generic hybrid system prompt is replaced, only in an active multi-app recording. Custom system instructions and user messages are preserved. The shorter prompt retains authorization, untrusted-page handling, provenance, correlation, uncertain-action and replay requirements. Active project instructions omit unsupported shared credentials and legacy browser guidance.
- **Evidence-preserving compaction:** superseded successful checklist snapshots shrink, but latest plans, failures and skipped milestones remain. Latest fused observations remain intact; old ones lose only stale targets, retaining captures, checks, traces, execution state and taint evidence. Original stored history is unchanged.
- **Useful failure diagnostics:** read_test_file resolves the TC-based filenames emitted by the multi-app exporter and returns validated typed workflow data alongside the wrapper. Identifier validation prevents path traversal. It no longer fails merely because it expected a legacy flow-based filename.

## Evaluation expansion

`engine/bench_multi_app.py` now has three versioned tasks:

| Task | Required outcomes |
|---|---|
| `smoke` | Create → capture ID → approve correlated record → verify producer propagation |
| `form` | Fill name/amount/notes, create, verify all three details in the other app before approval, verify propagation |
| `validation` | Assert empty-form error first, then complete the full form task |

The form fixture includes an unrelated record in the approval app. Wrong-record and disabled-propagation controls must fail; correct runs use fresh IDs. Semantic audits require the business outcomes and their ordering, rather than counting any exported green test as success. The planner receives a business task, not selectors, refs or a prepared workflow.

```powershell
.\venv\Scripts\python.exe engine/bench_multi_app.py --provider mimo --task validation --timeout 420 --tool-budget 50
```

This explicitly invokes paid APIs using configured environment-variable credentials. Use `--decision` for another configured decision profile. Synthetic fixtures use localhost only, independent isolated projects and no real records. Saved ephemeral URLs cannot be replayed after fixture shutdown; rerun the benchmark to recreate them.

## Retained failures that informed this bundle

- `20260921-214747-76da895d`: seven stale-ref goals, no Jev requests, timed out without export. The planner repeatedly tried to fill an empty output element. Control-type evidence and explicit unsupported-fill errors address this grounding defect; preflight limits now prevent unlimited refresh/retry loops.
- `20260921-215643-39aa02a0`: after the control-type fix, Jev executed Create and Approve, but the planner still timed out before export. Three decision requests reported 2,229 tokens; planner usage was unavailable at timeout and is **unknown**, not zero. This motivated combined observations, mode-specific context and batched checks.

These are retained unsuccessful attempts, not excluded from the record. The narrower prior successful smoke runs and failed efficiency comparison remain in `QAMATE_MULTI_APP_AUTHORING.md`.

Expanded attempt `20260921-235829-2d85db5d` authored a 21-step validation/form/cross-app workflow. Its semantic audit and two independent fresh-ID replays passed (7.72s, 7.91s); missing propagation and wrong-record controls failed (15.07s, 14.53s). It made three bounded-goal calls routing six mutations through Jev (including three fills plus Create within one planner call), five batched assertion calls, and four observations. Decision usage was 8,297 reported tokens across six requests. Planner usage was unknown because authoring reached the 420s cap.

This was **not completed end-to-end authoring**: self-verification failed because the first fixture version reused an old approval timestamp after Create, invalidating the agent's correct Pending-before-approval assertion. The fixture now generates a fresh ID and clears approval on every valid Create; consecutive-replay regression covers the defect. The assertion was not removed. A legacy diagnostic filename mismatch also blocked the agent from inspecting its workflow; that reader is fixed above.

The old raw scorecard labelled this attempt reliable_hybrid based on independent replay alone. Keep that raw artifact unchanged, but interpret it as **independent_replay_only**. The benchmark now reports workflow_reliable separately from authoring_success, and requires completion, export, self-verification and independent controls for the headline reliable_hybrid verdict. Every new run records implementation source hashes as well as its task-contract hash.

## Boundaries

No claim of production readiness, statistical reliability, universal control support or matched speed/token savings. Targets remain data-testid based, secure per-actor authentication and per-app artifacts remain incomplete, and no new Electron visual smoke was performed. The expanded live functional run overlapped the local regression suite, so its wall time is not a clean performance comparison. MiMo is a test profile, not an architectural dependency.
