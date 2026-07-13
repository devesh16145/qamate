# Publishing QAmate (open source checklist)

Use this after Phases 1–3 (secrets out of the tree, demo suite, rebranding).

## 1. Rotate exposed credentials

If `config.json` was ever committed or pushed, **rotate before publishing**:

- [ ] Jira API token (`config.json` → `jira.apiToken` or `JIRA_API_KEY`)
- [ ] Seller / admin passwords in `config.json`
- [ ] Any LLM API keys that were stored in config or chat logs

Git history scrubbing does not invalidate tokens that were already public.

## 2. Commit the open-source tree

Ensure the working tree has:

- `config.example.json` (tracked), `config.json` (gitignored)
- `tests/flows/demo/` only (private flows gitignored)
- `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `README.md`

```bat
git add -A
git status
git commit -m "Prepare QAmate for open source release"
```

## 3. Scrub secrets from git history

`config.json` appears in older commits. Remove it from **all** history:

### Option A — git-filter-repo (recommended)

```bat
pip install git-filter-repo
git filter-repo --path config.json --invert-paths --force
git remote add origin https://github.com/YOUR_ORG/qamate.git
```

### Option B — bundled script (Windows)

From repo root:

```bat
scripts\scrub-git-history.bat
```

Then re-add your remote:

```bat
git remote add origin https://github.com/devesh16145/qamate.git
```

## 4. Force-push (only after rotation)

```bat
git push --force-with-lease origin main
```

Warn collaborators: everyone must re-clone or reset after a history rewrite.

## 5. GitHub repository settings

- [ ] Rename repo to `qamate` (optional)
- [ ] Set description + topics: `playwright`, `e2e`, `testing`, `electron`, `ai-agent`
- [ ] Enable **Private vulnerability reporting** (Settings → Security)
- [ ] Add LICENSE file recognition (GitHub detects `LICENSE` automatically)
- [ ] Disable wiki if unused; protect `main` branch if desired

## 6. First public release

```bat
git tag -a v0.1.0 -m "QAmate initial open-source release"
git push origin v0.1.0
```

Create a GitHub Release from the tag with notes: demo suite, agent, self-healing locators, setup link to `SETUP.md`.
