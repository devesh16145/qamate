# QAmate fast explorer — product review and redesign (2026-10-04)

Goal: a fast, accurate web-app explorer that takes a task in plain language, navigates
the app without browsing forever or getting stuck, and produces tests that replay
correctly. This document reviews the current agent and Jev implementation against that
goal and proposes a redesign. Sources: four code/evidence audits of this repository, an
external survey of browser agents and AI test tools, and three experiments run today.

## 1. Executive summary

The current system cannot reach the goal by further patching. The evidence:

- **Reliability has stalled.** Full 30-attempt cohorts went 36.7% → 36.7% → 26.7%
  despite dozens of targeted fixes between them (`QAMATE_PROGRAMME_CHECKLIST.md`).
- **Simple tasks take minutes.** Typical single-app tasks take 90–350 s end to end
  and use 35k–650k tokens. SauceDemo login/sort/cart: 132–209 s at best.
- **Five browsing architectures coexist,** with three controllers, three observation
  stacks, three loop guards and five system prompts. The live `config.json` turns every
  experimental flag on, which routes *every* task — single-app included — into the
  multi-app executor that only sees `data-testid` elements.

The problems are structural, and each has a well-understood fix that the rest of the
industry has converged on:

| Problem | Root cause | Fix |
|---|---|---|
| Slow | Many planner round trips with large prompts and thinking on; 2–4 heavy page scans per action; heuristic waits (`networkidle`, fixed sleeps); request interception that freezes the app while models think; five executions per authored test | Plan once, execute deterministically, call models only on ambiguity; Playwright's AI snapshot (30 ms); effect-based waits; no request interception on the sync API; one fast in-process replay |
| Gets stuck | One-shot contracts that end on any uncertainty; uncalibrated 0.8 confidence gate; read-only action policy that can't express most navigation; contracts written before seeing the app; loop detection keyed on volatile page text | Escalation ladder instead of termination; risk-based gates; normal writes allowed with confirmation for destructive ones; plan against an app map and re-plan on surprise; structural loop detection |
| Tests don't replay | Live actions use healing heuristics, but the generated test runs different code; generator rewrites and bugs; actions that aren't recorded | One step runtime shared by the live run and the test (replay-first); selectors from Playwright's own generator; accept a test only after it replays |
| Jev used against its design | Full page plus overall goal sent on every step (5.6k tokens/call); compound questions with negations; used for trivial waits/checks | Jev as a fast chooser over a short, pre-ranked shortlist with atomic questions; deterministic code for exact operations |

**Target for the redesign** (to be measured, not promised): a SauceDemo-class task in
about 30–45 s including replay, ≤ 3 planner calls, under 20k tokens, and ≥ 95% of
delivered tests passing an independent replay.

## 2. What was measured today

**Request interception freezes the app.** The decision loop installs
`page.route("**/*")` on Playwright's sync API. Handlers only run while QAmate is inside a
Playwright call, so while a model is thinking the page's network is paused.

| Interception | Page text after 3 s idle ("model thinking") | Data request latency |
|---|---|---|
| off | `loaded` | 11 ms |
| on (as `decision_browser_tools.py:283`) | `loading` | 2,710 ms |

This explains many "wait"/"check" decisions and low-confidence stops: Jev is shown pages
that haven't finished loading.

**Playwright's AI snapshot is fast and small** (internal `snapshotForAI` in 1.58; public
`aria_snapshot(mode="ai")` from 1.59 per its release notes):

| | Current `inspect()` | Playwright AI snapshot |
|---|---|---|
| Work per call | 5–8 page round trips: full DOM walk with `getComputedStyle`, a second aria pass, `elementFromPoint` per control; run 2–4× per action in decision mode | 57 ms first page, 29 ms after navigation |
| Unchanged page | full rescan | 6 ms, empty diff |
| Size | 8–16k chars (estimate) | 645 chars (login page), 6.2k (product list) |

Acting by snapshot refs works: filling and clicking via `aria-ref=eN` logged in to SauceDemo.

**Playwright's selector generator solves the duplicate-element problem.** Six product
buttons all named "Add to cart" resolved in 2–5 ms each to unique, stable selectors
(`[data-test="add-to-cart-sauce-labs-backpack"]`, `…-bike-light`, …), each matching
exactly one element. Duplicate and non-unique locators caused many past replay failures.

## 3. Diagnosis

### 3.1 Where the time goes

1. **Planner round trips** are the critical path.
   - Regular mode re-sends about 15–22k tokens per request (system prompt, 22 KB
     playbook, 47 tool schemas). Input is 94–99% of all tokens.
   - MiMo V2.6 Flash is a reasoning model, at about 49 output tokens/s with about 5 s to
     the first token (Artificial Analysis). Thinking is on by default. About 1,000
     reasoning tokens adds about 20 s per turn. Per-turn reasoning time has never been
     measured; it is the most likely single driver.
   - Plan bookkeeping (`update_plan`/`set_plan`) was 40–53% of planner tool calls.
     Removing round trips was the best measured lever: 19 → 9 requests cut tokens 55%
     and time 27%.
2. **Observation is redundant and heavy.** In decision mode each action runs a probe, an
   observe, a re-validation and a post-click snapshot, each a full `inspect(160)`. The
   multi-app observer makes up to about 600 browser round trips per turn.
3. **Waits are guesses.**
   - `networkidle` (3–4 s caps) after almost every operation. In SPAs it usually returns
     at once without waiting for the click's data; on polling pages it burns the full cap.
   - Fixed 1–1.5 s sleeps in option polling, and at least 4 s after every direct-selector
     fill.
   - Dropdown ladders can take 60–100 s in the worst case.
   - A "wait" decision costs a full Jev turn plus a 0.5 s sleep, and doesn't pump
     Playwright, so the page still can't load.
4. **The request-interception freeze** (section 2).
5. **Verification runs the flow five times.** Live run, `pytest --collect-only`, two
   self-verify runs (each a fresh Python plus Chrome with 1080p video, tracing, a 2 s auth
   wait and 500 ms after every click), then two independent replays in benchmarks. A
   failed replay in regular mode means re-driving the whole flow.
6. **Jev itself is not the bottleneck**: about 0.5–1.2 s per decision, 3–6% of authoring
   time. In decision mode its tokens dominate (60–85%) only because each call carries the
   whole page.

### 3.2 Why it gets stuck

- **Terminal on uncertainty.** In decision mode one "stop", one click below 0.8
  confidence, one provider error (no retries, even for 429/5xx) or a candidate overflow
  ends the session's only contract. After that the planner gets *zero tools*. Ledger
  stops at confidence 0.47–0.79 dominate the failure list; the 0.8 threshold was never
  calibrated.
- **The action policy can't express the task.**
  - Read-only by default: only same-origin `<a href>` links and seven whitelisted button
    labels.
  - No login except public demos; all non-GET requests blocked.
  - "Log in, open Orders, test the order lifecycle" cannot run in single-app decision
    mode.
- **Contracts are written blind.** The planner must predict exact outcomes, routes and
  bindings before seeing any page beyond the entry. That produced invented routes and
  assertions, wrong bindings and output-cap failures (8k, then 16k).
- **Loop detection keys on volatile text.** State hashes include up to 20k characters of
  page text, so clocks, toasts and spinners defeat the circuit breakers. The same
  volatility turns progress into `state_changed` handoffs.
- **Unbounded waits.** `ask_user` and the GUIDED value gate wait forever; cancelled tools
  keep running on the single browser thread.

### 3.3 Why tests fail on replay

- **The live action and the test are different code.**
  - Live: `smart_locator` healing with `.first`, geometric `nth`, force clicks, a
    `Control+a` typing path.
  - Generated test: a bare locator plus app-specific rewrites (MUI autocomplete,
    "gated submit" with a second JavaScript click, select-all-checkboxes, "admin" URL
    routing) that apply to every app.
  - The multi-app path, which runs the same function live and at replay, has *no*
    documented replay asymmetry. The single-app path has at least 11.
- **Three new bugs, verified in code:**
  - scroll steps emit `page.mouse.wheel(0, )` (TypeError);
  - the `set_checked` fallback emits `.set_checked(...).scroll_into_view_if_needed()`
    (AttributeError);
  - `Control+a` doesn't select all on macOS, so live typing gives "70" while replay gives
    "7".
- **Not everything is recorded or checked the same way.**
  - `press_key` and `mouse_click` record nothing.
  - Body-text checks use visible text live but `textContent` at replay.
  - Viewport, timeouts, authentication and leftover state differ between authoring and
    replay.
- **Refs can collide.** "Cart", "Cart", "Cart 1" produce `cart`, `cart-1`, `cart-1`.

### 3.4 Jev, judged against its own documentation

TypeSafe describes Jev as a typed classifier for routing, gating, ranking and closed-set
choices. Its documented weaknesses include:
- accuracy falls as unrelated state grows;
- a bias toward the first option;
- poor handling of negation and multi-hop questions;
- susceptibility to untrusted text in the state.

QAmate currently does all of these: it sends the whole page plus the overall goal, asks
one compound question with "DO NOT …" criteria, and includes page text. Jev fits QAmate
well as:
- a **chooser over a short shortlist** ("which of these 8 elements is the Login button?");
- a **navigator** during exploration ("which of these nav links most likely leads to
  Orders?");
- a **gate** ("is this action destructive?", "is an error message showing?", "did the
  expected outcome happen?"), with several atomic questions in one call.

It fits poorly as the component that reads a whole page and decides the next step toward
a long goal.

### 3.5 What the rest of the field does (external survey)

- **Observation:** compact accessibility snapshots with element refs, screenshots only
  when needed (Playwright MCP, Anthropic browser tool, browser-use).
- **Batching:** a few actions per model turn, stopping on surprise (browser-use, Anthropic
  tools).
- **Caching:** successful steps are cached and replayed without a model call; AI runs only
  on a miss or a failure (Stagehand: 20–30 s → 2–3 s; Skyvern code caching; Momentic step
  cache).
- **Validation:** an explicit validator or progress check (Skyvern +17 WebVoyager points).
- **Testing:**
  - Playwright's own test-agent Generator writes the spec from a log of actions it
    actually executed, using Playwright's locator generator. Its Healer forbids
    `networkidle`.
  - Slack's study: agent-run flows succeed, but generated tests break late (last
    interaction or assertion), so CI runs deterministic tests and agents are used for
    exploration and repair.

## 4. Product principles for the redesign

1. **What ran live is the test.** One typed step runtime executes the live session *and*
   the generated test. A test is accepted only after it replays in a fresh browser.
2. **Plan with the big model, execute with code, decide ambiguity with Jev.** The planner
   is called rarely (plan, re-plan on surprise, assertion review). Most steps ground to a
   unique element deterministically and run with no model call.
3. **Every wait has a condition.** After an action, wait for its effect (URL change,
   element shown or hidden, value set, DOM quiet for about 300 ms), capped in seconds. The
   same condition goes into the test. No `networkidle`, no fixed sleeps.
4. **Never terminal on uncertainty; never unbounded.**
   - Escalate: deterministic match → Jev shortlist → planner → user.
   - Budgets per step and per task are visible in the UI. When a budget runs out, deliver
     the partial test with a clear diagnosis.
5. **Allow real QA work.** Forms, logins and writes are normal in test environments.
   Confirm only destructive or irreversible actions (delete, pay, send), classified by
   Jev and the planner. Don't block the network.
6. **Learn the app once.** A quick scan builds an app map (routes, navigation, forms) and
   reusable step sequences (login) per project, so later tasks don't rediscover them.
7. **One mode.** Replace the six experimental switches with one engine; keep the old
   engine behind a "Legacy agent" setting only until the new one beats it on the
   benchmark.

## 5. Target architecture

```
 task ──► Planner (MiMo, thinking ON, 1 call) ── plan: short list of intents + expectations
            ▲    (uses app map + current snapshot)            │
            │ re-plan on surprise (rare)                      ▼
            │                                   Orchestrator — "act until surprised"
            │                                   per intent:
            │                                    1. Observer: AI snapshot (≈30 ms; diff ≈6 ms)
            │                                    2. Grounder: rank elements for the intent
            │                                       unique strong match ─► act (no model call)
            │                                       ambiguous ─► Jev choice over ≤10 candidates
            │                                       none / low confidence ─► planner ─► user
            │                                    3. Runtime: act via Playwright auto-wait,
            │                                       then wait for the expected effect
            │                                    4. Record the step (Playwright-generated selector)
            └──────────────── surprise / budget ◄─┘
                                                │
              Assertion builder: observed effects + task expectations → planner review (1 call)
                                                │
              Verifier: in-process replay in a fresh context (same runtime) → accept / heal one step
                                                │
              Test file: readable pytest that calls the same runtime helpers
```

**Components**

- **Step runtime** (new, shared live and replay):
  - Typed steps: `goto`, `click`, `fill`, `select`, `check`, `press`, `upload`, `hover`,
    and `expect_url / visible / hidden / text / value / count`.
  - Each target stores a Playwright-generated selector (verified to match exactly one
    element at record time), a human description and a fingerprint for repair.
  - The generated test is plain pytest, e.g.
    `qm.click('[data-test="login-button"]', "Login")`, executed by the same functions.
  - Generalizes what `multi_app.execute_step` already proved.
- **Observer:** Playwright AI snapshot with incremental diffs, scoped to the active dialog
  or region. One snapshot per step, reused for grounding and validation.
- **Grounder:** deterministic ranking by role compatibility, exact or near name match,
  label or placeholder, and region priority (dialog > main > nav). Uniqueness and margin
  decide whether a model is needed.
- **Jev usage:**
  - an element choice over a ranked shortlist, with positive option text and a "none of
    these" option;
  - atomic yes/no gates in the same call (destructive? error shown? outcome reached?);
  - navigation choice during exploration;
  - a threshold set per action risk, with the dated model slug pinned.
- **Planner:**
  - compact structured output (no 48-tool schemas);
  - a prompt of about 2–3k tokens plus the app map and current snapshot;
  - thinking on for the plan, off and output-capped for small follow-ups;
  - a stable prompt prefix so provider caching works (MiMo cache hits are 50× cheaper).
- **Orchestrator:**
  - runs intents in order and stops on surprise;
  - loop detection on a structural fingerprint (URL, element refs, field values) and an
    action-repeat window;
  - budgets with an escalation ladder;
  - streams live step status to the UI.
- **Verifier:**
  - immediate replay in a fresh browser context using the same runtime (no video or
    tracing, seconds not minutes);
  - on failure, report the failing step with snapshot and candidates and heal that one
    step;
  - an optional pytest run in the background for JUnit and report evidence.
- **App map:**
  - a budgeted crawl (same-origin links, nav items, tabs) using the Observer, with Jev
    choosing promising branches;
  - stored per project and reused by the planner;
  - successful sequences (e.g. login) cached by (route, intent).

**Kept from today:** the project store and auth capture, the provider catalog and BYOK,
the session UI, the `decision.py` adapter (simplified), the conftest fixtures
(simplified), the benchmark task definitions, and the outcome-audit ideas.

**Retired once the new engine wins:** `execute_goal`, `browse_goal`, `browse_workflow`
and the planner-driven multi-app tools as separate modes; `recorder_parser` app-specific
rewrites; `smart_locator` healing at authoring time; the 22 KB playbook; the six
experimental flags.

## 6. Delivery plan

Each phase ends with measurements on the same benchmark tasks, run one at a time with
per-phase timers (startup / plan / execute / verify) and per-role tokens.

| Phase | Scope | Exit criteria |
|---|---|---|
| **0 — Stop the bleeding** | Fix the mode precedence and flag UI so single-app tasks never reach the data-testid-only executor; remove sync request interception; fix the three verified replay bugs, record `press_key`, use visible-text body checks; verification = one run without video/tracing; telemetry for reasoning tokens and time per planner turn | Benchmark baseline recorded with timing breakdown; no regression in engine tests |
| **1 — Step runtime + observer** | Typed step runtime shared by live and replay; Playwright AI snapshot (upgrade Playwright to ≥1.59 for the public API, or wrap the 1.58 internal call); selector generation via Playwright; effect-based waits; test emission; in-process replay verifier | Local fixtures (Dispatch Desk, SauceDemo) driven step by step through the runtime, replay green 10/10; per-action browser time median < 1 s |
| **2 — Fast executor** | Grounder; Jev shortlist and gates; orchestrator with act-until-surprised, structural loop detection, budgets and escalation; live step stream in the agent panel | Scripted plans for the 10 benchmark tasks execute without a planner; Jev used only on ambiguity |
| **3 — Planner integration** | Compact planner prompt and structured plan; re-plan on surprise; assertion builder and review; "Fast agent" as the default mode, legacy behind a setting | SauceDemo smoke ≤ 45 s end to end including replay, ≤ 3 planner calls; ≥ 90% of delivered tests pass independent replay on the 10-task suite |
| **4 — App map and memory** | Quick-scan crawler with Jev navigation; per-project app map; cached sequences (login); planner grounded in the map | Second task on the same app ≥ 30% faster than the first; one genuinely unseen site attempted with an ordinary objective |
| **5 — Retire legacy** | Remove the old modes, flags, playbook and codegen rewrites once the new engine beats them | Engine tests green; benchmark at or above targets |

## 7. Risks

- **Planner plans without seeing every page.** Mitigation: app map plus re-plan on
  surprise; plans are intents, not exact assertions.
- **Deterministic grounding misses unusual UIs.** Mitigation: Jev shortlist, then planner,
  then a user picker; record which tier resolved each step and tune from data.
- **Playwright internal API.** Mitigation: upgrade to a version with the public AI
  snapshot, and keep a fallback to `aria_snapshot`.
- **Effect waits on slow or asynchronous apps.** Mitigation: expected-element waits with
  caps, a quiet-DOM fallback, and the same waits emitted into the test.
- **Scope.** Mitigation: build alongside the current engine, reuse what works, and measure
  every phase; legacy stays available until replaced.

## 8. Progress log

### 2026-10-04 — Phases 0–3 built (fast engine is the default; Classic stays selectable)

| Phase | What landed | Evidence |
|---|---|---|
| 0 | Sync request interception removed from the decision loop; three replay bugs fixed (scroll, set_checked, macOS select-all) plus unrecorded key presses and visible-text checks; authoring verification without video/tracing; per-request planner timing and reasoning tokens | Regression tests fail on the old code, pass on the new; verification 3.2 s → 2.0 s on two demo tests |
| 1 | `qm_runtime` (one implementation of every step, live and replay), `qm_observe` (Playwright AI snapshot), `qm_selectors` (Playwright-chosen locators, rendered as readable Python, verified to target the same element), `qm_steps`, `qm_testgen`, `qm_verify`; Playwright 1.58 → 1.63 | Dispatch Desk 11-step transfer: authoring 3.6 s, fresh replay 3.7 s, pytest replay with QAmate's conftest 5.0 s, stable over repeated runs |
| 2 | `qm_ground` (deterministic ranking with item context for repeated controls), `qm_decide` (Jev over a short list with "none of these"), `qm_explorer` (precise stop reasons, destructive-action confirmation, loop stop, custom dropdowns) | SauceDemo login + two specific products + check from plain-language steps: 3.6–3.9 s, about 0.35 s per click/fill, no model calls |
| 3 | `qm_planner` (compact page summary, plan-as-far-as-visible, re-plan on surprise), `qm_agent` (bounded rounds and time; saves a test only after a fresh-browser replay passes), wired into the agent runtime and panel (Engine: Fast / Classic) | Scripted-planner tests through a real agent session: plan → execute → replay → saved into the project suite → transcript persisted |

Not yet measured: the live planner (MiMo) on the benchmark tasks — needs the user's keys. Next: run the
benchmark tasks on the Fast engine with MiMo (and Jev for ambiguity), compare with the Classic
baseline, then Phase 4 (app map and cached sequences) and Phase 5 (retire legacy modes).

### 2026-10-04 (later) — complex app, streaming plans, app memory (Phase 4 core), faster waits

Driven against a harder app (the Atomic CRM demo: React + shadcn/Radix, browser-local data) with
scripted plans, i.e. no model calls, so these numbers are the engine's share only.

| Change | What landed | Evidence (development, not live acceptance) |
|---|---|---|
| Grounding for business apps | Nameless card links labelled by their content and clicked through their heading; exact label beats "contains the word"; UI synonyms and plurals (new/create/add, save/submit, log in/sign in); a named role wins ("Contacts tab"); counts in labels ignored; same-destination links are one choice; text checks target the control showing the value; searchable comboboxes typed into, and only an exactly-labelled option taken (never 'Create "<value>"'); card images named like their title are not rivals; look-alike controls need a `within` | CRM lifecycle (create, edit, search, reopen; 35 steps) and CRM related contact (31 steps) author and replay green; the genuine missing-Size defect still fails its check; SauceDemo cart-edit 19 steps, 3/3 replays green |
| Streaming plans | Providers stream (OpenAI-compatible incl. MiMo, Anthropic, Ollama); steps run as soon as the model has written them; a step that doesn't fit abandons the rest of the reply (connection closed); test name/flow first in the reply | Fake model at 0.5 s/step: steps run at the model's pace, first within ~1 s; a wrong step abandons a 10-step reply |
| App memory (`qm_map`, `page_map.json`) | Pages by route with exact control labels and where clicks led, learned from every observation; a bounded links-only quick scan of a fresh project (nav + "New/Create" links; never logout/delete/export or other origins); the planner gets the relevant pages; "Explore the app" maps instead of authoring | CRM: 7 pages in 8.5 s incl. both create forms; 2.5k-character map; fixture proves the scan's safety |
| Waits | Quiet window 250 → 120 ms (60 ms when nothing changed); script/stylesheet loads count as pending; grounding re-looks for up to 1.5 s while the page is still changing | CRM authoring 15.4 → 12.8 s and replay 15.7 → 12.9 s (related: 16.3 → 12.9 / 15.9 → 12.7); 10/10 replays green; fixture suite 3×28 green, 86 → 61 s |
| Benchmark | `engine/qm_bench.py` + Benchmark tab (Engine: Fast): the frozen gate's ten workflows, cold or warm memory, each saved test re-run twice through pytest | End-to-end test with a fake model |

Not yet measured: the live planner on these ten workflows (time, tokens, re-plans, reliability) —
run Benchmark → Engine: Fast. The streaming and app-memory gains depend on live planner behaviour.
Optional next steps after that measurement: re-plans without thinking (MiMo/DeepSeek declare an
off switch; keep it opt-in until measured), and cached step sequences (login) per project.
