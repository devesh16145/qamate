# Broader public-demo benchmark gate

This extends the original smoke test without replacing Qamate's authoring or replay pipeline. It is still a public storefront suite, not a complex CRM, visual-decision or multi-app release gate.

## Tasks

`engine/public_bench_tasks.py` defines versioned contracts with SHA-256 fingerprints:

- `smoke`: the unchanged historical login, selected sort value and cart-presence task.
- `cart-edit`: product-detail navigation, two distinct products, removal of only one product, positive and negative cart assertions, and persistence after leaving and reopening the cart.
- `negative-login`: missing-username validation, missing-password validation, then successful login. No invalid-password attempts or account lockouts are requested.

All tasks use only SauceDemo's public QA account. No checkout, purchase, personal-data submission or existing project modification. Each attempt has a fresh isolated root and preserves its task contract, generated tests, events, usage and replay results.

## Run explicitly

These commands spend provider tokens. Configure the planner profile and decision key through the existing supported settings/environment mechanisms; never put keys in this document or the command arguments.

```powershell
# One harder Jev attempt, followed by two independent replays:
.\venv\Scripts\python.exe engine\pilot_saucedemo.py --provider mimo --decision-openrouter --task cart-edit --timeout 240 --tool-budget 48

# Two paid authoring attempts: matched regular/hybrid pair on one task:
.\venv\Scripts\python.exe engine\public_bench.py --provider mimo --decision-openrouter --tasks cart-edit --repeats 1

# 18 paid authoring attempts: three tasks, two arms, three repetitions:
.\venv\Scripts\python.exe engine\public_bench.py --provider YOUR_PLANNER --decision YOUR_DECIDER --repeats 3
```

The suite alternates arm order across task/repetition pairs, uses the same budgets and task contract in both arms, retains failures and their available usage, and stops on terminal provider/infrastructure errors. A bypassed hybrid attempt is not credited as a hybrid success. Unknown usage is reported separately rather than counted as zero. Median successful-run timing is descriptive; it must not hide failures or be treated as a paired causal estimate across dissimilar task mixes.

## Assertion hardening

`page_not_contains_text` is available in regular and typed goal checkpoints and records a web-first negative assertion for replay. Require positive page identity and retained-record assertions alongside absence: a blank or wrong page alone is not evidence of correct removal.

Checkpoint truth probes now fail closed: a dead page, probe exception, blank expected value or unsupported assertion cannot produce a recorded success. Case-sensitive matching agrees with generated text/URL assertions. Text assertion values are emitted using Python literal quoting. The local browser fixture tests the negative assertion and then deliberately restores the supposedly removed text; replay must fail.

Local gate for this slice: 287 offline tests and ten browser integration tests passed; agent and evaluator self-tests passed. The browser suite uses local fixtures and no paid models. Live outcomes are recorded separately below.

## Remaining evaluation gaps

Independent replay establishes executability and the recorded assertions, not full requirement coverage. Generated test meaning still needs inspection; this runner does not automatically prove that every requested outcome was asserted at the right point. Public site drift and provider variation remain confounders. Use repeated matched runs before making reliability or speed claims; pin local app fixtures before a release gate. Realistic CRM discovery, screenshot/video decisions and isolated cross-app workflows remain pending.

## First harder matched pair: 20 September 2026

Suite: `results/_public_bench/20260920-151143-2bd83feb/`. Task `cart-edit`, contract version 1, identical SHA-256 in both arms. Planner `mimo-v2.5-pro`; hybrid decision model `typesafe/jev-1.13`. Both had a 48-tool, 240-second authoring cap and two independent replays if a test was delivered. No forced hybrid instruction.

| Measure | Regular | Hybrid |
|---|---:|---:|
| Authoring outcome | Not delivered: tool budget | Delivered and independently reliable |
| Planner requests | 26 | 17 |
| Tool calls | 48 | 16 |
| Explicit plan updates | 19 | 1 |
| Combined reported tokens | 487,891 | 151,587 |
| End-to-end | 124.06 s, no replay | 156.52 s, includes replay |
| Independent replay | No test | 2/2 passed |

Regular artifact: `results/_public_pilot/20260920-151143-022cd785/`. Hybrid artifact: `results/_public_pilot/20260920-151347-3d9c267d/`.

Hybrid used 130,469 planner tokens and 21,118 decision tokens across 12 Jev requests (10,061 ms total decision time). Three goal handoffs (`stale_ref`, low-confidence planner handoff, navigation handoff) were retained and recovered without bypassing the controller. No schema validation calls failed. This is not an error-free controller trace.

Manual inspection of the generated hybrid test confirms the required ordering: product detail assertion; both products asserted on cart before removal; Backpack absent and Onesie present on cart after removal; then the same assertions after continuing shopping and reopening cart. The test preserves login and passed authoring verification as well as two independent replays.

This pair shows delivery within the same cap where the regular path exhausted it, with 68.9% fewer combined reported tokens. It does **not** establish a speedup: the regular arm never completed the task, and comparing its shorter failed-run time against completed hybrid time would be misleading. It also does not isolate Jev from the smaller prompt/tool surface and compaction. Suite exit code is intentionally nonzero because one arm failed; the failed arm is not discarded.

## Negative-login failure and bounded recovery fix

Update: the [21 September validation-context run](QAMATE_VALIDATION_CONTEXT.md) passed two independent replays without controller fallback. Earlier failures and the bounded-recovery findings remain below; one later success does not erase them or establish broad reliability.

The first negative-login attempt (`results/_public_pilot/20260920-184734-3c5e6e59/`) **timed out without a test**: 262 seconds including startup, 32 planner requests and 18 Jev requests. Planner usage is unavailable because the process hit the wall-clock deadline; its zero token fields must not be treated as zero spend. Jev reported 11,456 tokens.

The first empty-form validation succeeded. Subsequent goals repeatedly handed off on low confidence, interleaved with stale invented refs and unchanged fills. This exposed a gap: browser-action retry limits cannot stop repeated decision handoffs that execute no action. The decision payload also lacked redacted field population state.

Fixes add boolean-only populated/checked state to the decision payload (no input values), plus a session-local handoff guard: at most two equivalent unchanged-state goal failures, or four different goal failures on the same state, before disabling hybrid explicitly and exposing regular tools. Rewording the goal or reordering candidates does not reset the equivalent-failure budget. Changed page state gets a fresh budget. Fallback is emitted and counted; suite summaries do not credit mixed/fallback runs as pure hybrid successes. This is a bounded fallback, not proof that Jev solved the failing decision.

The one post-fix retry (`results/_public_pilot/20260920-185712-ab9d0122/`) also **timed out without a test**, at 247.5 seconds including startup. It made 24 planner requests and nine Jev requests (6,741 reported decision tokens); planner usage is again unknown. The circuit breaker emitted `controller_fallback` at 165.03 seconds and no later Jev requests were made. Thus bounded decision fallback is live-verified, but negative-login authoring is not fixed.

At 214.86 seconds, `get_recorded_flow` showed both validation checkpoints plus inventory URL and Products checkpoints. The planner then cleared that recording and began again instead of delivering it before the deadline. Follow-up should preserve recoverable recording versions, discourage unnecessary re-recording after verified outcomes, and improve expected-validation-error handling. Do not lower confidence thresholds simply to make this case green. The failed attempts remain part of the evidence, and field-population hints have not demonstrated a general quality gain.

Final post-fix gate: **303 tests passed** (293 offline plus ten local-browser tests), both engine self-tests passed, and `git diff --check` passed. No live defaults were changed and no commit was made. The evaluation skill guided fixed task contracts, retained failed attempts, independent replay, and separation of fallback completion from pure hybrid success.
