# MiMo and Jev browser pilot — 2026-09-20

## Conclusion

Jev successfully executed real, grounded browser actions through Qamate's controller. A test authored with those actions passed two agent replays and two independent replays. **The current hybrid architecture did not demonstrate a speed, token, or accuracy improvement. Keep it opt-in.**

This is a diagnostic on one SauceDemo flow, not a statistically meaningful benchmark or a complex/multi-app evaluation. The final successful hybrid run explicitly required controller use after an ordinary opt-in run bypassed it; it is not evidence of reliable automatic routing.

## Fixed task and limits

Public demo login, select low-to-high sort, verify selected value `lohi`, add Sauce Labs Onesie, open cart and assert its presence. No checkout or purchase. Each run used a fresh isolated root, MiMo `mimo-v2.5-pro`, 48 tool calls maximum, 240-second authoring limit, and up to two independent replays (stop on first failure). Jev was `typesafe/jev-1.13` through OpenRouter Decisions. No production project settings were changed.

The sort assertion checks the selected control, not the numeric order of the entire product list. All generated tests and raw events remain under `results/_public_pilot/<run>/`.

## All attempts, including failures

| Run directory suffix | Mode | Outcome | MiMo requests | Reported MiMo tokens |
|---|---|---|---:|---:|
| `20260920-000834-fed44511` | Original baseline | Independent replay 2/2 | 18 | 653,616 |
| `20260920-001111-8c87b1fb` | Original hybrid | Timed out; independent replay failed | 33 | Unknown; input-only estimate 883,198 |
| `20260920-074849-4813daaa` | Corrected baseline | Independent replay 2/2 | 17 | 325,365 |
| `20260920-075254-23858be3` | Corrected opt-in hybrid | MiMo HTTP 400; no test delivered; Jev not invoked | 17 | 295,190, incomplete |
| `20260920-075555-35c64f70` | Explicit-controller diagnostic | Independent replay 2/2 | 17 | 328,872 |

The timeout's legacy zero token field is missing usage, not zero consumption. The harness now initializes `usage_incomplete=true` until a usage event arrives. Input estimates are not comparable to provider-reported totals or billing.

### Original hybrid failure

Jev completed username fill, password fill, and Login click correctly (three calls, 1,948 reported tokens, 2.656 seconds aggregate decision-request latency). MiMo then cleared the login recording, citing the playbook's unconditional statement that the runner logs in. The generated test began on the inventory route without authenticated replay state and failed; recovery exhausted the authoring deadline. MiMo also had an approximately 49-second response gap before the controller call.

Fixed the conflicting playbook rule: omit login only with confirmed runner authentication, preserve it when explicitly requested, and inject explicit guidance for projects with `auth.type=none`. Both subsequent modes used this correction. This is guidance hardening, not a deterministic authentication invariant.

### Corrected opt-in failure

MiMo used ordinary tools throughout, emitted the nonexistent tool `update_planobserve`, then received HTTP 400 on the following model request. The temporal sequence is recorded; the provider's generic error does not prove the exact internal cause. Zero Jev calls means this run cannot measure Jev performance. It does demonstrate that the current optional-tool routing is unreliable.

## Successful baseline versus explicit-controller diagnostic

| Metric | Corrected MiMo-only | Explicit MiMo + Jev |
|---|---:|---:|
| Agent verification | 2/2 passed | 2/2 passed |
| Independent verification | 2/2 passed | 2/2 passed |
| MiMo requests | 17 | 17 |
| Jev requests | 0 | 3 |
| MiMo tokens, provider-reported | 325,365 | 328,872 |
| Jev tokens, provider-reported | 0 | 1,963 |
| Combined reported tokens | 325,365 | 330,835 |
| Time from process start to completed agent turn | 160.58 s | 204.03 s |
| Time from browser-ready to completed agent turn | 130.33 s | 190.45 s |
| Agent-visible tool calls | 30 | 26 |

Token volume increased approximately 1.7%; no cost saving is established. Token totals include input plus output, not currency or cache-adjusted billing. Jev itself reported $0.000077658 for its three final diagnostic requests, with 4.594 seconds aggregate client request latency. Its controller completed login in about 6.13 seconds including observation and browser execution. Confidence values of 0.98–0.99 are provider scores, not calibrated accuracy estimates.

The controller collapsed three browser actions into one planner tool call, but did not reduce the 17 planner requests. MiMo-only already batched login actions in one response. Fewer visible tool calls therefore did not translate to lower token volume.

These are single, sequential, non-randomized runs. Response latency, startup, optional plan/story calls and host load varied; local regression tests also overlapped part of the corrected baseline. Timing is descriptive, not a causal speed estimate. Do not infer a general accuracy percentage or model superiority from these runs.

## Next implementation priorities

1. Harness-owned routing for eligible bounded goals, with explicit fallback, rather than hoping the planner chooses an optional tool. Keep provenance, stale-node and action guards in force.
2. Reduce planner requests and repeated context: compact within long turns, batch plan updates, restrict tools by phase, and measure every model role separately.
3. Enforce replay prerequisites and recording continuity so login cannot silently disappear from a flow that needs it.
4. Then repeat several matched tasks and seeds, alternating run order without concurrent workloads. Include native/custom selects, repeated rows, validation, and navigation before expanding to multi-app workflows.

## Reproduction

The key must already be supplied securely in the process environment. No key is stored in this report or benchmark config.

```powershell
.\venv\Scripts\python.exe engine\pilot_saucedemo.py --provider mimo --timeout 240 --tool-budget 48
.\venv\Scripts\python.exe engine\pilot_saucedemo.py --provider mimo --decision-openrouter --timeout 240 --tool-budget 48
# Diagnostic treatment, not evidence of automatic routing:
.\venv\Scripts\python.exe engine\pilot_saucedemo.py --provider mimo --decision-openrouter --require-hybrid --timeout 240 --tool-budget 48
```

Regression gate after the shared authentication-guidance and missing-usage fixes: **258 tests passed** (249 offline, nine local-browser integration tests). Live-model outcomes are separate from that gate.
