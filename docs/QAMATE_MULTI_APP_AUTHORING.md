# Real-planner cross-app authoring — 2026-09-21

One bounded local task passed with a real MiMo planner and Jev decisions. This establishes an autonomous authoring smoke result, not broad accuracy or efficiency gains.

## Reproduce

```powershell
.\venv\Scripts\python.exe engine/bench_multi_app.py --provider mimo --timeout 240 --tool-budget 40
```

This explicitly invokes paid APIs using environment-variable credentials. `--provider` selects any supported configured planner profile; `--decision` selects an existing decision profile, defaulting to Jev through OpenRouter. No credentials are copied into the isolated project. The fixture uses two local servers and synthetic records only. Saved ephemeral URLs are not reusable after the servers stop; rerun the benchmark to recreate them.

The planner receives a business task (create, approve the same record in another app, verify propagation, author and verify a test), not selectors, refs, tool-call instructions or a prepared workflow. Existing harness instructions still explain available tools. The benchmark caps authoring at 40 tool calls and 240 seconds, audits the requested outcome in the exported typed workflow, then runs independent positive and negative replays. Export alone never counts as verification.

## Retained result

Artifact directory: `results/_multi_app_author/20260921-100218-aa3f7fe8/`. See `scorecard.json`, `events.jsonl`, `task_contract.json`, and the generated project flow `projects/local-authoring/tests/flows/bench_multi_author/`.

- Planner: `mimo-v2.5-pro`; decision model: `typesafe/jev-1.13`. Both are replaceable profiles.
- Generated six-step workflow: open producer, Create, capture generated ID, open consumer, Approve captured row, assert Approved on the producer's same captured row. No hard-coded record ID.
- Agent verification passed; two additional fresh-process independent replays with different IDs passed (pytest durations 2.88s and 3.25s).
- Missing propagation failed (11.39s); wrong-record correlation failed (13.51s). These checks prevent an unrelated or superficial passing assertion from being the headline result.
- Authoring process wall time: 163.30s; turn completed at 156.30s; total including independent checks: 198.77s.
- Planner usage: 120,693 input + 3,236 output = **123,929 tokens**, 18 requests. Decision usage: 2,122 input + 93 output = **2,215 tokens**, three requests, 3,719ms summed decision duration. Combined reported usage: **126,144 tokens**. No monetary cost estimate or matched baseline.
- 27 tool calls, including 11 `update_plan` calls and six observations. No user questions or recorded assumed values.

## Friction retained, not hidden

The first direct click attempt was rejected by the routing guard. The first Jev Create choice had confidence 0.77 and handed off at the unchanged 0.80 threshold without executing. A later bounded goal executed Create once. Approve executed once but reported `no_progress` before asynchronous propagation; the planner observed and asserted the delayed result rather than repeating it. There were three non-success tool outcomes and no controller fallback. The final exported workflow contains no duplicated mutation.

A fixture HTTP polling connection was aborted during browser shutdown (Windows error 10053); verification still completed. This is retained in the console output, not silently treated as a task failure or removed from evidence.

## Interpretation and next work

Regression verification: **379 tests passed** across `engine/tests` and `engine/integration_tests` (164.70s); both agent/evaluator selftests passed; `git diff --check` passed. Existing Pydantic AI `prepare_tools` deprecation warnings remain. No renderer changes or new Electron visual smoke in this slice.

The evaluation skill's independent-replay and negative-control approach shaped this gate. The real planner can now author a correlated two-app test through Jev, but the task is deliberately small and uses `data-testid` controls, no login and one actor per app. It is not the requested complex-public-app benchmark or evidence of statistical reliability.

Efficiency remains poor: planner tokens and round trips dominate this sample. Next, measure planner prompt/history components and reduce irrelevant single-app context and redundant planning calls, keeping outcome audits, confidence safeguards and independent replay unchanged. Compare repeated runs against this retained contract before claiming savings. Broader app discovery, per-actor authentication and per-app artifacts remain open.

## Context-overhead follow-up

The active multi-app tool surface now describes milestone-only, batched plan updates; it no longer inherits the legacy instruction to update immediately on every action. It explicitly routes click/fill through the bounded controller, while capture/assertions remain direct. The read-test tool's multi-app description no longer recommends unavailable legacy clear/overwrite repair operations.

Hybrid history compaction now recognizes multi-app observations, replaces superseded target lists with stale-observation summaries, and preserves the latest observation even across bookkeeping turns. App/actor identity, error details and separate capture results remain available; original stored history is not mutated by outgoing compaction.

`planner_context` events report message-character buckets, tool-schema/description character counts and instruction size, without logging their content. These are pre-adapter character counts, not exact wire size, model tokens or billing. Provider token usage remains authoritative. This profiling is provider-neutral and does not change confidence, action authorization or verification gates.

Retained rerun: `results/_multi_app_author/20260921-182120-ec885a42/`, same command, task-contract hash, profiles and caps as the first authoring run. **Correctness passed; efficiency did not improve overall.**

| Metric | First authoring run | Context follow-up |
|---|---:|---:|
| Planner reported tokens | 123,929 | 125,890 |
| Decision reported tokens | 2,215 | 2,237 |
| Combined reported tokens | 126,144 | 128,127 |
| Planner requests | 18 | 19 |
| Total tool calls | 27 | 25 |
| Plan updates | 11 | 7 |
| Authoring process wall seconds | 163.30 | 209.20 |
| Total including independent checks, seconds | 198.77 | 252.88 |

Two independent fresh-ID replays passed (5.50s, 5.11s); disabled propagation and wrong-record controls both failed (13.58s each). The outcome audit passed, with no controller fallback. Four goal calls made three actual Jev requests: a low-confidence Create handoff (0.71), a stale-ref rejection before any provider call, successful Create, and successful asynchronous Approve returning no_progress. The planner attempted capture after the first handoff without a created record; that assertion failed. It subsequently recovered and exported the correct six-step workflow without duplicated mutations. The direct-controller bypass rejection from the first run was absent.

The final request's content-free accounting showed 4,059 system-prompt characters, 2,112 instruction characters, 5,407 thinking-history characters, 8,130 tool-result characters, 2,520 tool-call argument characters, and 30,077 total counted characters including remaining categories and tool definitions. This confirms repeated history is material, not that any particular bucket caused the failures. Initial counted context was 19,564 characters; no comparable bucket telemetry exists for the first run. Existing legacy `usage_est` numbers remain rough estimates and must not be substituted for reported usage.

One run per version cannot separate sampling/network variation from changes. Plan-update count fell, but total tokens and time rose: **no efficiency win is claimed**. Next targets are explicit no-action handoff guidance, obsolete checklist-history compaction, and reducing irrelevant active-mode instructions, followed by repeated matched runs. Both successful replay evidence and the failed intermediate actions remain in the event logs.

Follow-up verification: **383 tests passed** across engine unit and isolated-browser integration suites (172.41s); both agent/evaluator selftests and `git diff --check` passed. The 14 warnings are existing Pydantic AI `prepare_tools` deprecations. No Electron visual smoke or UI changes in this slice.

## Handoff and checklist follow-up

Goal feedback now separates `not_attempted`, `completed`, `partial` and `uncertain`, with attempted/completed trace counts and `verified=false`. An empty trace cannot be mistaken for an asynchronously completed click; an exception or failed dispatch cannot be presented as definitely not having mutated the app. Existing confidence, retry budgets and replay requirements are unchanged.

Outgoing hybrid compaction replaces superseded successful checklist results with a short marker. It keeps call/return pairing, the latest checklist (even beyond the normal recent window), errors, and failed/skipped milestones. A new set_plan supersedes the prior successful snapshot. Stored history is unchanged. On the frozen prior 19-request transcript, cumulative message-content characters fell from 231,140 to 218,318 (5.5%); the final request's message content fell from 19,805 to 17,150. These exclude schemas/instructions and are not token or latency measurements. The prompt-engineering skill's profiling-first approach informed this measurement.

Retained failed attempt: `results/_multi_app_author/20260921-214747-76da895d/`. It timed out at the unchanged 240s authoring cap (257.4s process wall), with no exported test or independent replay. It made 20 planner requests, seven stale-ref goals, ten observations and one rejected direct action. No Jev request was reached. Provider usage was unavailable when the process was stopped; zero-valued token fields are **unknown**, not zero spend.

Trace diagnosis: every goal included a fill on the empty `output[data-testid=record]`, followed by Create. The observation lacked control-type/editability metadata, and the empty output disappeared from actionable snapshot geometry, yielding an unhelpful stale_ref rather than identifying an invalid fill. Fresh refs could never repair that candidate list.

The resulting grounding fix adds `tag`, `input_type`, and `fillable` observations. Non-editable fills are rejected before a decision request with `unsupported_fill` and instructions to remove the fill, not refresh refs. Direct recording rechecks editability immediately before execution. Generated IDs remain capture-only in this fixture. Regression checks exercise output, editable input/textarea, read-only input, checkbox and a field becoming read-only after observation. No confidence threshold was relaxed to resolve this failure.
