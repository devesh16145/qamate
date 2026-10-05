# Fast-engine probes: does it generalise?

Model-free checks of the fast engine (`qm_*`): hand-written plans go straight to the executor,
so they measure observe → ground → act → record → replay, not the planner. Run them before and
after any change to grounding, waits or the step runtime.

```
venv/bin/python engine/probes/run_plans.py        # 25 plans: ten benchmark flows + every app tried since
venv/bin/python engine/probes/run_patterns.py     # 45 common web patterns on patterns.html
venv/bin/python engine/probes/run_backend.py      # an app with server-side data (replay-before-save)
venv/bin/python engine/qm_chooser_eval.py         # chooser models on 43 fixed cases (needs keys; Benchmark tab)
```

Rules: a plan for an unseen app is written from what the planner is shown only
(`run_plans.py --look URL`, then `plans/x.json --show` page by page), run once, and what went wrong
on that first attempt is kept in the plan's `"note"`. Once the engine has been changed because of
an app, that app is no longer unseen — add fresh ones for the next round. A plan that fails for the
app's own reasons (a demo that loses data when used quickly) is cut back until it is deterministic.

## State on 2026-10-05, engine `e547d85` (no models: this measures the engine, not the planner)

| Probe | Baseline `aaffea5` | Now |
|---|---|---|
| Ten benchmark flows, perfect plans | 6/10 | 10/10 |
| All stored plans (25, two fresh-browser replays each) | — | 25/25 |
| Common web patterns | 6/19 | 45/45 |
| App with server-side data | unsaveable or polluting | 4/4 scenarios behave as they should |
| Unit tests / fast-engine browser tests | — | 640 / 51 |

**First attempts on apps the engine had never seen** (engine gaps found before the app passed;
planner-side corrections such as adding a `within` are not counted):

| # | App | What it is | Gaps |
|---|---|---|---|
| 1 | TodoMVC | single-page list | 1 |
| 2 | Practice Software Testing tool shop | Angular store | 3 |
| 3 | MUI CRUD dashboard | React + MUI admin | 3 |
| 4 | demoqa web tables | table + modal form, ad scripts | 2 |
| 5 | A Next.js store | store | 2 |
| 6 | LambdaTest playground (form, table) | plain forms | 0 |
| 7 | Practice registration form | custom widgets | 1 |
| 8 | XYZ Bank | AngularJS app | 4 |
| 9 | OpenCart store | 1,150-element pages, drawers, carousels | 7 |
| 10 | Ant Design form demo | antd widgets | 3 |
| 11 | Angular Material selects | mat-select, several choices | 3 |
| 12 | Angular Material date range | calendar pop-up | 1 |
| 13 | Quill playground | role-less editor inside a frame | 1 |
| 14 | jQuery UI droppable | drag-and-drop inside a frame | 0 |

The count is **not** falling to zero: every new kind of app still shows something. What it shows has
changed, though. Rounds 1–5 found steps the engine could not do at all or did wrongly (a confident
wrong click). Rounds 8–13 found mostly what the planner is shown (dropdown options, bold values,
identical items, off-screen drawers), locators leaning on generated ids, speed, and one real
reliability bug (a request slower than 1.5 s was treated as background; a form swapped in by it
received the next step's typing in its old copy — `slowswap` in patterns.html reproduces it).
Expect a new app to need 0–3 engine fixes still; do not claim otherwise.

**What these probes do not measure:** the live planner (labels it invents, steps it forgets, how
often it re-plans), the chooser model, cost and time with real models, anything behind a login the
probes may not use, canvas-drawn apps, and file contents of downloads. The live numbers come from
Benchmark → Engine: Fast and Benchmark → Decision-model comparison, run with the user's keys.

## Baseline, 2026-10-05, engine frozen at `aaffea5`

**Plans — 6/12 pass.** Benchmark flows 6/10; unseen apps 0/2 on the first attempt.

| Plan | Result | Why |
|---|---|---|
| cart-edit, crm-lifecycle, crm-related-contact, ops-transfer, smoke, sort-detail | pass | 2.8–15.3 s authoring, replays green |
| negative-login, ops-validation, ops-allocation, unseen-todomvc | FAIL | loop guard refuses a third identical action on the same URL (Login ×3, Save ×3, Next page ×3, Enter ×3) |
| crm-search, unseen-toolshop | FAIL | a label containing a role word ("Clear **search**", "**Search**") is scored as a role hint, so the exactly-named control loses |

Further blockers found on the tool shop after forcing past step 1: its product cards are dropped by
the snapshot parser (Playwright quotes lines whose label contains `: `), and its "Add to cart"
button has no name in the AI snapshot although `get_by_role("button", name="Add to cart")` finds it.

**Patterns — 6/19 pass** (row action, date field, 3.5 s toast, French labels, rich-text box, same
labels in two sections). Fail: `confirm()` pop-up (auto-dismissed), new tab (not followed), text
checks inside an iframe or shadow DOM (the click works, the page-text check can't see it), result
after 6.5 s (authoring check timeout is 5 s), unlabelled checkbox / dropdown, icon-only button,
hover menu, toast gone within 1.2 s (the settle wait outlasts it), Hindi labels (tokenizer is
ASCII-only), type-ahead that needs a pick (recoverable by a re-plan), and — the dangerous one —
**"Ticket 150" in a lazy list clicked "Ticket 1"**: substring scoring plus the "links to the same
place are one choice" rule (every link was `href="#"`) made a confident wrong click.
The row action passes with a positional locator (`.nth(1)`), which breaks if rows reorder.

**Server-side data.** Unique names: the replay's create is rejected, the test can never be saved.
Duplicates allowed + reopen from the list: replay fails (two matches). Create + check only: saved,
and every run adds another record.

**Exploration** (no models): AdminLTE 3 demo 60 pages in 119 s; CoreUI free demo 3 pages (its
sidebar is neither a `<nav>` nor ARIA-marked).
