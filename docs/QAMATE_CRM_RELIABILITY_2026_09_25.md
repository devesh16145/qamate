# CRM authoring follow-up — 2026-09-25

The goal remains experimental and incomplete. MiMo 2.6 Flash remains a replaceable planner profile; Jev 1.13 is the decision profile. This work fixes observed harness defects, not model-specific prompts or prepared CRM selectors.

## Failure evidence

- `results/_public_pilot/20260923-013429-1868d525`: the frozen read-only Atomic CRM search task timed out without an export. Interactive observations omitted ordinary empty-state text. The agent requested unavailable vision tools and attempted assertion-only goals, which the harness rejected. Repeated malformed tool names were also observed.
- Two content-free synthetic streaming probes (`results/_protocol_probe/20260923-014113-439e9b16.json` and `20260923-014239-fd55d591.json`) returned four correctly separated tool calls. They did not reproduce the malformed-name problem. No speculative transport rewrite was made.
- `results/_public_pilot/20260925-172549-dba776c9`: with rendered text and vision capability gating, the agent created a test and correctly observed `No companies found`. It still timed out after 318.7 seconds, 26 planner requests and 33 tool calls. Both self-verification attempts failed; independent replay also failed. Inspection found the generated navigation duplicated the app path: `/atomic-crm-demo/atomic-crm-demo/`. Planner usage is incomplete, so zero placeholder tokens are NOT savings. Jev reported three calls and 19,061 tokens.
- The exported restoration check only asserted that the empty-state message disappeared, not positive list population. Even a passing replay of that source would not establish full task coverage.

## Changes

1. Capability-aware vision exposure: unavailable/unconfigured vision tools are hidden, cached calls fail explicitly, and terminal provider rejection disables subsequent attempts. No automatic provider switch.
2. Bounded rendered text alongside interactive observations: prioritize an active dialog or main region, cap text at 2,000 characters and scan at 2,000 text nodes, exclude hidden/editable values. This evidence is not sent to Jev and is not itself a verified assertion.
3. Assertion-only bounded goals: `actions=[]` with nonempty typed checkpoints executes existing assertion machinery without a decision request. Stops at first failure, never unlocks direct mutations, and never claims independent verification.
4. Origin-relative navigation generation uses `urljoin` instead of concatenating a captured path onto a possibly subpath-bearing app URL. Regression cases cover subpaths, environment host changes, hash routes, queries, admin URLs and leading double slashes, including replacement exports.

## Verification

Focused rendered-text, vision and grounding gate: 64 passed. Assertion-only planner policy gate: 34 passed. Navigation and manual-input generation: 12 passed. Both engine selftests passed. The full `engine/tests` plus `engine/integration_tests` gate passed **465 tests** in 319.21 seconds, with 22 existing PydanticAI `prepare_tools` deprecation warnings. `git diff --check` passed (line-ending warnings only). The combined live run started after this gate finished.

The first September 25 retry predates assertion-only batches and the navigation fix. Its retained artifacts must not be relabeled as testing those changes.

## Combined live result

Artifact root: `results/_public_pilot/20260925-174040-b367ac54/`.

- Frozen CRM contract and limits unchanged: 300-second authoring timeout, 48 tool calls. No source edits or full test suite overlapped this run.
- MiMo `mimo-v2.6-flash` + `typesafe/jev-1.13`; three Jev calls, no controller fallback.
- Created and self-verified the test; **both independent replays passed**. Retained per-replay JUnit paths are in `scorecard.json`.
- Authoring 220.4 seconds, end-to-end 253.03 seconds, 16 planner requests, 15 tool calls.
- Complete reported usage: 155,901 planner tokens plus 16,157 decision tokens = **172,058 combined tokens**. Jev decision time totals 1.827 seconds. The earlier failed run has incomplete planner usage; no token-saving percentage is valid for this comparison.
- Generated navigation now correctly uses `urljoin(base_url, "/atomic-crm-demo/")`.
- Source review confirms search value, actual empty-state message and Companies identity assertions. However, the restoration checks only assert absence of the empty message plus a header and URL. They do **not** positively establish populated result rows. No random company name/count is hard-coded.

**Conclusion: replay reliability improved on this one task; full task coverage remains unverified.** The raw scorecard predates the follow-up coverage gate and says `reliable`; retain it as execution evidence, not full-requirement success. `coverage_review.json` records the separate manual assessment. This is not a matched speed comparison or evidence that Jev caused the improvement.

After this run, non-smoke public tasks now always pass through the coverage gate. The conservative auditor does not yet assess CRM's positive list-population outcome, so future green CRM replays are `coverage_unverified`, never silently counted as full task success. The post-run gate passed **51 focused tests**. The full 465-test gate above predates only this final scorecard change.

## Remaining acceptance work

Add stable structural list-population assertions and a matching coverage recognizer, then repeat held-out CRM authoring. Broader exploration and authenticated real-business multi-app tasks remain unproven. The prior public paired trials saved tokens but did not improve speed; the full goal in `QAMATE_JEV_PLAN.md` is not achieved.
