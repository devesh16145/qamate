# QAmate — Setup

## First run

1. Install dependencies (see root `run_ats.bat` or README).
2. On first launch, QAmate copies `config.example.json` → `config.json` if `config.json` is missing.
3. Open **Settings** and configure:
   - Platform URLs and test users
   - LLM provider (default is `mock` for offline use)
   - Jira (optional) — prefer `JIRA_API_KEY` env var over storing tokens in config

## Important

- **`config.json` is gitignored** — never commit it. It may contain passwords and API tokens.
- **`config.example.json`** is the tracked template with no secrets.
- If you previously committed secrets, **rotate** any exposed Jira tokens and passwords before publishing. See [docs/PUBLISHING.md](docs/PUBLISHING.md).

## Test suites

The public repo ships **`tests/flows/demo/`** — three tests against [playwright.dev](https://playwright.dev/) (no login).

Product-specific flows (catalog, orders, admin, etc.) are **gitignored**. Clone the repo and add your own flows under `tests/flows/`, or use **Projects** (`projects/<id>/tests/`) for per-app suites. See `tests/flows/README.md`.

```bat
venv\Scripts\pytest tests\flows\demo\ -v
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `ATS_ANTHROPIC_KEY` | Anthropic API key for the AI agent |
| `ATS_OPENAI_KEY` | OpenAI API key |
| `JIRA_API_KEY` | Jira API token (overrides config) |
| `ATS_ENV` | `dev` or `staging` |
| `ATS_SELLER_USER_INDEX` | Seller account index in config |
| `ATS_ADMIN_USER_INDEX` | Admin account index in config |
