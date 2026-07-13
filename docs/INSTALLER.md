# QAmate — Windows Installer

How QAmate is packaged into a Windows `.exe`, how the first-run environment
bootstrap works, and how to publish the installer to the website / GitHub
Releases.

---

## 1. Packaging approach

QAmate is an **Electron desktop app driving an embedded Python engine**
(Playwright, `pydantic-ai`, pytest). That combination makes packaging
non-trivial: the app is JS, but every test run and agent session spawns
`venv/Scripts/python.exe` and reads engine `.py` files **from disk**.

We use **Option A (hybrid)** — an **electron-builder NSIS installer** that ships
the app, plus a **first-run bootstrap** that provisions the Python environment on
the user's machine:

| Concern | Decision | Why |
|---|---|---|
| Installer | **electron-builder → NSIS** (`.exe`) | Native, well-supported, per-user install, auto-uninstaller. |
| Install scope | **Per-user** (`perMachine: false`) → `%LOCALAPPDATA%\Programs\QAmate` | The app dir stays **user-writable**, so `venv/`, `config.json`, `results/`, `projects/` all work in place — no data-dir refactor, no admin rights. |
| `asar` | **Disabled** (`asar: false`) | Python must read `engine/*.py` from disk and we create `venv/` next to them; both are impossible inside an `asar` archive. |
| Python venv | **Created on first run** at `<app>/venv` | A venv is ~500 MB with Chromium, is **not relocatable**, and is platform/patch-specific — bundling one is fragile. Creating it locally is robust and mirrors the dev layout exactly. |
| Base Python | **Bundled `python-build-standalone`** (preferred) → else **system Python 3.11+** | The bundled relocatable runtime makes a **clean-machine, no-Python** install possible; the system-Python fallback keeps the build lightweight when you don't stage it. |
| Playwright Chromium | Installed on first run (`playwright install chromium`) into the shared user cache (`%LOCALAPPDATA%\ms-playwright`) | Standard Playwright location; shared across apps; avoids bloating the installer. |

Because the venv is created next to the app (identical to the dev checkout),
`main.js` keeps using `venv/Scripts/python.exe` **unchanged** — nothing in the
engine/agent spawn paths had to move.

### What ships in the installer

Included (`build.files` in `package.json`): `main.js`, `preload.js`,
`bootstrap.js`, `engine/**`, `src/**`, `assets/**`, `requirements.txt`,
`config.example.json`, run scripts, and the public `tests/flows/demo/` suite.

Excluded: `config.json` (secrets), `venv/`, `results/`, `projects/`,
`templates/`, `engine/tests/**`, and the private product flows (already
git-ignored). See the `!` negations in `build.files`.

---

## 2. First-run bootstrap (`bootstrap.js`)

On launch, `main.js` calls `bootstrap.pythonReady()`. If the environment is not
ready it runs `bootstrap.runFirstRunSetup()` behind a branded splash
(`src/setup.html`) **before** opening the IDE:

1. **Locate a base interpreter** — bundled `resources/pybundle/python/python.exe`
   first, otherwise a system `py -3.12` / `py -3` / `python` / `python3` (≥ 3.11).
2. **Create the venv** — `<base> -m venv <app>/venv`.
3. **Upgrade** `pip` + `wheel`.
4. **Install deps** — `pip install -r requirements.txt`.
5. **Install Chromium** — `python -m playwright install chromium`.
6. **Stamp** — write `venv/.qamate-env.json` (`{ version, complete: true }`).

`pythonReady()` is idempotent and dev-safe:
- A completed bootstrap is detected via the stamp.
- A hand-made **developer venv** (no stamp) is treated as ready if Playwright is
  already installed, so a dev checkout never triggers the splash.
- A partial/failed venv re-runs the (idempotent) bootstrap next launch.

If **no** Python can be found, the app still opens and shows a clear message
telling the user to install Python 3.12 (or the maintainer to ship the bundled
runtime). Bump `ENV_VERSION` in `bootstrap.js` when `requirements.txt` changes to
force a re-provision.

---

## 3. Building the installer

### Prerequisites
- Windows 10/11 x64
- Node 18+ (`npm install` pulls in `electron`, `electron-builder`, and the icon
  tooling `sharp` + `png-to-ico`)

### One-shot build

```bat
scripts\build-installer.bat            REM system-Python fallback build
scripts\build-installer.bat --offline  REM also bundle a Python runtime (clean-machine install)
```

### Manual / step-by-step

```bat
npm install
npm run icon                REM assets\branding\qamate-mark.ico from the brand SVG
npm run prepare:python      REM OPTIONAL: stage bundled Python (offline install)
npm run dist                REM electron-builder --win  -> dist\QAmate-Setup-<version>.exe
```

Quick sanity check without producing an installer (fast, unpacked):

```bat
npm run dist:dir            REM electron-builder --win --dir -> dist\win-unpacked\
```

**Output:** `dist\QAmate-Setup-<version>.exe`

### The app icon

`win.icon` points at `assets/branding/qamate-mark.ico`. It is generated from
`assets/branding/qamate-mark.svg` by `npm run icon`
(`scripts/generate-icon.js`, via `sharp` + `png-to-ico`; sizes 16–256). The
`.ico` is committed so CI/builds don't hard-depend on `sharp`. Regenerate it
whenever the brand mark changes. If `sharp` can't install in your environment,
create the `.ico` manually (e.g. an online SVG→ICO converter at ≥256 px) and drop
it at that path.

### Offline (no-Python) install

`npm run prepare:python` (`scripts/prepare-bundle.ps1`) downloads a
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
"install_only" runtime into `build/pybundle/python/`. electron-builder bundles it
as `resources/pybundle/python`, and the bootstrap uses it as the venv base — so
the app installs and runs on a machine with **no Python at all**. Skip this step
for a smaller installer that relies on a system Python 3.11+.

---

## 4. Known blockers / caveats

- **Code signing** — the build is **unsigned**, so Windows SmartScreen shows a
  warning on first run (users click *More info → Run anyway*). To sign, add an
  Authenticode cert and set electron-builder's `win.certificateFile` /
  `certificatePassword` (or `CSC_LINK` / `CSC_KEY_PASSWORD` env vars in CI).
- **`.ico` requirement** — packaging fails if `assets/branding/qamate-mark.ico`
  is missing. Run `npm run icon` first (the build script and CI do this).
- **First run needs internet** unless you both bundle Python *and* pre-seed the
  Chromium cache (not currently bundled — Chromium is fetched on first run).
- **Installer size** — without the bundled runtime the installer is ~90–110 MB
  (Electron). With the bundled Python it grows by ~40–50 MB; Chromium is still
  fetched at first run.

---

## 5. Publishing

### To GitHub Releases (recommended)

**Automated (CI):** push a tag and let
`.github/workflows/build-installer.yml` build + attach the `.exe`:

```bat
git tag v1.0.0
git push origin v1.0.0
```

The workflow runs on `windows-latest`, stages the bundled Python, builds, and
attaches `dist/*.exe` to the release for tag pushes (`softprops/action-gh-release`).
`workflow_dispatch` runs upload the `.exe` as a run artifact instead.

**Manual:**
1. `scripts\build-installer.bat --offline`
2. Create a release at `https://github.com/devesh16145/qamate/releases/new`, choose
   the tag, and upload `dist\QAmate-Setup-<version>.exe`.

### To the marketing website

The website already links to the latest release:

- **Nav:** `Download`
- **Hero:** *Download for Windows* → `#download`
- **Download section** (`website/index.html` `#download`): the button points at
  `https://github.com/devesh16145/qamate/releases/latest` and documents system
  requirements + the developer-install alternative.

Once a release exists, that link resolves to the newest `QAmate-Setup-*.exe`. To
pin a specific asset instead, replace the button `href` with the direct asset URL,
e.g. `https://github.com/devesh16145/qamate/releases/download/v1.0.0/QAmate-Setup-1.0.0.exe`.

If you host the `.exe` yourself, drop it somewhere like `website/releases/` and
point the button at `releases/QAmate-Setup-x.x.x.exe`.

---

## 6. File map

| File | Purpose |
|---|---|
| `package.json` → `build` | electron-builder config (appId `com.qamate.app`, NSIS, per-user, `asar:false`, `signAndEditExecutable:false`, files/extraResources). |
| `bootstrap.js` | First-run Python provisioning + splash orchestration. |
| `src/setup.html` | Branded first-run progress screen. |
| `scripts/generate-icon.js` | SVG → `.ico` + window PNGs (`npm run icon`). |
| `scripts/prepare-bundle.ps1` | Stage bundled Python for offline install (`npm run prepare:python`). |
| `scripts/build-installer.bat` | End-to-end build orchestrator. |
| `build/pybundle/` | Staging dir bundled as `resources/pybundle` (`python/` is a git-ignored artifact). |
| `.github/workflows/build-installer.yml` | CI: build on tag, publish to Release. |

---

## 7. Troubleshooting builds

**`Cannot create symbolic link` (winCodeSign extract)** — Windows without Developer Mode blocks symlinks in electron-builder's code-sign cache. We set `win.signAndEditExecutable: false` so unsigned local builds work. For production, sign with an Authenticode cert in CI instead.

**Missing `.ico`** — Run `npm run icon` before `npm run dist` (generates `qamate-mark.ico` from the SVG).
