# Multi-app decision routing — 2026-09-21

The opt-in multi-app recorder now routes grounded click/fill candidates through the existing replaceable decision role, including Jev. It reuses `execute_bounded` rather than introducing a second provider stack. The confidence threshold remains 0.80.

## Runtime behavior

- Enable both experimental multi-app recording and the bounded decision controller in AI/LLM Settings, bind a decision profile, save and restart the agent.
- `multi_app_execute_goal(goal, actions)` accepts up to eight click/fill candidates within one observed app/actor. The model cannot switch apps, invent locators, or choose input values. Capture IDs and assert outcomes separately with `multi_app_act` after observing fresh refs.
- The harness freezes original DOM-node identity, refreshes state before executing a choice, and rechecks after any input-approval pause. Raw input values are excluded from the decision form state; local hashes distinguish changes to already-populated fields.
- Live recording and replay share the same executor. References remain usable only inside the bounded goal and are invalidated afterwards. The decision payload includes harness-verified captured-record correlation: capture name, originating app/actor, and exact row match.
- Direct click/fill calls are rejected while decision routing is active, except for two recovery actions after a handoff. Missing/terminal provider errors and repeated unchanged-state handoffs disable decision routing for that recording and explicitly report fallback. No hidden provider substitution.
- A no-progress handoff can follow a successful asynchronous mutation. Its trace records execution; the agent is directed to observe/assert the delayed outcome, not blindly repeat the mutation. This is not a verified outcome until assertions and independent replay succeed.
- Usage and goal events use the existing event stream, with multi-app scope. Captures/assertions remain harness operations, not Jev decisions.

## Retained live evidence

Command: `venv\Scripts\python engine\pilot_multi_app.py --decision-openrouter`

This is a scripted integration smoke, **not an autonomous planner-authoring benchmark**. Each attempt allows at most two decision calls on synthetic local HTTP apps. No existing project, credential, business record or real app was modified. Reports are under `results/_multi_app_pilot/`.

| Attempt | Outcome | Reported decision tokens |
|---|---|---:|
| `20260921-094851-77d8f51f` | Create executed; approval confidence 0.47 caused handoff. No export/replay. | 1,572 |
| `20260921-094955-3d920cb7` | Added correlation evidence; approval confidence 0.78 still caused handoff. No export/replay. | 1,689 |
| `20260921-095205-59e7b0e4` | Corrected fixture to separate producer/consumer controls and enforce backend roles. Both selected actions executed; exported workflow passed two independent replays. | 1,570 |

Total across all three attempts: six decision requests, 4,831 reported tokens. Different fixture/payload versions and single attempts do not establish a causal accuracy improvement.

Successful attempt details: Jev `typesafe/jev-1.13`, confidence 0.95 for Create and 0.87 for Approve. Decision-call durations were 1,141 ms and 781 ms. The approval click returned `no_progress` before its asynchronous status update; the subsequent assertion verified `Approved`. The scripted harness handled that handoff without repeating the mutation or substituting another decider.

The exported workflow is open producer → create → capture generated ID → open consumer → approve captured row → assert Approved in producer. It contains the capture name, not the original generated ID. Two fresh pytest processes passed with different record IDs (`1 passed in 6.24s`, `1 passed in 7.18s`). Disabling propagation yielded `1 failed in 15.31s`. Total pilot wall time was 53.95s, including browser setup, export, two replays and the negative control, while other local tests were running. This is not a speed comparison.

The report and generated files are retained in the successful attempt directory. The local fixture servers stop after the pilot; re-run the pilot script to recreate a live fixture, rather than running those saved ephemeral-port URLs on their own.

## Verification and remaining work

Follow-up: a real MiMo planner subsequently authored this local cross-app flow through Jev and passed independent positive and negative checks. See `QAMATE_MULTI_APP_AUTHORING.md`; the evidence below describes the earlier scripted smoke.

Full offline/browser suite: 371 passed. Both selftests and renderer compilation passed. Focused tests cover unchanged-state retry limits, terminal errors, stale state, approval delays, input redaction, batched fills, cross-app correlation and replay with fresh IDs. Existing Pydantic AI `prepare_tools` deprecation warnings remain.

Still unproven: autonomous planner discovery/authoring on complex apps, broad reliability, token savings versus a matched baseline, and end-to-end speed gains. Still limited: `data-testid` targets, no password/OTP literal recording, session-local recordings, and incomplete per-app artifacts/auth support. The experiment is OFF by default. No Electron visual smoke was performed.
