# Autonomous Testing — Architecture & Roadmap

**Vision:** point the tool at an app and its PRD; it explores, writes self-healing
tests, runs them reliably, and keeps them green as the UI changes — no
record / play / fix loop. This document is the build plan and the contract
between layers so each can be built and sold independently.

## Why this is sellable (and not "another Playwright wrapper")

Record-and-replay and the trace viewer are commodities (Playwright ships them
free). The defensible value is **autonomy + maintenance**:

| Capability | Sellable promise | Layer |
|---|---|---|
| Self-healing locators | "Your tests don't break when the UI moves." | **L0 ✅ built** |
| App exploration | "Zero-config: it discovers your flows." | L1 |
| PRD → tests | "Coverage is driven by requirements, not guesswork." | L2 + L3 |
| Reliable runner | "Green means green — flake is quarantined, not ignored." | L4 |
| Maintenance loop | "It fixes its own tests and tells you what drifted." | L5 |

The wedge: **AI-maintained E2E for B2B commerce / marketplace apps** — ship the
PO-lifecycle, catalog, GST, and seller/admin domain knowledge as pre-built
assets a horizontal tool can never match.

## The pipeline

```
 PRD.md ─► [L2 Requirement Extractor] ─► requirements.json ─┐
                                                            ├─► [L3 Test Synthesizer] ─► test_cases.json + test_<flow>.py
 Live app ─► [L1 App Explorer] ─► app_model.json ───────────┘            (uses L0 self-healing locators)
                                                                                    │
                                                          [L4 Reliable Runner] ◄────┘
                                                                    │  (heal + retry + flake quarantine)
                                                                    ▼
                                              results/<ts>/{traces,heals,checkpoints,videos}
                                                                    │
                                                          [L5 Maintenance Loop] ─► auto-repair PRs, coverage drift
```

Each arrow is a JSON contract (below), so layers are swappable and testable in
isolation. The LLM (Claude) is the reasoning engine for L1–L3 and L5; L0 and L4
are deterministic so reliability never depends on a model call.

---

## L0 — Self-healing locators ✅ BUILT

`engine/smart_locator.py`. Resolves an element through three tiers, cheapest
first, recording any heal:

1. **Primary** — the authored/recorded locator.
2. **Fallbacks** — same-element alternatives (role+name, text, test-id, label,
   relaxed CSS), auto-derived from the primary or captured at synthesis time.
3. **Fingerprint scan** — find the page element most similar to the stored
   identity (tag/role/name/text), accept above a confidence threshold.

Use it in any test via the `heal` fixture:

```python
def test_TC_X(seller_page, heal):
    heal(seller_page, 'page.get_by_role("button", name="Save")').resolve().click()
```

Heals are written to `results/<ts>/heals/<tc_id>.json` and surfaced as
`heal_count` per test and in `get-run-artifacts`. Verified against id/class
drift (fallback tier) and aria/text-only drift (fingerprint tier, 0.85 conf),
and it fails loud (`SelfHealError`) when an element is genuinely gone.

**Next on L0:** capture a real fingerprint at record/synthesis time (currently
derived from the locator string); auto-rewrite the healed locator back into the
test on demand (the L5 hook).

---

## L1 — App Explorer  (build next)

A Playwright-driven crawler that logs in (reusing `conftest` login) and walks the
app breadth-first, emitting an **App Model**: the pages, their interactive
elements (with fingerprints L0 can reuse), and the navigation graph.

**Contract — `app_model.json`:**
```json
{
  "base_url": "https://supplier-dev.agrim.app/",
  "generated_at": "2026-05-29T...",
  "pages": [
    {
      "id": "catalog",
      "url": "/catalog",
      "title": "Catalog",
      "elements": [
        {"ref": "search", "role": "textbox", "name": "Search", "fingerprint": {...},
         "locators": [{"by": "get_by_placeholder", "value": "Search"}]},
        {"ref": "incorrect_link", "role": "link", "name": "Incorrect?", "fingerprint": {...}}
      ],
      "transitions": [{"on": "incorrect_link", "to": "incorrect_modal"}]
    }
  ]
}
```
Reuses `smart_locator`'s strategy/fingerprint format so synthesized tests are
born self-healing. Build with a depth cap, a visited-URL set, and a per-page
element budget; reuse the existing `dom_inspector.py`.

## L2 — Requirement Extractor  (build next)

PRD markdown → testable requirements. Deterministic pre-pass (headings, "must/
should", acceptance-criteria bullets, tables) hands candidates to Claude, which
returns structured, atomic, verifiable requirements.

**Contract — `requirements.json`:**
```json
[
  {"id": "REQ-CATALOG-007", "area": "Catalog", "role": "Seller",
   "statement": "Seller can request a new product with L/B/H, HSN, image, MRP, price, expiry",
   "acceptance": ["Request modal opens", "All fields accept input", "Submit shows success toast"],
   "priority": "P1", "source": "PRD.md#request-product"}
]
```

## L3 — Test Synthesizer  (the headline)

`requirements.json` × `app_model.json` → Claude maps each requirement's
acceptance criteria onto real elements in the app model and emits the existing
**`test_cases.json` checkpoint schema** + a `test_<flow>.py` that uses `heal(...)`
locators and `checkpoints.run(...)`. Output flows straight into the current
runner — no new execution path. Human-in-the-loop review reuses the existing
review UI. Unmapped acceptance criteria are flagged as coverage gaps, not
silently dropped.

## L4 — Reliable Runner  (extend current)

Already have: checkpoints (continue-after-failure), per-run traces, screenshots,
videos, and now self-healing + heal reports. Add: **flake quarantine** (retry a
failed test once in isolation; if it then passes, label `FLAKY` not `FAIL` and
tag the locator/step that healed or timed out) and a per-test health score over
run history (you already persist history + checkpoints + heals).

## L5 — Maintenance Loop  (sellable differentiator)

Consume `heals/` and flake data across runs: when a locator heals consistently,
open an auto-repair PR rewriting the primary locator (L0 already knows the
winning strategy); when coverage drifts from the PRD, flag the missing
requirements. This is the "it fixes its own tests" promise.

---

## Build order (each ships value alone)

1. **L0 self-healing** ✅ — reliability today; adopt `heal(...)` in the highest-churn flows.
2. **L1 App Explorer** — `app_model.json`; immediately useful as a flow/coverage map.
3. **L3 synth from a hand-written `requirements.json`** — prove app_model → runnable healing tests before automating L2.
4. **L2 PRD extractor** — close the loop to "just point it at the PRD."
5. **L4 flake quarantine + L5 auto-repair** — the maintenance story that justifies the price tag.

**Open decision (needed before L1–L3):** where the LLM runs — Claude API key in
config (fastest), or a pluggable provider for on-prem buyers. Affects security
posture and pricing.
