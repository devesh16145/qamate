# QAmate

**AI-assisted E2E testing** — an Electron desktop IDE with self-healing Playwright tests, checkpoint-based flows, and an autonomous agent that authors and verifies suites in a live browser.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

<img width="1919" height="1004" alt="image" src="https://github.com/user-attachments/assets/4a327530-59bc-4847-a0c1-1e28836d7017" />

<img width="1919" height="1003" alt="image" src="https://github.com/user-attachments/assets/29e39d79-e0dd-44fe-bb75-3ac9a9f5673f" />

<img width="1919" height="1008" alt="image" src="https://github.com/user-attachments/assets/a123fb3b-7abd-41c7-ac71-25523f693d78" />


## Why QAmate

Manual test maintenance breaks when the UI drifts. QAmate combines:

- **Record & run** — Playwright-backed tests with video, traces, network capture, and JUnit results
- **Self-healing locators** — three-tier recovery when selectors stop matching
- **AI agent** — drives your app, writes tests, and re-runs until they pass (verify-and-fix loop)
- **Projects** — per-app URLs, credentials, context files, and optional private suites
- **BYOK** — bring your own LLM key; secrets stay in the OS keychain, never in git

The public repo ships a **demo suite** against [playwright.dev](https://playwright.dev/) — no login, no proprietary app required.

---

## Quick start

### Prerequisites

- **Node.js** 18+ (for Electron)
- **Python** 3.11+ (3.12 recommended)
- **Windows** is the primary target; core engine is cross-platform Python

### Install

```bat
git clone https://github.com/devesh16145/ATS.git
cd ATS

python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\playwright install chromium

npm install
```

On first launch, QAmate copies `config.example.json` → `config.json` if missing.

### Run the app

```bat
run_qamate.bat
```

Or:

```bat
npx electron .
```

### Run the demo tests

```bat
venv\Scripts\pytest tests\flows\demo\ -v
```

### AI agent (optional)

1. Open **Settings → AI / LLM**
2. Default provider is **`mock`** (offline, no API key)
3. For Claude or OpenAI: paste your key and click **Save**, or set `ATS_ANTHROPIC_KEY` / `ATS_OPENAI_KEY` in your environment

See [SETUP.md](SETUP.md) for full configuration.

---

## Features

| Area | What you get |
|------|----------------|
| **Test IDE** | Flow tree, recorder, coverage view, results + history, Jira bug/story export |
| **Checkpoints** | Milestone-based tests that keep running after a failure |
| **Self-heal** | Primary → fallback → fingerprint scan; heals logged per run |
| **Agent** | GUIDED / AUTO modes, context folder, session persistence, autonomous flow authoring |
| **Engine quality** | ~218 offline unit tests — `run_engine_tests.bat` |

---

## Project layout

```
main.js             Electron main process + IPC
engine/             Python runner, agent, self-heal, normalizer
src/                IDE + agent UI (React via babel-standalone)
tests/flows/demo/   Public demo tests
projects/           Per-project suites (gitignored when local)
config.example.json Tracked template — copy to config.json locally
```

Add your own flows under `tests/flows/<module>/` or `projects/<id>/tests/`. See [tests/flows/README.md](tests/flows/README.md).

---

## Architecture

Layered autonomous pipeline (PRD → explore → synthesize → self-heal): [AUTONOMOUS_TESTING.md](AUTONOMOUS_TESTING.md).

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run `run_engine_tests.bat` before opening a PR.

## Security

**Never commit `config.json`.** Keys belong in Settings or environment variables. See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) — Copyright (c) QAmate contributors.
