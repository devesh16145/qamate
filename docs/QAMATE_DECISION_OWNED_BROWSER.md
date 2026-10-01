# Decision-owned browsing — 2026-09-25

## September 30 opt-in controls and current limits

The acceptance ledger (`QAMATE_PROGRAMME_CHECKLIST.md`) is authoritative for
current results; historical successes below are not a final release gate.

`agent_execution.decision_context_format = "columns"` uses lossless column
encoding, retaining missing/null distinctions and every observed target. It
does not filter candidates or truncate page evidence. `"objects"` remains the
default. Character-size telemetry is not token billing.

`agent_execution.decision_contract_review = true` asks the configured decision
profile to review the original request before browser work. Exact registered-
entry URL claims receive a focused review. At most three rejected compilations
are permitted; uncertainty or provider errors stop. A review acceptance is
not proof of semantic correctness. Once a dispatched contract fails, tools are
closed for the final report; asking the user cannot reset its one-run budget.
Both settings are exposed as experimental single-app controls in Settings.
They do not alter the separate cross-app executor.

Milestones can require `transition: "navigate"` or `"roundtrip"`. Recorded
intermediate URLs become replay assertions; matching shared text alone is not
evidence of a visit. Query-only changes do not count. This is conservative for
applications whose entire routing is query-based; same-URL modal transitions
also require separate evidence and are not claimed as URL movement.

Read-only field binding is non-mutating and still requires a deterministic
readback. Click/input mutation confidence policies have not been relaxed.

Provider-specific SDK settings remain configuration, not engine model-name
branches. Isolated pilots support JSON or `@file` overrides, for example:

```powershell
.\venv\Scripts\python.exe engine/pilot_saucedemo.py --provider mimo --task crm-search --decision-openrouter --decision-loop --no-contract-hints --compact-decisions --review-contract --planner-model-settings '@scripts/evaluation_profiles/mimo-nonthinking.json'
```

This example tests a documented MiMo option; it is **not a recommendation or
default**. The September 30 non-thinking cohort regressed in correctness.
Saved profiles remain unchanged. Other providers must use their own supported
settings; no universal thinking parameter is assumed. Alternative live
provider portability is not yet validated merely by adapter unit tests.

## Architectural change

The earlier `execute_goal` accepted planner-selected `(kind, ref, value)` actions. In the retained CRM run, all three Jev calls were effectively one action versus stop. That is not a decision-owned browsing loop.

The new experimental `agent_execution.decision_loop_enabled` mode instead exposes `browse_goal` to the generative model. It accepts a typed outcome contract: registered start URL, overall goal, named approved inputs, and ordered outcome milestones. No action refs, selectors or click sequence are accepted. The compiler cannot use the old browsing tools in this mode. Export/replay tools are exposed only after every milestone succeeds.

`decision_browser.py` builds candidate actions from each fresh observation. The configured decision provider chooses actions, input-to-field bindings, waits and milestone checks. Page changes no longer automatically hand control back to the planner. Initial navigation to the registered start URL is deterministic setup. The generative model remains responsible for bounded requirement compilation and final test export/reporting, not individual browser decisions.

`decision_browser_tools.py` bridges the loop to Qamate's existing native session, recorder and value-provenance gate. Target identity and capability are checked again after input approval. Successful live milestones are recorded atomically; failed milestones leave no partial assertion batch. A live success is explicitly **not** independent verification.

## Scope and limits

- Opt-in **read-only, single-app** policy: same-origin links, search/filter inputs and a narrow set of read controls. Business writes, authentication, uploads and multi-app switching are not implemented in this mode. Existing multi-app/legacy code remains intact but is not a hidden fallback.
- Non-read HTTP methods and cross-origin main-frame navigation are blocked while the loop runs. This is not a general browser security sandbox or a guarantee about arbitrary sites' GET semantics. Only use authorized testing targets.
- Maximum 32 decision requests and a 120-second loop budget. Decision timeouts terminate the loop; synchronous browser operations retain their existing bounded timeouts and can finish after the loop deadline. Two equivalent choices in an unchanged semantic state exhaust that choice. An uncertain action ends the loop without automatic replay.
- One contract attempt per agent session prevents retrying uncertain writes or weakening expectations mid-run. New tasks in this experimental mode currently require a new agent session.
- A minimum-confidence threshold of 0.8 is inherited for clicks/fills, not claimed as calibrated across providers. Checks and waits do not require it: their deterministic result cannot assert success when an outcome is false. Readiness probes do not commit assertions. Every completed milestone also records its actual route as replay context.
- No image/video input is claimed. TypeSafe's own launch article states its game demo used structured state/text, not images: https://typesafe.ai/blog/introducing-system-one-models-and-jev . Visual API support requires separate validation.

## Outcome checks

In addition to existing URL/text/value checks, the loop records actual positive collection evidence rather than a list heading. It supports empty input values. `observed_empty_results` captures a single visible, recognized empty-results message as **observed behavior**, not a specified business rule. Ambiguous or unsupported empty messages fail closed. `record_links_present` derives a record-route prefix from the current page and requires a visible matching record link, excluding create/new/import links. These are bounded recognizers, not universal list detection.

The CRM source-coverage gate now requires the exact search value, empty-results text and Companies URL before clearing, then empty input, Companies URL and positive collection evidence afterward. Removing any required action/outcome or replacing collection evidence with absence of the empty message fails the audit.

## Run

```
venv\Scripts\python engine\pilot_saucedemo.py --provider mimo --decision-openrouter --decision-loop --task crm-search --timeout 300 --tool-budget 24
```

The CLI uses isolated configuration and test artifacts; it does not enable the mode in saved app settings. Planner and decision profiles remain replaceable through supported adapters. The task prompt is unchanged; the architecture/tool contract differs, so this is a functional diagnostic, not a matched performance comparison.

## Retained live evidence

- `results/_public_pilot/20260925-224037-ee5fa07b`: first compiler attempt, before discovery outcome types were added. MiMo made one `get_settings` call, then exhausted its 8,192-token output cap before emitting a contract. Terminal `model_error`, no Jev request, no generated test, no replay. Partial usage is not a valid total-cost comparison. The run was not retried unchanged; discovery outcome types and clearer compiler instructions remove the need to invent page evidence before browsing.
- `results/_public_pilot/20260925-224822-8439d40b`: contract compilation succeeded after two rejected extra-field arguments. Jev selected Companies from 47 choices, selected the search field and verified two milestones, then handed off. Five decision requests, no export/replay. The first trace did not preserve handoff confidence; later traces do. Outcome-readiness probes and removal of conflicting legacy injected instructions followed this attempt.
- `results/_public_pilot/20260925-225143-7fc9db20`: one compiler `browse_goal` call; Jev completed navigation, search and empty-state milestones, then clicked Clear search. It chose a final check at confidence 0.61 while collection readiness was false; the old all-operation threshold ended the loop. Seven decision calls; no export/replay. This motivated distinguishing deterministic checks/waits from browser mutations, without lowering click/fill confidence thresholds.
- `results/_public_pilot/20260925-225522-95d51bb2`: **complete task success** in this bounded diagnostic: exported, self-verified, two independent replay passes, and source outcome audit passed. Eight Jev decisions (click, check, fill, wait, check, click, wait, check) ran inside one `browse_goal`, with 10–65 candidates per step. No planner-supplied action refs, direct browser calls or fallback. MiMo made six requests and five tool calls total. Authoring 93.9 seconds; end-to-end with replays 116.56 seconds. Planner usage 29,837 tokens plus decision usage 45,054 = **74,891 combined tokens**, complete reporting. Decision inference total 3.814 seconds. Source includes the previously missing empty-input and positive record-link assertions. The generated preconditions prose contains a URL typo; executable navigation is correct. This retained metadata flaw is not edited out of the artifact.

### Frozen-runtime repeatability cohort

All three scorecards have identical runtime manifests and task-contract hashes. Each run independently compiled and authored the task; each passed the source outcome audit and two fresh-browser replays. Profiles: `mimo-v2.6-flash` compiler and `typesafe/jev-1.13` decision provider. No legacy browser fallback occurred.

| Artifact directory under `results/_public_pilot/` | Authoring seconds | Including replays | Combined tokens | Compiler requests | Jev requests | Independent replay |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `20260925-225522-95d51bb2` | 93.9 | 116.56 | 74,891 | 6 | 8 | 2/2 |
| `20260926-212051-948c9c7d` | 92.1 | 112.72 | 71,924 | 6 | 9 | 2/2 |
| `20260926-212327-abc17424` | 147.5 | 168.59 | 67,424 | 5 | 8 | 2/2 |

Median authoring time is 93.9 seconds; median combined usage is 71,924 tokens. The third run demonstrates substantial latency variance despite lower token usage. Jev inference totals were 3.814, 4.361 and 3.781 seconds respectively; those are inference-only, not full browser-loop durations. Each run used one `browse_goal`; the compiler did not choose individual browser actions. These are three repetitions of one supported task family, not three different tasks or held-out accuracy evidence.

The earlier planner-led CRM attempt took 220.4 seconds and 172,058 tokens but lacked positive row coverage. Different code and task coverage, public randomized demo state, and an unpaired baseline mean this is **not a matched speed/efficiency claim**. All failed development attempts above remain part of the evidence. Real authenticated/multi-app readiness is not established by this read-only CRM result.

## Verification

The full unit/browser gate passed **490 tests** (183.78 seconds, 22 existing PydanticAI deprecation warnings), and both engine selftests passed. Subsequent discovery fixture checks passed 20 tests; readiness integration passed 49 focused tests; action-only confidence thresholds, route anchoring and coverage checks passed 61 focused tests. Final runtime exposure guards and subsequent live results are recorded below when complete.

Final runtime regression gate on September 26: **492 passed**, 23 existing PydanticAI deprecation warnings, 175.53 seconds. A later focused native-browser gate passed **7 tests**, including three cases that execute the generated collection assertion: populated rows pass, hidden rows fail, and create-only links fail. No production runtime files changed for those added tests. `git diff --check` passed. The fresh repeat beginning `20260926-212051-948c9c7d` has identical runtime manifest hashes to the successful September 25 run.
