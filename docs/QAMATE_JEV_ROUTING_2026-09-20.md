# Jev: harness-owned routing pilot

20 September 2026. This follows the [earlier comparison](QAMATE_JEV_BENCHMARK_2026-09-20.md); it does not replace its failed-run evidence.

## Implemented

- Hybrid mode exposes a smaller, phase-specific planner tool set. Grounded click/fill/select go through `execute_goal`; execution-time guards prevent direct bypass. A failed controller handoff grants two direct recovery actions. Specialized keyboard/upload/coordinate tools remain explicitly available for operations outside the controller's action vocabulary.
- A concise hybrid system prompt replaces the full legacy prompt only in hybrid mode. Within-turn compaction keeps the latest percept and compact summaries of obsolete percepts without mutating persisted history or breaking tool-call pairing.
- Goals can include typed outcome checkpoints. Existing assertion/recording services evaluate them after the action group, and a fresh observation is returned. A false checkpoint remains a failure, not a recorded success.
- Missing decision configuration and terminal provider errors restore the regular tool surface explicitly. Models remain replaceable through profiles and role bindings; MiMo is only the pilot planner.

The agent-design/evaluation skill informed smaller tool surfaces, bounded recovery and separate independent replay verification. No project default was changed; hybrid remains experimental and opt-in.

## Live evidence

Run root: `results/_public_pilot/20260920-125031-9345c5db/`. Sources: `scorecard.json`, `events.jsonl`, generated test and independent replay results. The isolated run used MiMo `mimo-v2.5-pro` and OpenRouter Decisions `typesafe/jev-1.13`.

Command: `venv\Scripts\python.exe engine\pilot_saucedemo.py --provider mimo --decision-openrouter --timeout 240 --tool-budget 48`, with the authorized key supplied process-locally. **No `--require-hybrid` instruction was used.** No checkout or purchase was performed.

| Metric | Earlier corrected regular baseline | Harness-routed hybrid |
|---|---:|---:|
| Run suffix | `074849-4813daaa` | `125031-9345c5db` |
| Independent replay | 2/2 passed | 2/2 passed |
| Planner requests | 17 | 19 |
| Planner reported tokens | 325,365 | 148,289 |
| Decision reported tokens | 0 | 10,963 |
| Combined reported tokens | 325,365 | 159,252 |
| Tool calls | 30 | 28 |
| Agent turn completion from process start | 160.58 s | 157.41 s |
| Ready-to-turn completion | 130.33 s | 152.38 s |
| End-to-end including independent replay | 209.11 s | 179.83 s |

Jev handled six grounded actions across four successful goal groups: two login fills, login click, sort selection, add product and open cart. Its six requests took 5,609 ms in total. The test preserved login and passed both agent verification and two independent replays.

Combined reported tokens were **51.1% lower** than the earlier corrected baseline. This is a descriptive single-task comparison, not a controlled estimate of Jev's contribution: prompt/tool pruning and percept compaction changed too. It is not a billing savings calculation. Timing includes variable startup/model/browser latency; ready-to-completion was actually slower, so no general speed win is established.

## Limits and follow-up

- Three additional `execute_goal` calls were schema-rejected because the planner passed `checkpoints` as a JSON string instead of an array. It recovered by omitting that argument and later used standalone checkpoints. Therefore **batched controller checkpoints are locally tested, not demonstrated by this live run**.
- The scorecard's `failed_tool_results: 0` counts dictionary-shaped `ok:false` results only; it misses those validation-error arrays. Use the event trace for this limitation, not that aggregate as an error-free claim.
- Twelve `update_plan` calls and 19 planner requests remain substantial overhead. Next: count schema failures explicitly, improve typed argument compliance, reduce planning-only rounds, then run repeated matched A/B comparisons.
- The selected-value assertion proves `lohi` was selected, not complete numerical inventory ordering. This is one SauceDemo smoke workflow, not evidence of complex-site exploration accuracy, visual/video decision quality or multi-app support.
- Jev used grounded structured candidates here. No claim is made that screenshot/video understanding has been validated.
- Confidence thresholds still need calibration. Automatic candidate construction, coverage-driven discovery, richer outcomes and multi-app/actor session orchestration remain pending.

## Regression gate

Final gate: **268 tests passed**, comprising offline tests and nine local browser integration tests. Both agent and evaluator self-tests passed. Tests cover bounded routing, direct-action rejection, missing-role fallback, non-mutating percept compaction, correct GUIDED recording/replay, successful controller checkpoints and checkpoint failure recovery.

The installed Pydantic AI emits a deprecation warning for `prepare_tools`; the current hook is retained for compatibility with the supported dependency range. This is a future migration item, not a test failure.

## Follow-up: checkpoint normalization and planner overhead

Run `results/_public_pilot/20260920-131012-a5836c01/` used the same command, task, model profiles, budget and independent replay procedure. No forced-controller instruction or existing-project changes. Credentials came from the configured environment, not source files.

Changes: accept one JSON encoding layer before full typed checkpoint validation; reject unsupported/extra fields, missing values/refs and more than ten assertions; remove conflicting immediate-update instructions from hybrid plan tools; add `plan_step` for goal-owned progress. Local assertions alone never imply independent replay success. Structured tool-result outcome metadata now survives display truncation; legacy traces still have a fallback parser.

| Metric | Previous routed pilot | Overhead-fix pilot |
|---|---:|---:|
| Independent replay | 2/2 passed | 2/2 passed |
| Planner requests | 19 | 9 |
| Planner tokens | 148,289 | 60,297 |
| Jev tokens | 10,963 | 11,047 |
| Combined reported tokens | 159,252 | 71,344 |
| Jev decisions | 6 | 6 |
| Tool calls | 28 | 11 |
| Explicit checklist updates | 12 | 3 |
| Validation-rejected calls | 3 (trace audit) | 0 |
| Ready-to-turn completion | 152.38 s | 104.56 s |
| End-to-end including independent replay | 179.83 s | 132.00 s |

Combined tokens fell **55.2%** and end-to-end time **26.6%** against the previous routed pilot in this single comparison. Jev took 5,516 ms across six requests. Planner usage is provider-reported (58,204 input + 2,093 output); Jev reported 10,833 input + 214 output. No cost/cache savings estimate is inferred.

The event trace confirms all three goal calls still sent JSON-encoded checkpoint arrays. They were normalized and typed, not silently dropped. Four assertions passed and were recorded: inventory URL, exact sort-control value, cart URL and product presence. All three goals supplied `plan_step`; their status updates were handled by the harness. The generated test preserved login and passed both authoring verification and two independent replays. The pilot's explicit request for a standalone `add_checkpoint` call was satisfied semantically through the same assertion service inside the goal, not literally as a separate tool call.

Final regression gate: **290 tests passed**, including nine local browser integration tests; both self-tests passed. Tests cover malformed/double-encoded/oversized checkpoint inputs, unsupported fields/assertions, progress state on passing/failing assertions, legacy plan behavior and untruncated failure accounting. Re-analysis of the prior trace correctly reports three rejected calls; its historical scorecard was not rewritten.

Remaining limits: this is one repeated smoke task, not statistical evidence or a complex-app/multi-app benchmark. The final verification checklist update still used a standalone planner round. General exploration, candidate recall, visual decisions and cross-app workflows remain unvalidated. The skill-guided changes targeted bounded normalization, batching and trustworthy evaluation; they do not replace the provider-independent architecture.
