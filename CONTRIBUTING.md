# Contributing to QAmate

Thanks for helping improve QAmate.

## Development setup

1. Clone the repository.
2. Copy `config.example.json` → `config.json` and configure your app URLs (or use the demo flow against playwright.dev).
3. Create the Python venv and install dependencies (see [SETUP.md](SETUP.md)).
4. Install Playwright browsers: `venv\Scripts\playwright install chromium`
5. Launch the IDE: `npx electron .` from the repo root (or `run_qamate.bat` from the parent folder).

## Quality gates

Before opening a PR:

```bat
run_engine_tests.bat
venv\Scripts\pytest tests\flows\demo\ -v
```

Engine changes must pass `engine/tests/` (no browser, no LLM). UI or agent changes should include updates to the offline suite when behavior is testable without a live app.

## Pull request guidelines

- One logical change per PR when possible.
- Do not commit `config.json`, `projects/`, `results/`, `.auth/`, or private test flows.
- Match existing code style in the file you edit.
- Update `README.md` or `SETUP.md` if setup or public behavior changes.

## Private / product-specific tests

Product-specific flows (e.g. internal app suites) belong in **gitignored** folders under `tests/flows/` or in `projects/<id>/tests/`. The public repo ships only `tests/flows/demo/`.

## Agent and playbook changes

Changes to `engine/agent_playbook.md` or `engine/normalizer.py` affect every agent session. Run `run_engine_tests.bat` and, for agent-surface changes, consider `engine/agent_bench.py --only BENCH-001` against a reachable demo environment.
