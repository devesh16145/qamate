# MiMo 2.6 Flash comparison — 2026-09-22

The saved `mimo` profile and Python fallback default now use `mimo-v2.6-flash`, confirmed by Xiaomi's official release documentation: https://mimo.mi.com/docs/en-US/news/latest/v2-6 . The existing subscription endpoint, environment-variable credential, request parameters and token limits are unchanged. Jev remains `typesafe/jev-1.13`. Explicit custom model selections, the separate 2.5 and Ultraspeed profiles, and other providers are preserved. Restart an existing agent session to pick up the new profile.

## Method

Real planner authoring on the isolated two-app `validation` task: empty-form error, three provided field values, generated-ID capture, correlated checks and approval in the second app, and propagation back to the first. No prepared workflow or selectors are supplied. Both models use a 420-second authoring deadline, 50-tool budget, 4096-token request cap, the same subscription endpoint and Jev profile. The task hash is `dfc2f8b27ec253aadfd0d48018859d0539dbd7666174b3cbc2ccb169491d4271`.

Success requires completed authoring, self-verification, semantic workflow audit, two independent fresh-ID passes, and failure of both wrong-record and missing-propagation controls. Failed controls are expected and necessary. Reported tokens include planner input/output and Jev input/output; they are not dollar cost or unique context size.

```powershell
.\venv\Scripts\python.exe engine/bench_multi_app.py --provider mimo --task validation --timeout 420 --tool-budget 50
.\venv\Scripts\python.exe engine/bench_multi_app.py --provider mimo --planner-model mimo-v2.5-pro --task validation --timeout 420 --tool-budget 50
```

The optional `--planner-model` changes only the isolated benchmark copy, not saved settings. Runs are sequential. The short 27-test offline check overlapped the opening part of the Flash run; no browser regression suite ran concurrently.

## Flash result

Artifacts: `results/_multi_app_author/20260922-110547-b36d4ad0/`.

- Verdict: `reliable_hybrid`; authoring and semantic audit passed.
- Authoring wall time: 159.9 seconds; total with independent checks: 242.41 seconds.
- Planner: 14 requests, 14 tool calls, 83,636 reported tokens (78,853 input + 4,783 output).
- Jev: 6 requests, 8,249 tokens, 7,360 ms summed decision duration.
- Combined reported tokens: 91,885; no missing usage.
- Fresh replays: both passed (12.17s and 13.20s). Missing propagation and wrong record correctly failed (21.87s and 20.82s).

The earlier 2.5-Pro run `20260922-001201-4f07b954` passed the same contract but used 288.0 seconds authoring and 143,378 combined tokens. That historical run overlapped a full regression suite, so it is not the latency baseline below.

## Fresh comparison

Fresh 2.5-Pro artifacts: `results/_multi_app_author/20260922-111026-f90ba939/`.

| Metric | 2.5 Pro | 2.6 Flash |
|---|---:|---:|
| Authoring wall time | 267.0s | 159.9s |
| Total including independent checks | 353.95s | 242.41s |
| Planner requests | 18 | 14 |
| Tool calls | 34 | 14 |
| Planner tokens | 148,249 | 83,636 |
| Jev requests | 8 | 6 |
| Jev tokens | 11,211 | 8,249 |
| Combined tokens | 159,460 | 91,885 |
| Independent fresh-ID passes | 2/2 | 2/2 |
| Broken outcomes correctly rejected | 2/2 | 2/2 |
| Controller fallbacks | 0 | 0 |

Both verdicts were `reliable_hybrid`, with complete usage reporting. Flash reduced authoring wall time by **40.1%**, combined reported tokens by **42.4%**, and total time including checks by **31.5%**. This is less elapsed time, not a claim about isolated API decoding throughput. The 2.5 run made 17 update_plan calls plus set_plan; Flash made none. The older model also encountered two no-action Jev handoffs before successful recovery. Those observed orchestration differences contributed to the comparison; it was not simply a faster Jev service (summed Jev durations were 6,905ms for 2.5 versus 7,360ms for Flash).

The baseline fresh replays passed in 14.02s and 12.95s; missing propagation and wrong record correctly failed in 21.19s and 23.13s. Runtime and fixture hashes match between runs. Only the benchmark entrypoint hash differs: the isolated `--planner-model` override was added after starting Flash so the baseline could run without reverting the user's saved model. No prompt, controller, fixture, audit or verification gate changed between these runs.

Conclusion: retain 2.6 Flash as the requested MiMo default. This first measurement supports better task-level efficiency with the same observed verification outcome, not improved general accuracy or completion of the wider Qamate goal.

## Limits

One task/run per model cannot establish broad accuracy, latency distributions or production readiness. This synthetic multi-app result is not complex public-app exploration evidence. Cache warmth, sequential run order, network/provider load and machine load are uncontrolled. The original reliability and broader evaluation gates remain in force.

Focused model-profile and benchmark tests: 27 passed. The profile regression checks the new default and preserves explicit user-selected model overrides. No UI code changed.
