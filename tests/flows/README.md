# Test flows

Each subdirectory is a **flow** (module) discovered automatically by the QAmate UI and pytest runner.

## Public example (shipped in repo)

| Flow | Description |
|------|-------------|
| `demo/` | Three checkpoint tests against [playwright.dev](https://playwright.dev/) — no login required |

Run the demo suite:

```bat
venv\Scripts\pytest tests\flows\demo\ -v
venv\Scripts\pytest tests\ -v -k "TC_DEMO"
```

## Private / product-specific suites

Product-specific flows (e.g. catalog, orders, admin) are **gitignored** and kept only on your machine. To add a new flow:

1. Create `tests/flows/<flow_id>/`
2. Add `__init__.py`, `test_cases.json`, `test_<flow_id>.py`
3. Optionally `test_data.json` and `*_user_stories.json`

TC function names use underscores: `test_TC_CATALOG_001_...` (pytest `-k` uses `TC_CATALOG_001`).

## Platform fixtures

| Fixture | Use when |
|---------|----------|
| `page` | No login (public pages, demo flow) |
| `seller_page` | Logged-in seller/app platform from `config.json` |
| `admin_page` | Logged-in admin platform (separate browser context) |
