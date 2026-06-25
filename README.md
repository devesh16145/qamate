# QAmate

AI-assisted E2E testing desktop app with self-healing locators, checkpoint-based tests, and an autonomous agent that authors and verifies Playwright suites.

## Features

- **Electron IDE** — browse flows, record tests, run suites, review artifacts, Jira integration
- **Self-healing locators** — three-tier recovery when the UI drifts
- **AI agent** — drives a live browser, authors tests, verify-and-fix loop
- **Projects** — per-app URLs, auth, context files, and optional private test suites
- **Demo suite** — `tests/flows/demo/` against [playwright.dev](https://playwright.dev/) (no login)

## Quick start

See [SETUP.md](SETUP.md).

```bat
npx electron .
venv\Scripts\pytest tests\flows\demo\ -v
run_engine_tests.bat
```

From the parent workspace folder you can also use `run_qamate.bat`.

## Architecture

See [AUTONOMOUS_TESTING.md](AUTONOMOUS_TESTING.md) for the layered pipeline (L0–L5).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

See [SECURITY.md](SECURITY.md). **Do not commit `config.json`.**

## License

[MIT](LICENSE)

## Maintainers — publishing

See [docs/PUBLISHING.md](docs/PUBLISHING.md) for history scrub and GitHub release checklist.
