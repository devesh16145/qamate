# QAmate — Setup

## First run

1. Install dependencies (see [README](README.md#install)).
2. On first launch, QAmate copies `config.example.json` → `config.json` if `config.json` is missing.
3. Open **Settings** and configure:
   - Platform URLs and test users
   - AI provider (Settings → AI / LLM; required for the AI agent)
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

1. Open **Settings → AI / LLM** and click **Add provider**. Presets (defined in `engine/provider_catalog.json`): Xiaomi MiMo, Anthropic Claude, OpenAI, Google Gemini, OpenRouter, DeepSeek, Ollama (local), and **Custom** for any OpenAI-compatible endpoint
2. Paste your API key, pick a model (the list fills in from the provider after a successful test), and click **Save key & test**. For Xiaomi MiMo, Token Plan (`tp-`) keys are matched to their region automatically
3. Optionally set the thinking mode and token cap, then click **Save changes**. The first provider you add becomes the default

Keys are encrypted with your OS keychain (`safeStorage`) and stored in the Electron userData folder (`ats_secrets.json`; `%APPDATA%/qamate` on Windows, `~/Library/Application Support/qamate` on macOS). They are **never** written to `config.json` or committed to git.

For CLI / CI runs outside the app, set the provider's key environment variable instead (shown under **Advanced**, e.g. `MIMO_API_KEY`, `ATS_ANTHROPIC_KEY`, `ATS_OPENAI_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`).

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
