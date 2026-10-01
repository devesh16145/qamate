# Flash + Jev public-site evaluation — 23 September 2026

## Matched public tasks

Suite: `results/_public_bench/20260923-011924-785440ba/`. Four sequential paid authoring attempts: cart-edit regular then hybrid; negative-login hybrid then regular. All used `mimo-v2.6-flash`, a 300-second authoring cap, 48-tool budget, 8192 output-token cap and two independent replays. Hybrid used `typesafe/jev-1.13`. Task contracts were unchanged from version 1. No full regression suite ran during these pairs; short offline checks did run while the benchmark was active.

| Task / arm | Authoring seconds | Total seconds with replays | Planner requests | Tool calls | Planner + decision tokens |
|---|---:|---:|---:|---:|---:|
| Cart edit, regular | 157.8 | 187.36 | 25 | 48 | 468,646 |
| Cart edit, hybrid | 166.6 | 193.78 | 20 | 24 | 178,399 |
| Negative login, regular | 96.1 | 120.05 | 14 | 28 | 242,007 |
| Negative login, hybrid | 156.3 | 176.12 | 15 | 16 | 124,836 |

All four delivered, self-verified and passed both independent replays (8/8). Both hybrid runs exercised Jev without controller fallback. Reported usage is complete. All four generated sources passed the new outcome audit when assessed after execution; original scorecards are preserved, not rewritten.

Hybrid reduced combined reported tokens by 61.9% on cart editing and 48.4% on negative login. **There was no speed improvement**: authoring took 5.6% and 62.6% longer respectively. Jev's summed decision duration was only 8.313s and 4.125s, so those inference calls alone do not explain the full slowdown. Planner orchestration, prompt/tool differences and provider variability need further measurement. One pair per task does not establish a latency distribution or broad accuracy.

Cart hybrid retained two stale-target handoffs, one low-confidence handoff, a navigation handoff and an invalid final plan-update argument. Recovery succeeded without silently dropping the required assertions. Fewer tool calls must not be reported as a measured wall-time speedup.

Attempt roots under `results/_public_pilot/`:

- Cart regular: `20260923-011924-6434e2fd`
- Cart hybrid: `20260923-012232-fe05f665`
- Negative login hybrid: `20260923-012545-43f845af`
- Negative login regular: `20260923-012842-7210a002`

## Evaluation improvements

`public_outcome_audit.py` adds a conservative, non-executing audit of the generated straight-line Playwright source for the cart-edit and negative-login contracts. It checks required assertions, stage ordering, cart page identity, before/after removal state, round-trip persistence and both negative-login outcomes before successful inventory login. Assertions across intervening mutations cannot be combined into one state. Comments, unreachable control flow, swallowed assertions, ambiguous test files, optional URL regexes and unsupported code cannot count as coverage.

This is a supported-emitter coverage check, not a general Python verifier or sandbox. Unsupported forms are unassessed, not necessarily incorrect. Independent replay remains mandatory. New public pilots preserve `replay_verdict` and report `coverage_unverified` when replay passes but required coverage cannot be established; coverage never upgrades a failed execution. The smoke and new CRM contracts are outside this audit's current scope.

Independent replay now retains authoritative JUnit evidence when an explicit run directory is supplied, in unique `verification-*` children. Benchmark replays receive distinct invocation/run artifact directories, returned in the scorecard. This prevents overwriting a previous replay or accepting its stale green report. Existing no-artifact callers keep temporary behavior. New public pilots record implementation hashes and resolved model profiles. These evaluation changes were made while the paired runner already had its old modules loaded; the pairs above were post-audited, not retroactively presented as runs of the new evaluator.

## Realistic CRM extension

Added `crm-search`: a bounded read-only Companies search/empty-state/reset task on the official Atomic CRM demo, with an explicit target URL. No private authentication, record mutation, imports/exports or messaging is authorized. The task forbids fixed expectations on randomly generated company names/counts. The generic public pilot now uses each task's registered target rather than hard-coding SauceDemo. The forced SauceDemo-login diagnostic is rejected for this task.

Official references: [Atomic CRM](https://github.com/marmelab/atomic-crm), [FakeRest demo provider](https://marmelab.com/atomic-crm/doc/developers/data-providers/). The local Docker executable is installed but its Linux daemon was unavailable at the read-only preflight; no Docker service was started. The public read-only pilot does not need that dependency. A pinned local CRM with reproducible data remains necessary for a release benchmark.

## Verification and remaining gates

447 offline/local-browser tests passed, with 17 existing PydanticAI deprecation warnings. Both engine self-tests passed. The subsequently added CRM target contract passed the focused 44-test public benchmark/audit suite. No Electron UI changes or visual smoke, no commits, no memory writes.

The overall goal is **not achieved**: neither public pair meets the 2x speed target or the 70% input-token-reduction target. These four successes are not the planned held-out suite. Requirement-directed exploration, realistic CRM breadth, secure per-actor multi-app authentication and a second live planner protocol still need evidence. Experimental flags remain opt-in; Flash is the configured MiMo test model, not a product dependency.
