# Qamate project memory

Portable project continuity, consolidated on **2026-10-04** from Qamate-specific
project notes and the committed acceptance ledger. This is not a new live test
run. Latest published validation checkpoint: **2026-10-01**, runtime source
`daa1756c791668a55a58dc519c210ade4ab3863d`.

## Read first

1. [Acceptance ledger](docs/QAMATE_PROGRAMME_CHECKLIST.md): authoritative dated
   results and completion criteria. Read newest entries first.
2. [Benchmark failure review](docs/QAMATE_BENCHMARK_FAILURE_REVIEW_2026_09_29.md):
   failure classes and limits of earlier benchmark conclusions.
3. [Decision-owned browser](docs/QAMATE_DECISION_OWNED_BROWSER.md): Jev decision
   ownership and the typed browsing contract.
4. [Multi-app authoring](docs/QAMATE_MULTI_APP_AUTHORING.md) and
   [multi-app Jev](docs/QAMATE_MULTI_APP_JEV.md): cross-app execution and evidence.
5. [Recording recovery](docs/QAMATE_RECORDING_RECOVERY.md),
   [MiMo profile](docs/QAMATE_MIMO_26_FLASH.md), and
   [validation context](docs/QAMATE_VALIDATION_CONTEXT.md).

Older plans and implementation reports describe their own checkpoints. Where
they conflict with later results, preserve the history but use the newer ledger.

## Goal and working agreement

Improve the existing system rather than replace it: reliable exploration and
automatic test authoring on unfamiliar complex websites and interacting apps,
with independently replayed tests and lower end-to-end time and token cost.

- Jev must own browser action decisions, not merely approve planner-picked clicks.
- MiMo 2.6 Flash was chosen for testing. Neither planner nor decision provider may
  become an architectural dependency; model/API replacement must remain easy.
- Pursue substantive end-to-end acceptance, not isolated tiny fixes followed by
  completion claims. Verify processes before saying background work is running.
- Preserve unrelated dirty-worktree changes. Commits, pushes, and deployments
  require user authorization; authorization for a push does not authorize deploys.
- Never publish credentials, private sessions, customer data, or unrelated memory.

## Current published status: goal NOT achieved

These are recorded results, not measurements rerun when this memory was written.

| Checkpoint | Result | Meaning |
| --- | --- | --- |
| October 1 pre-push regression gate | 590 passed; 26 existing PydanticAI deprecation warnings; 253.06 seconds | Engine unit/native tests and two evaluation audits; not the live acceptance gate |
| Normal-planner frozen cohort `20260930-180753-1cbcc4b6` | 30/30 attempts, 8 reliable (26.7%), source stable | Acceptance failed |
| Non-thinking experiment `20260930-174453-0f20c025` | 12/30 attempted, zero reliable; stopped | Rejected experiment, not the saved default |
| Integrated cohort `20260930-174626-6ec0db46` | Six fresh authors: request 2/3 reliable; return 3/3 reliable | Combined multi-app acceptance failed |
| Integrated return replay | Six independent green replays with JUnit, correlation, and duplicate-submit checks | Evidence for this flow/source only |
| UI publication checks | Both inline Babel blocks and shared agent UI/provider checks passed | Synthetic IPC render evidence is not real Electron/backend validation |

Target speed/token reduction and unfamiliar-site generalization remain unproven.
Green unit tests, older successful pilots, and an agent's own self-verification do
not supersede the full frozen cohort's failed result.

## Architecture and ownership

- [agent_chat.py](engine/agent_chat.py) coordinates the planner, NDJSON session,
  contract compilation, test export, and verification.
- [planner_policy.py](engine/planner_policy.py) governs planner tool exposure.
  Decision-owned mode must not silently fall back to legacy planner browsing;
  exhausted/failed contracts close execution tools so the planner can report.
- [decision_browser.py](engine/decision_browser.py) models typed outcomes,
  approved input slots, milestones, and candidates grounded in live observations.
  Jev selects actions; the harness owns execution mechanics and verification.
- [decision_browser_tools.py](engine/decision_browser_tools.py) bridges native
  browser actions, identity/capability checks, recording, and safety restrictions.
- [contract_review.py](engine/contract_review.py) optionally reviews the original
  requirement before browsing. Semantic review is a guard, not coverage proof.
- [decision.py](engine/decision.py), [model_profiles.py](engine/model_profiles.py),
  and [llm.py](engine/llm.py) contain decision/provider/profile integration.
- [decision_workflow.py](engine/decision_workflow.py) and
  [decision_workflow_tools.py](engine/decision_workflow_tools.py) support the
  separate typed multi-app path, isolated app/actor contexts, captured generated
  identifiers, and propagation checks.
- [recorder_parser.py](engine/recorder_parser.py) must preserve the actual live
  interaction in generated code; a healed live action does not prove replay works.
- [agent_bench.py](engine/agent_bench.py), [agent_eval.py](engine/agent_eval.py),
  [frozen_benchmark.py](engine/frozen_benchmark.py), and
  [public_outcome_audit.py](engine/public_outcome_audit.py) support authoring,
  independent replay, frozen cohorts, and coverage assessment.
- Active renderer JSX is in [src/index.html](src/index.html), with **two** inline
  Babel blocks. [src/agent_ui.js](src/agent_ui.js) is shared between docked and
  separate agent windows. `src/app.js` is a legacy backup, not the active renderer.

### Fast engine (default since 2026-10-04)

A second engine replaces the classic modes for chat tasks (Engine: Fast / Classic in the agent
panel); see [the redesign](docs/QAMATE_FAST_EXPLORER_REDESIGN.md) and its progress log.
`qm_runtime` (one step runtime for live run and generated test), `qm_observe` (Playwright AI
snapshot with positions, completed from the page), `qm_selectors`, `qm_ground` ("exact or ask"
grounding), `qm_decide` (chooser asked twice in opposite orders; agreement required),
`qm_explorer`, `qm_planner` (streamed plans), `qm_map` (per-project app memory and quick scan),
`qm_agent` (bounded loop; a test is saved only after two fresh-context replays pass),
`qm_bench` (ten frozen-gate workflows, `--model` to compare planners), `qm_chooser_eval` (chooser
models on fixed cases) — the last two are in the Benchmark tab.

Before and after any change to grounding, waits or the step runtime, run the model-free probes in
[engine/probes](engine/probes/README.md); that README holds the dated results and the first-attempt
record on apps the engine had never seen (14 so far; a new kind of app still needs 0–3 engine
fixes — do not claim general reliability). Never accept a fuzzy name match without asking. As of
2026-10-05 all of this is development evidence (hand-written plans, fixtures, fake models):
the live benchmark and both model comparisons have not been run with real models.

## Safety and truth semantics to retain

- Single-app `browse_goal` is read-only by default. Extended form interaction
  requires explicitly authorized disposable/local URLs and blocks HTTP writes.
  Explicit public-demo login support is not a general private enterprise login
  controller. Check the applicable path's policy before authorizing actions.
- Multi-app evidence comes from disposable authenticated apps on separate origins
  with a shared backend, not arbitrary production enterprise systems.
- Never blindly retry an uncertain dispatched write. Bound equivalent failures;
  terminal provider errors end that task. Authentication, quota, infrastructure,
  and unknown terminal failures stop the cohort. Task-local output-cap/tool
  validation failures keep their failed denominator while unrelated predeclared
  benchmark tasks can continue.
- Non-mutating checks, waits, and readback bindings differ from clicks/inputs.
  Do not weaken mutation confidence thresholds to make a run pass.
- Native type checks, readback truth gates, mandatory assertions, and independent
  replay remain essential. Never deliver an unverified test as verified.
- Required navigation/roundtrips need observed intermediate route evidence.
  Shared text across pages does not prove navigation; query-only changes do not
  count as route movement, and same-URL modals need more than URL evidence.
- `url_matches_start` means exact URL, not same origin. `url_matches_milestone`
  refers backward to an observed committed route, not a guessed destination.
- Null/omitted input readback derives from the approved input slot. Explicit
  empty string means an empty-field assertion. Contradictory simultaneous field
  values or present/absent text requirements must fail before browsing.
- Optional `decision_context_format="columns"` is lossless observation packing.
  Character savings are not measured total-token, cost, or time savings.
- Optional `decision_contract_review=true` checks original intent, allows at most
  three rejected compilations, and stops on uncertainty. Exact-entry route claims
  receive separate review; never replace them with weaker assertions silently.

## Lessons from failures

Use generic harness fixes, not hardcoded benchmark/site rules:

- Carry native-select identity and the real option value through live action,
  recording, and replay. Ground locators in current observations.
- Record the fallback that actually succeeded; preserve uniqueness, ordinals,
  exact text matching, and the identity of repeated controls.
- Preserve repeated reviewed clicks: pagination once lost three Next clicks
  because the recorder collapsed them into one.
- Select existing numeric input before sequential replacement: live `0 -> 7`
  once replayed as `07`. Browser regressions must execute generated code.
- Commit milestone checks atomically before leaving a form. Do not assert on
  transient dropdown/form content after it has disappeared.
- A page with zero interactive elements can still be a valid static page.
- Separate bad contracts, execution failures, recorder/replay asymmetry, genuine
  application defects, and provider failures. Keep the genuine CRM Size display
  defect visible rather than deleting its assertion to get a green test.
- Set PydanticAI request limits explicitly. Output-token caps and context limits
  are distinct; raising a tool budget alone does not fix a model request cap.
- Keep generated IDs and app/actor authentication scopes isolated. Correlate
  created records across apps; do not substitute hardcoded IDs.

## Historical context, not current acceptance

The August review identified `apps[0]`-centric execution, shared-context risks,
weak select grounding, and repeated model-cap failures. Those observations
motivated later work; do not assert they all remain current without inspecting
the newer implementation.

The September 29 frozen cohort `20260929-021759-26552383` completed 30 attempts
with 11 reliable (36.7%) and failed acceptance. Later full results are recorded
in the ledger; do not cherry-pick the highest historical percentage.

Earlier CRM evidence had three authors and six independent replays on matching
source, plus detection of three simulated rendered-readback faults. That is not
proof of backend corruption detection. Earlier request and return successes used
different source versions and cannot be combined into final-source acceptance.

Allocation and sort/detail development pilots passed coverage and independent
replay, but were not comparative speed tests or unseen generalization proof.
The sort/detail task's `ring-spun` versus actual `ringspun` typo was corrected
after inspection; retain the failed first attempt and label the corrected task.

The first public poster-admin probe `20260930-174014-8f2577b6` was not delivered:
login was required and contract compilation failed. It is now a consumed site,
not an unseen success. Mock-login retries are development retries; sharing
React-admin with Atomic CRM also limits framework diversity.

## Remaining acceptance work

1. Reach **at least 90% independently reliable** on the frozen three-app,
   ten-workflow, three-author gate. Preserve all 30 planned attempts and failures.
2. Establish genuine unseen-site/workflow evidence separately. Freeze the engine,
   use ordinary objectives without hand-supplied selectors/exhaustive assertions,
   and independently assess requirement coverage and defect detection. Inspected
   or tuned holdouts are no longer unseen. Separate first attempts from retries.
3. Pass both integrated workflows with three fresh authors and six independent
   replays each on the same final source, retaining correlation/no-duplicate checks.
4. Show matched **at least 50% lower median end-to-end time and total tokens**
   with verified success preserved. Count planner plus decision usage and costs;
   exclude contaminated sleep/timeouts from comparisons, not failed tasks from
   the reliability denominator. Observation characters are not a substitute.
5. Exercise an alternative live provider beyond adapter unit tests, using only
   authorized credentials. Prove model replacement rather than claiming it.
6. Validate actual Electron/backend behavior, beyond mocked IPC visual checks.

## Continuing from a fresh checkout

- Run from the `agrim-ats` repository and detect the OS. Windows uses this repo's
  `venv\Scripts\python.exe`; Linux uses `venv/bin/python`. A parent legacy venv
  is not necessarily the Electron engine environment.
- Windows offline gate: `run_engine_tests.bat`. Direct checks include
  `python engine/agent_chat.py --selftest`,
  `python engine/agent_eval.py --selftest`, and `python -m pytest engine/tests`.
  Use the selected venv executable and inspect the current gate script for scope.
- Engine unit tests do not establish live acceptance. Run appropriate native
  integration/evaluation audits for changed behavior and record source/task
  hashes, independent JUnit evidence, model settings, and complete usage.
- Changes to inline renderer code require checking both Babel blocks. Verify
  the shared agent UI and provider configuration paths when those change.
- Local `results/...` identifiers above are historical evidence pointers. Raw
  artifacts may be absent in a fresh clone; committed summaries do not recreate
  them. State when verification relies on a recorded result rather than rerunning.
- This repository `MEMORY.md` is project/development continuity. Runtime
  per-project `AGENT_MEMORY.md`, private `projects/` content, authentication state,
  transcripts, settings, and raw sessions are separate user data. Do not commit
  those wholesale to make a clone appear complete.

Update this document and the dated ledger when verified evidence changes. Keep
historical failures and boundaries; never turn a development checkpoint into a
claim that the overall goal has been achieved.
