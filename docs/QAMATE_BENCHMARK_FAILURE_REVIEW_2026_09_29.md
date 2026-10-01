# Frozen benchmark failure review

Baseline: `results/_frozen_bench/20260929-021759-26552383/scorecard.json`.
All 30 scheduled attempts completed on stable source: 11 reliable, 19 unsuccessful.
The baseline is immutable; fixes below do not upgrade historical verdicts.

## Confirmed recorder defects

1. Both exported ops-validation failures expected numeric value `7` but replay
   produced `07`. Live sequential fill selects existing text before typing;
   the exporter omitted selection. The exporter now clicks and selects all on
   the grounded locator before sequential typing. A browser regression executes
   emitted code against initial values `0`, `123`, and empty text.
2. All three ops-allocation exports dropped the second and third consecutive
   Next page clicks. The recorder deduplicated identical click source strings,
   so replay remained on page two and timed out finding Batch 024. Removed this
   semantic assumption for reviewed actions. A browser regression executes all
   three emitted clicks and verifies page four.

The source audit recognizes the emitted select-all as an action, never as
readback evidence. Negative coverage checks still require the numeric assertion.
No historical exported test or scorecard was edited.

## Remaining failure classes

- Read-only CRM search stopped on either explicit Jev stop or confidence gate.
- Operations transfer sometimes mutated an already-ready form instead of
  committing milestone assertions, then stopped during confirmation.
- Some compiler contracts used incorrect field-value outcomes or bindings.
- The CRM Size case retains its genuine application defect; not every failed
  attempt reached that defect, so all three cannot be labelled app failures.
- Negative-login coverage and decision failures remain unresolved.
- Held-out sort-detail stopped on confirmation or changing page state.

Opening held-out failure traces for diagnosis consumes that holdout. Future
generalization claims require a newly reserved workflow set, not relabelling
these tuned tasks as unseen.

## Validation results

- `results/_public_pilot/20260929-090844-1c6ae651`: operations-validation
  failed before export on uncertain premature submission with required inputs
  pending. No dispatch on failed confirmation; retained as failure.
- `results/_public_pilot/20260929-091209-a1ae7d0b`: fresh allocation author
  passed coverage, self-verification and **2/2 independent replays**. Export
  preserves all three pagination clicks and Page 4 readback. One development
  success is not a repeated-cohort or full-suite acceptance claim.
- Full engine/native suite after recorder fixes: **543 passed**, 24 warnings.
  Both module selftests passed. Later ready-milestone guard: **30 focused tests
  passed**; later native-select candidate fix: **26 controller tests passed**.
  Counts overlap and are not additive; full suite predates those two guards.

## Additional controller fixes

Ready milestones now offer check/wait/stop only. Jev still chooses whether to
commit; the harness does not auto-advance. This prevents further edits or save
from destroying mandatory pre-save readbacks before they are recorded.

Native select elements no longer offer a redundant click action. Existing DOM
options already permit typed selection; opening native browser chrome adds no
observable DOM choice. This addresses the inspected sort-detail traces in which
Jev repeatedly chose click instead of the available typed select.

Fresh sort-detail `20260929-091807-a7d55965` selected and read back `za`,
then stopped on the mandatory shirt description. Independent direct Playwright
inspection on September 29 returned: `Super-soft and comfy ringspun combed cotton.`
The v1 benchmark incorrectly required `ring-spun`. Task v2 corrects the spelling
in both prompt and required outcome; the assertion is not removed. Original
artifacts remain unchanged. This direct inspection is diagnostic, not agent proof.
