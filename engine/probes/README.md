# Fast-engine probes: does it generalise?

Model-free checks of the fast engine (`qm_*`): hand-written plans go straight to the executor,
so they measure observe → ground → act → record → replay, not the planner. Run them before and
after any change to grounding, waits or the step runtime.

```
venv/bin/python engine/probes/run_plans.py        # ten benchmark flows + first attempts on unseen apps
venv/bin/python engine/probes/run_patterns.py     # 19 common web patterns on patterns.html
venv/bin/python engine/probes/run_backend.py      # an app with server-side data (replay-before-save)
```

Rules: a plan for an unseen app is written from `run_plans.py --look URL` output only, run once,
and its first result is kept (set `"seen": false`). Once the engine has been changed because of an
app, that app is no longer unseen — add fresh ones for the next round.

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
