# AGENTS.md

## Project continuity

Read [MEMORY.md](MEMORY.md) before continuing Qamate work. It consolidates the
project's goals, decisions, evidence, limitations, and remaining acceptance work.
Follow its links to the acceptance ledger before reporting progress or completion.
Historical results are version-specific; do not treat green development pilots
as proof of unseen-site reliability.

The Cursor Cloud notes below are environment-specific historical setup guidance.
Detect the current host before choosing paths or commands; this repository is
also developed on Windows. Use the memory's dated ledger for validation counts.

## Cursor Cloud specific instructions

QAmate is an **Electron desktop IDE** (`main.js`, `preload.js`, `src/`) that drives a
cross-platform **Python engine** (`engine/`) for AI-assisted E2E Playwright testing.
The repo's docs (`README.md`, `SETUP.md`, `CONTRIBUTING.md`) assume **Windows**
(`venv\Scripts\...`, the `.bat` files). This VM is **Linux** — translate paths as below.

### Environment (already provisioned by the update script)
- Python deps live in a local venv. Use `venv/bin/python` / `venv/bin/pytest`
  (the Linux equivalent of the docs' `venv\Scripts\...`).
- Node deps are in `node_modules/` (`npm install`).
- On first run the app copies `config.example.json` → `config.json` (gitignored). Safe to delete to reset.

### Lint / test / run
- **Lint:** no linter is configured (no ESLint/ruff/flake8). The project's quality gate is the engine test suite.
- **Engine tests (fast, offline — no browser/LLM/app):** the `.bat` files are Windows-only; run directly:
  - `venv/bin/python engine/agent_chat.py --selftest`
  - `venv/bin/python engine/agent_eval.py --selftest`
  - `venv/bin/python -m pytest engine/tests`  (~218 tests, <10s)
- **Demo E2E flow (core product — drives a real browser against playwright.dev):**
  - `venv/bin/python -m pytest tests/flows/demo/ -v`
  - Runs headless from the CLI; needs internet. `TC-DEMO-001/002` pass.
    `TC-DEMO-003` currently **fails due to live-site drift** (playwright.dev's docs
    search is now a "Search / Ctrl+K" DocSearch button, not an `input[type=search]`) —
    this is a pre-existing test brittleness, not an environment problem.
  - Artifacts (videos/traces/checkpoints/screenshots) are written to `tests/results/manual/`.
- **Electron IDE (GUI):** a display is available at `:1`.
  - `DISPLAY=:1 npx electron . --no-sandbox`
  - The app **auto-opens Chrome DevTools** and quits when its **last window closes**
    (`window-all-closed`), so don't close the main window if you want it to stay up.
  - The `Failed to connect to the bus` / GPU-init log lines at launch are benign in headless.

### Non-obvious gotcha: IDE Python backend is Windows-only
`main.js` hardcodes `VENV_PYTHON = venv/Scripts/python.exe` (a Windows path, no platform
branch). On Linux the IDE UI renders fine, but its in-app **Run / AI-Agent / recorder**
features can't spawn Python and report `Python not found at .../venv/Scripts/python.exe`.
Windows is the documented primary target; on Linux exercise the engine via the **CLI**
commands above instead of the IDE Run button.
