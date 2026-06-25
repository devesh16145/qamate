# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `main`  | Yes       |

## Reporting a vulnerability

If you discover a security issue in QAmate, please **do not** open a public GitHub issue.

Email the maintainers with:

- Description of the issue and impact
- Steps to reproduce
- Affected version / commit (if known)

We will acknowledge within a few business days and work on a fix before public disclosure when appropriate.

## Secrets and local configuration

**Never commit `config.json`.** It may contain:

- Application login passwords
- Jira API tokens
- Other integration credentials

Use `config.example.json` as the template. Prefer environment variables for API keys (`ATS_ANTHROPIC_KEY`, `ATS_OPENAI_KEY`, `JIRA_API_KEY`).

## If credentials were ever committed

If `config.json` or other secrets were pushed to a remote:

1. **Rotate every exposed credential immediately** (Jira API token, app passwords, LLM keys).
2. Remove secrets from git history (see [docs/PUBLISHING.md](docs/PUBLISHING.md)).
3. Force-push the rewritten history only after rotation.

Removing a file from the latest commit does **not** revoke tokens that were already exposed in history or on GitHub.
