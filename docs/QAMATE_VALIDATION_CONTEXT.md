# Validation context for planner and decision execution

21 September 2026. The negative-login traces repeatedly showed unnecessary error-dismissal goals. Previously the planner could see validation text, but the bounded decision payload dropped it and neither surface reported whether a target was covered.

## Change

`interaction_context.py` adds visible dialog/modal counts and DOM-grounded, center-point pointer-hit evidence for observed controls. Offscreen centers are unknown rather than treated as covered. Inert ancestry is reported. Probe failures are explicitly unknown, not evidence of an unobstructed page. This is a hint, not a replacement for Playwright actionability, permissions, freshness checks or user authorization.

The planner and replaceable decision adapter now both receive validation messages plus that context. Instructions distinguish expected negative-test errors from unexpected authentication failures and do not assume that an inline message must be dismissed before further authorized input. The existing confidence threshold is unchanged; there is no forced Jev choice or automatic dismissal.

Local-browser controls cover an inline alert with reachable input, a labeled modal covering the input, and an unlabeled overlay covering the same input. Pure tests cover failed probes and the decision payload projection. The provider-neutral agent-design skill guided adding observed state rather than site-specific rules.

## Limits

Center-point hit testing is incomplete actionability evidence: an unobscured center does not prove a future click will succeed, and a covered center may not mean every part of a control is inaccessible. Framework-specific dialogs, frames and transient transitions still need existing browser checks and handoff handling. No screenshot/video interpretation or multi-app execution is added by this change.

## Verification and live result

Local gate: **315 tests passed**, including 14 local-browser tests; both agent/evaluator self-tests and `git diff --check` passed. The existing Pydantic AI `prepare_tools` deprecation warning remains a compatibility migration item.

Live evidence: `results/_public_pilot/20260921-001806-1af5037a/`. Same version-1 negative-login contract, MiMo `mimo-v2.5-pro`, Jev `typesafe/jev-1.13`, 240-second authoring deadline, 48-tool cap and two independent replays. No forced hybrid prompt, threshold reduction or assertion removal.

- **Delivered and passed two authoring verification runs plus two independent replays.**
- 12 planner requests, 16 tool calls and five Jev decisions across three successful goal groups. No controller handoff or fallback; no recording clear/restart.
- Provider-reported usage: 101,557 planner tokens + 4,936 Jev tokens = **106,493 combined tokens**. Jev requests took 5,249 ms in total.
- Agent turn completed at 118.34 seconds from process start; end-to-end including independent replay was **146.83 seconds**.
- Manual code inspection confirms the missing-username assertion immediately after the empty submission; missing-password assertion after username-only submission; then successful inventory URL and Products assertions after filling the password.
- Two late attempts to re-add the old error assertions after login were rejected by the record-time truth gate. The correct earlier assertions were already recorded. There are also duplicate successful final checks and an extra leading navigation. Do not present this as zero failed tools or fully optimized output.

This is the first successful live negative-login run after several retained timeouts. It supports the usefulness of the changed context/instructions on this task, not statistically established reliability or a causal estimate separating each change. Earlier timeout runs lack complete planner usage, so a token-saving percentage against them would be misleading. Repeated matched runs and realistic business-app exploration remain necessary.
