# Qamate reliability and replaceable-model implementation

Status: first implementation slice, 2026-09-19. Experimental hybrid execution is OFF unless explicitly enabled. This is not completion of the full rollout in `QAMATE_JEV_PLAN.md`.

## Implemented

- 2026-09-25/26: new opt-in **decision-owned read-only browsing** replaces planner-selected action candidates with fresh DOM-derived choices inside `browse_goal`. Jev owns actions, waits and milestone-check decisions across routes. Three fresh CRM authoring runs on identical runtime/task hashes passed all source outcome audits and **6/6 independent replays**, including positive list restoration. Per run: 8–9 Jev decisions, 5–6 compiler/export/reporting requests; authoring 92.1–147.5 seconds (median 93.9), combined usage 67,424–74,891 tokens (median 71,924). Full regression gate: 492 passed; seven later focused native-browser tests include hidden-row and create-only negative controls. Three earlier development failures are retained. This is one task family, not a matched performance comparison or broad accuracy claim. This is not yet the default UI mode or support for authentication/business writes/multi-app transitions. See `QAMATE_DECISION_OWNED_BROWSER.md` for architecture, safeguards, scope and evidence.

- 2026-09-25: CRM-derived reliability bundle adds bounded rendered text, capability-aware vision exposure, deterministic assertion-only goals and correct navigation generation for subpath-hosted apps. Full gate: **465 tests** and both selftests passed. Retained CRM failures exposed missing page evidence and a duplicated replay URL; no blanket accuracy or speed claim. See `QAMATE_CRM_RELIABILITY_2026_09_25.md` for live evidence and coverage limits.

- 2026-09-23: Flash regular/hybrid public cart-edit and negative-login pairs all passed two independent replays and post-run outcome audits. Hybrid reduced combined tokens by 61.9% and 48.4%, but was slower in both pairs; no speed-goal claim. Added fail-closed generated-outcome coverage checks, retained per-replay JUnit evidence, implementation manifests and a read-only Atomic CRM task. See `QAMATE_PUBLIC_FLASH_2026_09_23.md` for artifacts, limits and remaining gates.

- Bundled multi-app authoring milestone (2026-09-22): control-type grounding and editability checks, explicit no-action/uncertain feedback, three-invalid-goal circuit breaker without direct fallback, fused fresh observations, typed batched outcome assertions, active-mode prompt reduction and evidence-preserving checklist compaction. The benchmark now includes form details, validation errors and a decoy record in addition to the original smoke task. See `QAMATE_MULTI_APP_MILESTONE_2026_09_22.md` for scope, retained failures and verification; the overall rollout remains experimental.

- Multi-app context follow-up: stale observations now compact correctly while latest refs and capture evidence survive; active-mode tool descriptions no longer demand per-action plan updates or legacy overwrite repairs. Content-free request profiling was added. The same live task passed both independent replays and both negative controls, but total reported tokens rose from 126,144 to 128,127 and authoring time from 163.3s to 209.2s. Planning calls fell, recovery overhead remained; no efficiency improvement is claimed. Details and both retained attempts: `QAMATE_MULTI_APP_AUTHORING.md`.

- Real-planner multi-app authoring follow-up: MiMo authored a six-step correlated workflow through Jev on two isolated local apps. Two independent fresh-ID replays passed; missing propagation and wrong-record controls failed. Reported usage was 123,929 planner tokens plus 2,215 decision tokens; 11 of 27 tool calls were plan updates, so efficiency is still unresolved. See `QAMATE_MULTI_APP_AUTHORING.md` for the retained contract, friction and evidence. This is one simple local task, not complex-app reliability.

- Multi-app Jev follow-up: bounded goals now reuse the replaceable decision profile with frozen app/actor refs, correlation evidence, input-approval rechecks and explicit retry/fallback limits. Full gate: **371 tests**, both selftests and renderer compilation passed. Live Jev selected both actions on a role-separated local fixture; its exported workflow passed two independent replays and failed with propagation disabled. Two earlier confidence handoffs and the successful run's asynchronous no-progress handoff are retained in `QAMATE_MULTI_APP_JEV.md`. This is a scripted local decision smoke, not autonomous authoring or proof of broad speed/accuracy gains.

- Live multi-app recording follow-up: experimental catalog/observe/action/export tools now use isolated app/actor contexts, fresh DOM-grounded refs and the same typed execution path as replay. Settings opt-in is OFF by default; legacy browser tools are suppressed while recording. Stale targets, sensitive controls and uncertain mutation export fail closed. A live-recorded local workflow passed two independent replays; disabled propagation failed. Full gate: **358 tests**, both selftests and renderer compilation passed. This multi-app path is not yet Jev-routed or benchmarked with a live planner; no broad accuracy/speed claim.

- 2026-09-21: opt-in typed multi-app replay with isolated app/actor contexts, captured-record correlation and propagation assertions. Project settings preserve stable IDs and declared actors; project-bound export produces runnable tests with checkpoint/JUnit verdicts and skips shared login-state loading. Two independent exported-test replays passed; disabled propagation failed. Full gate: 352 tests, both selftests and renderer compilation passed; 12 focused tests passed after the final actor-validation guard. Live AI recording integration, per-actor auth setup and multi-app artifacts remain incomplete. See `QAMATE_MULTI_APP_FOUNDATION.md`.

- Shared profile resolution for the agent, extraction helpers, decision model and optional vision role. Profile names are arbitrary; MiMo is a backward-compatible test configuration, not the architecture.
- API adapters: OpenAI-compatible Chat Completions, OpenAI Responses, native Anthropic Messages, native Gemini, local Ollama, and TypeSafe's typed-choice endpoint for decisions. Provider presets (base URLs, key env names, temperature/token quirks, reasoning send-back) live in `engine/provider_catalog.json`; the offline mock provider was removed (tests use pydantic-ai `TestModel`). Unknown protocols and explicitly unsupported capabilities fail clearly. Native planner SDK automatic retries are disabled; HTTP requests have bounded timeouts.
- Settings allow adding named profiles and editing protocol, model, endpoint, key-variable name, output cap and temperature/token-parameter compatibility. Existing encrypted key storage remains in use. Extraction, decision and vision roles can select different profiles. TypeSafe profiles cannot be selected as a conversational planner.
- Opt-in `execute_goal`: at most eight actions from a planner-supplied candidate set, current DOM refs and supplied input values. No generated selectors or invented input values. Uses the existing click/fill/select wrappers, GUIDED/AUTO provenance and recorder. It stops on changed documents, stale candidates, uncertainty, action failure, no progress or budget exhaustion. Only the decision step uses Jev; browser execution remains Playwright.
- DOM-node identity checks before actions; native option label-to-value mapping; correct `data-test`, `data-test-id` and `data-cy` locators; native options in observations; no autocomplete polling after ordinary grounded text-field fills.
- Distinct test-ID refs and bounded row/product context prevent observation compaction from merging different products' identically named buttons. Grounded `element_has_value` checkpoints verify and replay exact input/select values.
- Generated tests preserve recorded Python selector string semantics instead of adding raw-string prefixes that corrupt escaped CSS attribute quotes. A local integration test executes the complete generated test on a fresh page.
- Repeated-action circuit breaker in the regular browser path, not only hybrid mode. Two equivalent failures/no-progress attempts at the same sampled state exhaust that action's retry budget. The sampled state is bounded, not a full semantic app-state graph.
- Terminal model exceptions persist partial history/usage and finalize the turn immediately. The benchmark distinguishes `model_error` from timeout.
- Unique verification artifact directories. Both agent and independent verification require matching, non-skipped JUnit test results; pytest exit code zero alone is insufficient. Synthetic eval subprocesses now use an explicit working directory.
- Missing provider usage stays incomplete/unknown instead of treating input-history characters as output tokens. Decision requests emit separate model/profile/role usage and latency events. Do not treat the primary-model UI counter as the total cost of all roles.
- Extra localStorage restoration is guarded by exact origin, including scheme and port, rather than hostname alone.

## Configuration

In Settings → AI / LLM, click **Add provider** and pick a preset from `engine/provider_catalog.json` (planner: MiMo, Claude, OpenAI, Gemini, OpenRouter, DeepSeek, Ollama, Custom). Paste the key, choose a model and use **Save key & test**; the first planner added becomes the default. No browser code needs to change when switching a model or endpoint using a supported protocol.

For Jev, add **TypeSafe Jev** (`typesafe`, `jev-latest`, `https://api.typesafe.ai/v1`, `TYPESAFE_API_KEY`) or **Jev via OpenRouter** (`openrouter_decisions`, `typesafe/jev-1.13`, `https://openrouter.ai/api/alpha`, `OPENROUTER_API_KEY`) from the *Decision models* group. Adding one binds the **decision** role if it is unset; **Test connection** makes one tiny two-choice decision because these APIs have no model listing. Then enable the experimental controller, save settings, and restart the agent. Choose the actual model ID offered by your account rather than assuming an alias is available.

Equivalent illustrative configuration (merge into configuration; do not replace unrelated settings):

```json
{
  "llm": {
    "default_provider": "my-planner",
    "roles": {"decision": "my-decider"},
    "providers": {
      "my-planner": {
        "protocol": "openai",
        "model": "YOUR_MODEL_ID",
        "base_url": "YOUR_PROVIDER_API_BASE_URL",
        "api_key_env": "ATS_PRIMARY_KEY",
        "token_parameter": "max_completion_tokens",
        "supports_temperature": false,
        "capabilities": {"tools": true, "vision": false},
        "timeout": 60
      },
      "my-decider": {
        "protocol": "typesafe",
        "model": "jev-latest",
        "base_url": "https://api.typesafe.ai/v1",
        "api_key_env": "TYPESAFE_API_KEY",
        "timeout": 30
      }
    }
  },
  "agent_execution": {"hybrid_enabled": true}
}
```

The TypeSafe adapter follows the [official HTTP contract](https://docs.typesafe.ai/api) using the repository's existing HTTP transport instead of adding another SDK. It consumes provider confidence only; generic LLM adapters do not invent comparable probabilities. The default 0.8 handoff threshold is provisional, not calibrated accuracy evidence.

## Evidence and reproducible checks

Local gate: **256 tests passed** (247 offline plus nine local-Chrome integration tests), both engine self-tests passed, both inline JSX blocks and the shared agent UI compiled, and Electron main-process syntax passed. Live-model success is tracked separately below.

```powershell
.\venv\Scripts\python.exe -m pytest engine\tests -q
.\venv\Scripts\python.exe -m pytest engine\integration_tests -q
.\venv\Scripts\python.exe engine\agent_chat.py --selftest
.\venv\Scripts\python.exe engine\agent_eval.py --selftest
node scripts/check-agent-ui.cjs
node --check main.js
```

The local Chrome integration suite uses an intercepted fixture and makes no external model calls. It covers native-select record/replay, exact-value checkpoints, distinct same-label product actions, stale-node rejection, plain-input polling, invalid options, no-op retry limits and the registered goal tool. The goal-tool test proves that a GUIDED input correction reaches the recorder and replays on a fresh page. It is not a live model-quality benchmark or independent replay of a model-authored test.

The explicit public smoke command spends model tokens and creates an isolated run root without altering existing projects or tests:

```powershell
.\venv\Scripts\python.exe engine\pilot_saucedemo.py --provider YOUR_PROFILE --timeout 180
# After configuring a decision key:
.\venv\Scripts\python.exe engine\pilot_saucedemo.py --provider YOUR_PROFILE --decision YOUR_DECISION_PROFILE --timeout 180
```

The 2026-09-19 MiMo smoke returned HTTP 401 (invalid API key) after **8.8 seconds**, with **one request, zero browser tool calls and zero generated tests**. Evidence: `results/_public_pilot/20260919-182130-92a596c6/`. No automatic retry was attempted. That run exposed the legacy output-token-estimation bug; its recorded 7,047 output-token estimate is **not provider-reported usage or billing evidence**. The reporting fix was applied afterwards and covered offline.

After subscription renewal, MiMo worked. The first retry (`20260919-230809-4a975e2f`) exhausted a 24-tool budget without delivering a test and exposed merged product-button observations. The next run (`20260919-231825-33322019`) delivered a test but failed replay because the generator corrupted quoted CSS selectors. Both engine issues were fixed and regression-tested; the failed runs remain as evidence.

Final MiMo smoke: **independently reliable for this one task**, using `mimo-v2.5-pro`, with hybrid execution OFF. Evidence: `results/_public_pilot/20260919-232253-c30c9e7c/scorecard.json` and its generated `tests/flows/bench_public_pilot/test_bench_public_pilot.py`.

- Task: public demo login, select low-to-high sort, add Sauce Labs Onesie, open cart and verify its presence. No checkout or purchase.
- One generated test; two agent verification passes and **two independent passes**, with matching non-skipped JUnit results.
- 18 model requests, 30 tool calls, zero user questions, zero assumed values. Agent turn completed at 92.91 seconds; the harness records 123.3 seconds including process finalization, excluding the subsequent independent replay stage.
- Provider-reported usage: **654,832 input + 4,624 output = 659,456 tokens**. This is not a billing/cost calculation; cache pricing was not measured. Token usage is still high and no efficiency improvement is claimed.
- The sort assertion verifies the selected control value `lohi`, not numeric ordering of every product. This single public smoke is not a complex-app or multi-app accuracy benchmark.

At that point no TypeSafe key was available. On 2026-09-20, the user supplied an OpenRouter credential and the dedicated OpenRouter Decisions adapter was implemented and tested. A single synthetic next-action request to `typesafe/jev-1.13` selected `cart` rather than `checkout` or `stop`, with provider confidence 1.0, 391 input tokens, 38 output tokens, reported cost $0.000016422 and 1,125 ms client wall time. This confirms live access and response parsing, not calibrated accuracy or browser-controller performance. The credential was process-local for that check and was not saved in source/configuration files.

OpenRouter profile settings: protocol `openrouter_decisions`, model `typesafe/jev-1.13`, base URL `https://openrouter.ai/api/alpha`, key variable `OPENROUTER_API_KEY`. The adapter appends `/decisions`, not `/chat/completions`. Bind this profile only to the decision role and supply the key through the existing encrypted Settings key field. [Official OpenRouter contract](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-questions-and-answers-request). Post-change verification: 249 offline tests and UI compilation checks passed.

The subsequent [live browser comparison](QAMATE_JEV_BENCHMARK_2026-09-20.md) demonstrated real Jev-controlled login and a generated test passing two independent replays when controller use was explicitly requested. It did **not** demonstrate an efficiency win: 330,835 combined reported tokens versus 325,365 in the corrected MiMo-only run, and 17 planner requests in both. Ordinary opt-in routing was unreliable. Failed and bypassed attempts are retained in that report. Hybrid mode remains opt-in; no live project default was changed. Broad accuracy, speed and token-saving targets remain unproven.

## Remaining rollout work

Latest result (21 September): [validation and obstruction context](QAMATE_VALIDATION_CONTEXT.md) now reaches both the planner and decision model. The negative-login task finally delivered a test passing **2/2 independent replays**: five Jev decisions, no controller handoff/fallback, 106,493 combined tokens and 146.83 seconds including replay. Both negative assertions are recorded at their correct earlier steps. Two redundant late checkpoint attempts were correctly rejected, so this is not a zero-error trace. **315 tests and both self-tests passed.** This single successful run supersedes the task's latest failure status, not its retained failed history or broader reliability limitations.

Recording-recovery slice: [session-local undo and protected clear](QAMATE_RECORDING_RECOVERY.md) now preserve the latest five nonempty recordings, including assertions, input counters, assumptions and skips. Unexplained clearing of checkpointed work is blocked; explicit restarts archive it, and `restore_recording` preserves displaced work without rewinding the browser or claiming a pass. **310 distinct regression checks passed** (309-test gate plus the added fresh-document restore/replay test). The new bounded negative-login attempt still timed out without a test; it made no clear/restore calls and instead stalled around inline-error dismissal. Negative-login reliability remains open, and snapshots do not yet survive process exit.

Latest reliability follow-up: negative-login authoring timed out both before and after the bounded-handoff fix. The retry explicitly fell back to regular execution, reached all four requested checkpoint outcomes, but discarded its recording and restarted too late to deliver. This remains an open failure, not a verified test. The new guard caps unchanged-state decision handoffs and reports fallback separately; redacted field population hints expose no input values. Final regression gate: **303 tests passed** (293 offline, ten browser), plus both self-tests. Next authoring priority is recoverable recordings and avoiding unnecessary re-recording; broad exploration and multi-app work remain incomplete. Details and both failed-run artifacts are in [the benchmark report](QAMATE_PUBLIC_BENCHMARKS.md#negative-login-failure-and-bounded-recovery-fix).

Broader task gate: [public benchmark suite](QAMATE_PUBLIC_BENCHMARKS.md) now includes fixed smoke, cart-edit and negative-login contracts, optional repetitions and alternating regular/hybrid order. A harder cart-edit pair delivered a Jev-authored test passing **2/2 independent replays** within the same 48-tool cap where the regular run delivered no test. Combined usage was 151,587 versus 487,891 tokens; this failed-versus-completed pair is not a speed comparison. Negative assertions now work through both agent paths, and checkpoint probe errors fail closed. Local gate: **287 offline + 10 browser tests passed**, plus both self-tests. See the report for remaining requirement-coverage and broader-app limitations.

Follow-up overhead fixes (20 September): checkpoint arguments accept one JSON encoding layer and then undergo the same typed validation (ten-item cap, supported assertion types, required values/refs, no extra fields). Hybrid plan-tool descriptions no longer demand standalone updates; `execute_goal(plan_step=N)` publishes active/failed status and marks done only when its supplied outcome checkpoints pass. Generated-test replay remains separately required. Tool-result events now carry untruncated outcome metadata, and pilot metrics count validation-rejected calls. Reprocessing the previous trace correctly counts three such calls without changing historical artifacts. Regression gate after these fixes: **290 tests passed**, with both self-tests passing. Live follow-up `20260920-131012-a5836c01` passed two independent replays: **9 planner requests, 71,344 combined tokens, 132 seconds end-to-end**, six Jev decisions and zero rejected calls. All four bundled assertions were recorded and replayed. See the [follow-up comparison](QAMATE_JEV_ROUTING_2026-09-20.md#follow-up-checkpoint-normalization-and-planner-overhead); single-task improvements are not a general latency or accuracy claim.

Latest completed slice: [harness-owned routing and compaction](QAMATE_JEV_ROUTING_2026-09-20.md). The normal hybrid path now exercised six live Jev decisions without a benchmark-only routing instruction and produced a test passing two independent replays. Combined reported tokens were 159,252 versus the earlier corrected baseline's 325,365 (51.1% lower in this single comparison). Planner requests increased from 17 to 19, and ready-to-completion latency did not improve. Three malformed checkpoint arguments were rejected before execution; the report records this and the scorecard's validation-error counting limitation. Final local gate: **268 tests passed and both self-tests passed**. Batched goal checkpoints are locally verified; this live planner used standalone checkpoints. These findings do not establish broad speed/accuracy improvements or multi-app readiness.

- Live A/B/C runs with valid credentials; confidence/candidate-recall calibration and provider capability smoke tests against real endpoints.
- Project-level profile overrides, richer normalized provider errors, complete all-role cost accounting, and connection-test UI.
- Automatic goal-conditioned candidate construction, stronger entity modeling beyond bounded row/product context, richer assertions and outcome verification inside the controller. This slice requires planner-supplied actions and hands back on navigation; it is not full autonomous multi-page exploration.
- Condition-based waiting throughout the engine, compact state deltas, form/constraint modeling, exploration coverage graphs and discovery-to-test synthesis improvements.
- Stable app/actor identities, isolated multi-app session registry, cross-app correlation, propagation waits and multi-page generated tests. Exact-origin restoration is a prerequisite fix, not full multi-app support.

Roll back experimental behavior by disabling the controller in Settings. The default regular agent path remains available; reliability and verification fixes apply to both paths.
