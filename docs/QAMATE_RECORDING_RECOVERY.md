# Recoverable agent recordings

20 September 2026. This addresses the negative-login attempts that collected the required outcomes and then discarded their recording before delivery.

## Behavior

- `clear_recording()` without a specific reason refuses to clear a recording that already contains checkpoints. It returns `recording_protected` and recommends creating and replaying the test first.
- A deliberate restart requires a reason of at least 20 characters when checkpoints exist. This is a friction/intent check, not an automatic proof that the reason is justified. A caller can still restart for a concrete defect or a new scenario.
- Before a permitted clear, the harness deep-copies steps, assertions, step/input counters, assumptions and skips into a session-local snapshot. It retains the latest five nonempty snapshots and explicitly reports the evicted snapshot ID if that limit is reached.
- `get_recorded_flow` lists snapshot IDs and counts. `restore_recording(snapshot_id)` restores the recording and archives displaced work. Unknown IDs do not change anything. Summary metadata does not expose raw recorded input values.
- Restore does **not** rewind the browser, authentication, page refs, plan or verification state. The agent must observe before further browser actions and must generate/replay before claiming test success.
- Snapshots are in memory for the live session only. They do not survive process exit, timeout termination or an app restart. This change deliberately does not introduce another plaintext credential-bearing recording file. Durable, secure recovery remains future work.

The hybrid prompt and recorded-flow guidance now distinguish intentional negative-test validation from unexpected authentication failure, and discourage cosmetic re-recording after the requested outcome assertions exist. The original provider-independent execution, provenance gates and replay requirements remain intact.

## Local verification

309 existing/new regression tests passed, followed by an additional local-browser restore/replay test (310 distinct checks total). Tests cover protected clears, deep-copy isolation, full recording/provenance restoration, preservation of displaced work, unknown IDs, bounded retention, redacted snapshot summaries, registered tool wiring, and replay of a restored native-select assertion on a fresh document.

The agent-design/evaluation skill informed bounded undo, explicit recovery semantics and independent replay verification. These checks do not prove live model reliability; the bounded negative-login retry is reported separately below.

## Live retry: still an open failure

Historical result below. The later [validation-context slice](QAMATE_VALIDATION_CONTEXT.md) produced the first independently passing negative-login test; it did not rely on a live clear/restore operation, so recording recovery remains locally verified rather than credited as the cause of that success.

`results/_public_pilot/20260920-194118-1563016c/` used the unchanged negative-login task, MiMo planner, Jev decision profile, 240-second authoring deadline and 48-tool budget. It timed out after 247.6 seconds including startup, with 29 planner requests, 32 tool calls, 11 Jev requests and no generated test. Independent replay therefore did not run. Planner token usage is unknown after timeout; Jev reported 8,144 tokens.

The trace contains zero clear/restore calls, so this attempt does **not** demonstrate the protection or restore tools being used live. Those are verified by the local tests. The agent repeatedly attempted to dismiss inline validation errors, received uncertainty handoffs, navigated back to restart the form, and eventually fell back to regular tools. Expected missing-username/password outcomes were observed, but the flow was not delivered. All prior timeouts remain in the benchmark history.

The current evidence supports recoverable session-local recordings, not reliable negative-login authoring. Next work should improve semantic observations of inline validation versus blocking UI, and goal sequencing after expected errors. Do not claim that recording protection alone fixed the benchmark, or lower the confidence threshold merely to force completion.
