# QAmate — Setup

## First run

1. Install dependencies (see [README](README.md#install)).
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

## AI agent (BYOK)

QAmate is **bring-your-own-key** for cloud LLMs:

1. Open **Settings → AI / LLM**
2. Pick a **default provider** (`mock` works offline with no key)
3. For Anthropic, OpenAI, or other cloud providers: paste your API key and click **Save**

Keys are encrypted with your OS keychain (`safeStorage`) and stored in `%APPDATA%/qamate/ats_secrets.json` (or the Electron app userData path). They are **never** written to `config.json` or committed to git.

Alternatively, set the env var named in `config.example.json` (e.g. `ATS_ANTHROPIC_KEY`, `ATS_OPENAI_KEY`) before launching QAmate.

The agent UI blocks session start if the selected provider needs a key that is not configured. You can override the default per session from the provider dropdown in the agent panel.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `ATS_ANTHROPIC_KEY` | Anthropic API key for the AI agent |
| `ATS_OPENAI_KEY` | OpenAI API key |
| `JIRA_API_KEY` | Jira API token (overrides config) |
| `ATS_ENV` | `dev` or `staging` |
| `ATS_SELLER_USER_INDEX` | Seller account index in config |
| `ATS_ADMIN_USER_INDEX` | Admin account index in config |
