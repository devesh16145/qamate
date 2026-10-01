# Qamate acceptance ledger

## October 1 publication checkpoint: goal not achieved

Pre-push validation: **590 passed**, 26 existing PydanticAI deprecation
warnings, 253.06 seconds (engine unit/native tests and both evaluation audits).
Both inline JSX blocks and shared agent UI/provider checks passed. The staged
source passed credential-pattern and whitespace checks; this does not replace
the live acceptance gate below.

The normal-planner frozen cohort `20260930-180753-1cbcc4b6` completed all
**30/30 attempts: 8 reliable (26.7%), source stable, acceptance failed**.
Do not substitute earlier CRM successes or green unit tests for this result.
The non-thinking experiment `20260930-174453-0f20c025` stopped after 12/30
attempts with zero reliable; it is not enabled as the saved default.

The source-consistent integrated cohort `20260930-174626-6ec0db46` completed
all six authors. Request flow: **2/3 reliable**. Return flow: **3/3 reliable**,
with six independent green replays and the cohort's JUnit/record-correlation/
duplicate-submit checks passing. Combined multi-app acceptance still fails.

Final implementation additions reject contradictory simultaneous input/text
requirements before browsing. Null/omitted input readback derives from the
approved slot; an explicit empty string remains an empty-field assertion.
Task-local output-cap/tool-validation failures retain their failed denominator
but no longer skip unrelated predeclared benchmark tasks. Authentication,
quota, infrastructure and unknown terminal failures still stop the cohort.

These changes are a development checkpoint, not a production-readiness or
unseen-site generalization claim. The target speed/token reduction remains
unproven. Local credentials, sessions and raw run artifacts are not published.

## September 30 continuation: measured progress, acceptance still incomplete

Frozen `20260930-151104-3d84a81a` ended after **9/30 planned attempts**:
five reliable, four unsuccessful, 21 unrun. Related-contact was **3/3 authors,
six independent green replays**; CRM search was 2/3. All three lifecycle
authors failed. One stopped on the known missing Size detail; another stopped
before committing a ready form checkpoint; the third hit MiMo's 16,384-output-
token cap during contract revision. This was not an authentication/quota error.
The incomplete cohort does not replace either historical 11/30 full cohort.

Changes and verified evidence from this continuation:

- Typed required navigation/roundtrip evidence records intermediate exact URLs;
  native negative controls reject shared text on the wrong page.
- Lossless column encoding retains every observed element/candidate. The CRM
  search development run `20260930-150546-f43fbece` passed coverage and 2/2
  independent replays. Page-evidence character reductions are not token/cost
  or end-to-end performance claims.
- Optional Jev preflight reviews the original requirement, not its paraphrase.
  Entry observation exposes already-open control metadata without field values
  or credentials. This is a guard, not proof of coverage.
- UI exposes experimental compact observations and contract review. Both Babel
  blocks and shared UI compiled; synthetic-IPC render tests passed.
- The previous-source full unit/native gate passed **579 tests**. Later changes
  require the newer full gate; focused counts overlap and must not be summed.
- Multi-app request development `20260930-151227-d8044e91` passed coverage,
  self-verification and two independent replays; not a three-author cohort.

Follow-up fixes preserve configured SDK model settings in isolated pilots,
allow an explicit JSON/@file override without editing saved configuration,
exclude non-mutating readback bindings from mutation confidence thresholds,
and close planner tools after an exhausted/failed contract. Click thresholds,
deterministic assertions, origin restrictions and one-run budget remain.
Exact-entry URL claims receive a separate semantic review; they are never
automatically replaced with weaker assertions.

Non-thinking MiMo is an experiment, NOT the new default. Official API docs:
https://mimo.mi.com/docs/en-US/usage-guide/passing-back-reasoning_content
The first non-thinking CRM attempt `20260930-173812-a37ea181` compiled quickly
but failed on an invented exact-entry assertion and spent additional calls on
denied retries. Retained as a failure. Speed cannot compensate for this loss
of reliability. Configuration fixture: `scripts/evaluation_profiles/mimo-nonthinking.json`.

First-site probe `20260930-174014-8f2577b6` on the public poster-shop admin
was **not delivered**: entry required login and contract compilation failed.
This attempt is consumed, not an unseen-site success. Official demo auth source
subsequently confirmed arbitrary synthetic login values are accepted locally:
https://github.com/marmelab/react-admin/blob/master/examples/demo/src/authProvider.ts
The version-2 retry explicitly authorizes only that mock login, retains all
first-attempt evidence, and must be labelled a development retry. Sharing the
React-admin framework with Atomic CRM also limits generalization claims.

Current evaluation artifacts (read their scorecards before reporting completion):
- Frozen non-thinking 30-attempt cohort: `results/_frozen_bench/20260930-174453-0f20c025`.
- Predeclared six-author integrated cohort: `results/_integrated_cohort/20260930-174626-6ec0db46`.
- Public demo login development retry: `results/_public_pilot/20260930-174759-35dc0250`.

## Latest checkpoint: September 29 evening

Evening continuation: added typed `url_matches_start` outcomes, derived from the
registered URL rather than guessed by the planner. Live recording uses exact URL
equality; generated replay retains the exact route/query/hash with a portable
base origin. Native tests reject a different hash route and replay on a second
origin. Focused controller/native gate: 47 passed; offline engine suite: 504
passed; both wiring selftests passed. These are overlapping gates, not 551 tests.

Fresh negative-login `20260929-213151-94664049` still failed: the compiler used
the new route outcome successfully but invented a preliminary body-text `Login`
assertion for an input-based control. No submission or export occurred. Added
generic compiler guidance against invented readiness milestones; all required
negative and positive assertions remain mandatory.

New frozen 30-attempt suite: `results/_frozen_bench/20260929-213404-6b040b8b`.
Completed September 30: **30 attempts, 11 reliable (36.7%), source stable,
acceptance failed**. This frozen source predates the later slot-value,
observed-route-reference and readiness fixes documented below. Future manifests explicitly label
the former holdouts consumed. `--no-contract-hints` disables benchmark-specific
compiler hints without disabling independent outcome audits; this alone does
not turn known workflows or detailed benchmark prompts into unseen-site evidence.
No-hints negative-login diagnostics:
- `20260929-213526-38a2ca53`: failed because an omitted expected field value
  defaulted to empty. Omitted readback now derives from the approved input slot;
  explicit empty still means clearing, with regression coverage for both.
- `20260929-213753-e12c0791`: Jev completed both negative submissions and logged
  in, but the contract required a password readback after navigation. Added
  explicit simultaneous-state milestone semantics, not relaxed assertions.
- `20260929-214057-95bbd47a`: coverage passed, self-verified, **2/2 independent
  replays**, no fallback, eight Jev calls. End-to-end 135.17 seconds; planner
  19,875 tokens plus decision 15,724 tokens. This is one development success,
  not unseen-site evidence or a matched 50% performance result.

Frozen CRM repeats 1 and 3 replayed green but failed independent coverage:
they asserted shared company/contact text without returning to the parent
company Contacts tab. The frozen verdicts remain coverage_unverified.
Added `url_matches_milestone` for exact return to an earlier observed route;
only completed milestones can be referenced, probes cannot commit a route,
and forward/self references are rejected. A native negative control uses
identical text on parent/child routes and rejects the wrong page.

That regression exposed a static-page observation bug: zero interactive
elements discarded the page URL and text. Such pages now remain observable;
real snapshot failures still fail. Latest current-source unit plus grounding
gate: **527 passed**, 23 existing deprecation warnings. Earlier full combined
gate was 563 passed/1 failed before the static-page fix; do not label that run
fully green. An earlier native run had 56 passed/1 fixture-start timeout;
only fixture navigation setup timeout was increased, not assertion deadlines.

No-hints complex CRM `20260929-214457-c2ba2377` failed contract validation
before browser execution (unknown slot references, then wrong input shape).
Added actionable valid-slot-name diagnostics without guessing substitutions.
Fresh validation run `20260929-215054-7af0fd40` passed independent coverage,
self-verification and **2/2 independent replays**, with no compiler hints or
planner fallback. Jev made 44 decisions including the actual parent return and
Contacts tab. The contract used two observed milestone URL references.
End-to-end: 352.33 seconds; planner 41,593 tokens plus decision 244,656 tokens.
This exposes remaining token overhead rather than demonstrating a reduction.

Frozen cart traces exposed an over-restrictive ready-state guard: text present
on the inventory could block the required detail visit, and an unchanged cart
state could block the required roundtrip. Ready-state action restriction now
protects field readbacks only; for text/URL outcomes Jev retains navigation
choices and must complete the full milestone goal before committing checks.
Focused controller gate: 30 passed. Current-source cart diagnostic
`20260929-215642-e7d97c4a` passed coverage, self-verification and **2/2 independent
replays**, with no hints/fallback and actual detail/removal/roundtrip actions.
End-to-end 134.38 seconds. Full native gate immediately before
the readiness narrowing: 58 passed. Final current-source combined gate:
**565 passed**, 24 existing deprecation warnings, 373.09 seconds.

September 30 continuation: frozen sort-detail repeat 2 finalized flaky with
independent runs `[true, false]` and a 120-second replay timeout. Its artifact
timestamps span September 29 22:03 to September 30 11:12; an environmental
interruption is evident, but its exact cause is not established. Retain the
failure and do not use this cohort for a performance claim. All three final
allocation attempts ended with terminal model errors before browser actions;
there were no automatic replacement attempts. All prior scorecards and failed
development attempts are retained. Final per-task reliable counts (out of 3):
negative-login 3; smoke, sort-detail, ops-transfer and ops-validation 2 each;
CRM-related-contact, CRM-search, CRM-lifecycle, cart-edit and ops-allocation 0.
No benchmark process remains running after the final report.

Overall programme remains incomplete. The completed frozen suite
`results/_frozen_bench/20260929-021759-26552383` remains **11/30 reliable**.
Later development allocation `20260929-091209-a1ae7d0b` and sort-detail v2
`20260929-092144-a67e73cd` each passed coverage, self-verification and two
independent replays; these do not replace the frozen denominator. See
`QAMATE_BENCHMARK_FAILURE_REVIEW_2026_09_29.md` for recorder fixes and the
independently checked ringspun spelling correction. The inspected/tuned former
holdouts are consumed; future unseen-site claims need newly reserved workflows.

Earlier provider blocker: both fresh attempts `20260929-164558-69c5a8e3`
(ops-validation) and `20260929-164613-de7091f9` (negative-login) received
**HTTP 402, quota category, from the Jev decision provider** before any recorded
browser action. No export/replay acceptance. Do not silently substitute another
decision model, buy credits, or repeat paid calls; user must restore provider
access or explicitly authorize a changed configuration.

User-authorized retry `results/_public_pilot/20260929-170530-36ae124f`
confirmed restored Jev access: ops-validation freshly authored the full
negative-validation/correction/save/reopen flow, passed coverage and self-checks,
and passed **2/2 independent replays**. This is a development run, not a new
three-author cohort or replacement frozen-suite result. The quota failure above
is historical, not a current blocker established by this retry.

The other retry `20260929-170752-5a6cce75` (negative-login) reached Jev/browser
execution but failed delivery: the planner invented a `login.html` URL outcome
for a login page actually served at the root. This is an ungrounded contract
failure, not a provider quota error; no assertion was weakened to pass it.

Added allowlisted/redacted decision error categories to single- and multi-app
controllers: quota, authentication, permission, rate limit, HTTP failure,
timeout, response/probability/choice validation, configuration and internal
failure. Provider bodies, URLs, keys and exception text are not returned.
Errors remain terminal; no retry policy was loosened. Historical opaque
LLMError failures cannot retrospectively be labelled quota errors.

Negative-login coverage now accepts the exact Username/Password-required text
with or without the decorative Epic sadface prefix. Missing, generic,
wrong-field and swapped-order assertions remain rejected. Historical scorecards
are unchanged. Focused error/controller/audit gate: **93 passed**, two existing
deprecation warnings. That earlier checkpoint lacked live validation; the
evening results above supersede the historical provider-blocked status.

Recovered 2026-09-27. Existing uncommitted work is preserved. Experimental behavior remains opt-in. No commits or deployments authorized.

- [x] CRM full company edit/search/contact flow: three fresh authors, two independent replays each (September 28 cohort; see source hashes).
- [x] Generated-test negative controls: wrong values, missing persistence, wrong relationships; assertion failures required (simulated rendered readbacks, not backend corruption).
- [ ] Frozen benchmark: three different apps, ten workflows, held-out coverage, three authors each; >=90% independently reliable.
- [ ] Auditable Jev ownership and bounded recovery, without planner browser fallback or duplicate submission.
- [ ] Two integrated cross-app workflows: three fresh authors and six replays each, isolated roles/auth and propagation checks.
- [ ] Matched old/new end-to-end performance: >=50% lower median time and tokens with verified success preserved; complete planner/decision usage and costs.
- [ ] Replaceable provider validation, alternative live provider, Qamate UI integration, complete regression gates and reproducible scorecard.

## Evidence rules

Frozen-contract probes and manual demonstrations are diagnostics only. Retain failed attempts. Do not repair exported tests to claim authoring success. The CRM Size persistence/display defect remains a failing case. Missing usage cannot pass a cost gate.

## Current recovery

Prior handoff reports a 17-decision frozen probe stopping at Save confidence 0.74, before export/replay. Prior read-only CRM 3-author/6-replay results do not satisfy the full CRM gate. Historical results require checking against current source hashes.

The textarea integration fixture omitted its required description input under the new verification-only default. Correcting this declaration preserves the runtime gate. Local uncertain clicks now receive at most one fresh-state confirmation per equivalent state/choice, with all alternatives retained and the original 0.8 threshold unchanged. No click occurs before confirmation; changed state/target or another uncertain choice stops.

## Retained September 27 evidence

- `results/_decision_probe/20260927-175059-c875c798`: Save confirmed at 0.87 after initial 0.77. Five milestones completed; reopening hit a child-heading versus link identity mismatch. No export.
- `results/_public_pilot/20260927-175353-e85c7cc4`: fresh compiler; six of ten milestones, no delivered test. Contact company selector lacked field context. Planner attempted a prohibited continuation after the benchmark's canned answer; runtime rejected it. Failure retained.
- `results/_decision_probe/20260927-175751-65f233f3`: same frozen contract reproduced missing company-field context; no export.
- `results/_decision_probe/20260927-180011-e64d2984`: field context let Jev confirm company search at 0.8; locator resolved the aria-hidden text child, so dispatch was prevented. Bounded recovery stopped; no export.
- `results/_decision_probe/20260927-180251-e61002fc`: ten milestones, exported 28 steps/47 assertions, source audit passed. Independent replay FAILED on a non-unique contextual Sector locator. This is not acceptance evidence.

Fixes in progress: associated textarea names; clickable ancestor fallbacks with identity validation; explicit non-dispatch recovery limited to two; combobox field context; case-insensitive contact-tab coverage matching with removal/incorrect-value audit fixtures; DOM-identity ordinal recording for contextual locators matching multiple nodes.

Regression baseline after initial grounding/recovery changes: 515 engine/integration tests passed, 23 existing deprecation warnings; both selftests and renderer/main syntax checks passed. Subsequent field-context and button-fallback native gate: 16 passed. Later edits require their own checks; these counts cover different scopes and are not additive.

- `results/_decision_probe/20260927-210958-c52335c4`: full milestones and source audit passed; replay exposed a second asynchronously rendered company relationship link. Failed evidence retained.
- `results/_decision_probe/20260927-211304-e5dfd997`: full milestones, source audit and **2/2 independent JUnit replays passed**, 26.273s and 26.061s. Frozen-contract diagnostic only; does not count as fresh authoring.
- After contextual-locator ordinal and observed-link occurrence recording, focused engine/native gate: **94 passed**. Fresh CRM cohort now required on unchanged runtime sources.

- `results/_crm_controls/20260927-211729-b49ff1e9`: unchanged frozen-diagnostic exported test passed the control replay and failed all three injected readbacks for the expected AssertionError (original city, revised city after reopen, contact company). No infrastructure error; file hash unchanged. These are rendered readback simulations, not datastore corruption tests or fresh-authoring evidence.
- Fresh attempts `20260927-211539-76707f57` and `20260927-211903-a0fc7e07` failed delivery: missing selected-company binding and low confidence while opening Sector, respectively. They remain in the denominator of any development-run report.
- Local button-combobox disclosure is now typed `open`, separate from submission/navigation `click`; only this reversible UI disclosure joins local fill/select/pick outside the submit threshold. Default read-only policy is unchanged. Exact selected-text assertions now support button-style comboboxes, including wrong-selection negative replay.
- Qamate settings expose the opt-in decision-owned mode; session UI shows milestone progress, bounded confirmations/recoveries, stop state and decision token usage. It explicitly leaves independent replay pending; UI syntax compiled, live Electron visual verification remains outstanding.

## September 28 recovery

- Contract preflight now rejects omitted ordered outcome groups before any browser dispatch, leaving the compilation attempt correctable. It supplies required outcomes only, not browser actions or selectors.
- Allowed same-origin local links use `navigate_link`, separating navigation from submit clicks. Submit confidence remains 0.8; default read-only behavior remains unchanged.
- `20260927-212310-7b658eda` passed two replays but failed coverage (reopened old-value absence omitted); it is not a success. `20260927-212623-97205f68` and `20260927-212720-a0c337c2` stopped on navigation confirmation.
- Fresh `20260928-023658-bee2c550` and `20260928-023727-7aa8ce19` each passed outcome coverage, self-verification and two independent replays. Their cohort peer `20260928-023532-278eec8f` failed both self-verification and independent replay because an option locator also matched the asynchronous Create option. Thus this cohort is 2/3, not acceptance.
- Fixed the generic record/replay mismatch: option models use exact full names and recorder code now preserves explicit `exact` flags for roles, text and labels. Native regression introduces a competing Create option only after recording; replay still chooses the existing option. No generated tests were edited. Added `agent_recorder.py` to pilot source hashes.
- Full engine/integration gate before that final fix: 521 passed, 23 deprecation warnings. After it, the new native regression passed and locator/normalizer gate passed 61 tests. A test-fixture JavaScript quoting error was corrected; it did not affect runtime source.
- Fresh unchanged-source cohort: `20260928-024509-5139d622`, `20260928-024535-0cbaf63c`, `20260928-024550-3db04b35`: **3/3 full-outcome authors, 6/6 independent replays**, all self-verified with no hybrid fallback. `results/crm-cohort-20260928.json` verifies distinct sessions, identical task/runtime hashes, exports and actual JUnit. Author wall times 206.7s, 286.8s, 341.4s; these exclude the independent replays and are not a performance comparison.
- `results/_crm_controls/20260928-024817-f38ff78a`: previous freshly authored test passed control and failed each injected readback via the intended assertions; source unchanged. Repeating controls against the accepted cohort itself before marking its gate complete.
- Frozen diagnostic process now returns failure when export audit or independent replay fails, even if live controller milestones completed.
- Exact accepted-cohort controls: `results/_crm_controls/20260928-025313-50aad07e`; baseline passed and all three intended assertion failures observed, generated test hash unchanged. CRM gates are complete; later capability additions must retain their regression coverage.

## Broader programme implementation (current source is newer than the passed CRM cohort)

- Ten declared workflows now span Atomic CRM, SauceDemo and disposable Dispatch Desk. Held-out tasks: `sort-detail`, `ops-allocation`. `crm-lifecycle` retains its Size assertions and known application defect. Operations fixture includes dependent selects, dialogs, pagination, validation, delayed persistence and context isolation.
- `frozen_benchmark.py` creates a separate runtime copy and records source/task hashes before three attempts per workflow. Coverage and two independent replays are mandatory; missing/unrun attempts remain in the planned denominator. This infrastructure is implemented, not a passed benchmark.
- `results/_frozen_bench/20260928-104508-9e669550` was interrupted after detecting contradictory authentication descriptions; `INTERRUPTED.md` records why. No suite acceptance.
- `results/_frozen_bench/20260928-105201-5f8c54e4`: **0/3 attempted authors reliable, 0/30 planned; 27 unrun**. Two decision handoffs, one terminal planner output-limit error (8192 tokens). Suite stopped automatically on the terminal error. This is the latest broader cohort, not evidence of repeat reliability from the earlier CRM pass.
- Subsequent fixes awaiting fresh full-suite validation: 16384-token compilation ceiling; explicit host-only public-demo auth guard and consistent prompt/settings/tool descriptions; obsolete CRM-only CLI task restriction removed; exact option-choice semantics clarified; CRM contact-form/company readback added (contract v3); applied-input bookkeeping reset per milestone and seeded from currently observed fields. Native milestone-isolation regression passed in a 45-pass run; its sole unrelated new-tool test-fixture error was subsequently fixed.
- Decision-owned cross-app path is being implemented in `decision_workflow.py` / `decision_workflow_tools.py`: planner supplies outcome contracts, Jev chooses DOM actions/captures/app transitions, legacy multi-app actions are runtime-blocked, export requires completed outcomes. Existing multi-app/project subset: 66 passed. New path is not live-authoring verified.
- `integrated_apps.py` is a disposable authenticated two-origin service with request approval and return acceptance workflows, shared backend state, delayed propagation and unrelated-record protection. Per-actor auth-state files stay local and are excluded from workflows/prompts. Native fixture/controller tests initially exposed accessible labels being mistaken for rendered capture values; actual text is now separated from labels and tests are running.
- Current non-held-out development pilots: `results/_public_pilot/20260928-180836-d9ab8272` (ops-transfer), `results/_public_pilot/20260928-180851-77849220` (SauceDemo smoke). They are development evidence, not a substitute for the frozen suite.

## September 29 continuation

- Both preceding development pilots failed delivery because milestone inputs incorrectly included button-choice/product names as editable fields. Contract schema guidance now distinguishes those cases; input readback remains mandatory.
- `20260928-182008-201f02a0` SauceDemo authored and self-verified, with two independent replay passes, but original verdict remains `coverage_unverified`: auditor missed the exact emitted CSS sort locator. Parser now recognizes that selector; regression retains failure for absent, wrong-value and unrelated-selector assertions. Original scorecard is not overwritten.
- `20260928-182007-d4b811a4` operations stopped before an uncertain Choose service click. The contract's readback outcomes were already true although its service-choice goal was unfinished. Ops-transfer v2 adds the selected service to mandatory pre-save outcomes; thresholds and fixture behavior are unchanged.
- Integrated author `20260928-181430-401ce2fc` failed after cycling field assignments; `20260928-182009-dcec414a` stopped on a wrongly record-scoped Submitted notice. One-to-one stage input assignment and explicit page-wide notice scope address those concrete failures.
- Integrated request `20260928-182804-eaaa8e13`: fresh author, outcome audit and 2 independent replays passed. Follow-up request `20260929-001755-9ef7c462` and return `20260929-001755-67bfbf6d` also passed with 2 replays each. Request peer `20260929-001755-e850224b` stopped at approval confidence .79; no export. These mixed-source development results are not a complete cross-app acceptance cohort.
- Cross-app confirmation now supplies the pending action and unchanged-state confirmation to the decision model, preserves .8 click threshold, records alternatives and bounds model calls by the overall deadline. New same-source request cohort launched at `20260929-002115/002116`; still pending.
- Benchmark events are now appended before callbacks, preserving evidence during an interrupted run. Protocol regression checks durability from inside the callback.
- Current offline engine gate: **486 passed**, 22 deprecation warnings. Native integration gate is running separately. Full programme remains incomplete and opt-in.

- Native integration gate completed: **54 passed**, two deprecation warnings. Subsequent focused coverage/controller gates: 61 passed, then 7 passed after the required-readback state-machine guard. Counts overlap and are not additive.
- `results/development-coverage-reaudit-20260929.json` re-audits the unchanged SauceDemo `182008` and operations `001918` exports: both pass coverage and had two independently passing replays. Operations required recognition of emitted sequential numeric typing and Tab blur; neither supplies assertion evidence. Negative regression removes the numeric readback and still fails. Original scorecards remain unchanged.
- Request cohort `20260929-015354-39c181b8`, `015355-37f8324f`, `015356-2d450287`: 3/3 authors, 6/6 independent JUnit passes. `results/integrated-request-cohort-20260929.json` also verifies matching source/task hashes, per-root session identities and five distinct create/approve roundtrips per author (live, two self-checks, two independent replays). Two names shared a second-resolution session timestamp but their roots/processes were distinct; audit identifies sessions by root plus name.
- Failed return cohort `20260929-015817-7c074b7c`, `015819-9b365c0a`, `015817-88083f51`: 2/3, retained in `results/integrated-return-failed-cohort-20260929.json`. Another cohort `020312-f5dccf15`, `020313-d436e111`, `020314-89772756` was also 2/3: failed author tried approval before committing its ready pre-approval checks. No mutation dispatched on that failure.
- Cross-app candidates now expose checks only when ready, limit app transitions to the current milestone binding, and freeze mutations until ready checks are recorded. Current input readback is separate from post-submit generated captures. Click threshold stays .8. Native fixtures confirm one confirmation per submit and no duplicate effects.
- Return cohort `20260929-020952-f88089da`, `020954-10579cbd`, `020954-ca7e3f6c`: **3/3 authors, 6/6 independent JUnit passes**, audited in `results/integrated-return-cohort-20260929.json`. A hallucinated bash call was rejected as an unknown tool; it never dispatched. Rejected unknown-tool attempts are counted separately from actual planner browser fallback. Same-source request repetition launched `20260929-021349/021351` to pair with this final controller source.
- Frozen suite `results/_frozen_bench/20260929-015420-9de31f8a`: **2 reliable / 4 attempted / 30 planned**, 26 unrun. CRM related: 2/3; failing compiler used page text for an editable field value. CRM-search never launched because scheduler passed the legacy forced-login diagnostic flag. Source copy stayed stable. Retain this failed suite; fix the scheduler before a new cohort.
- UI: actual shared component rendered with synthetic IPC in `scripts/check-agent-ui-live.py`; confirmation, completion and stop assertions pass. Screenshots in `results/ui-verification-20260929` visually inspected at 1400 and 900px. Fixed missing History icon causing a blank dedicated window and fixed-position Results/Dock overlap. This is browser-rendered UI with mocked IPC, not a live Electron/backend session acceptance claim. Cross-app settings and app/actor/capture status are exposed; independent replay remains explicitly pending.
