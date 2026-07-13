// ─────────────────────────────────────────────────────────────────────────────
// QAmate first-run bootstrap
//
// QAmate is Electron + an embedded Python engine (Playwright, pydantic-ai, …).
// A packaged install ships the Electron app + source but NOT the Python
// environment (a venv is ~500 MB with Chromium and is not relocatable). So on
// first launch we provision a real virtualenv *next to the app* and install the
// Python deps + Chromium, showing a branded progress splash.
//
// The venv lives at `<app>/venv` — identical to the dev layout — so main.js and
// every engine spawn keep using `venv/Scripts/python.exe` unchanged. Because the
// NSIS installer is per-user (installs under %LOCALAPPDATA%\Programs\QAmate), the
// app directory is user-writable and the venv, config.json, results/ and
// projects/ all work in place.
//
// Base interpreter, in order of preference:
//   1. Bundled python-build-standalone at resources/pybundle/python (fully
//      offline; staged by scripts/prepare-bundle.ps1 before packaging).
//   2. A system Python 3.11+ (py -3.12 / py -3 / python / python3).
// If neither is available the app still opens; runs surface a clear message.
// ─────────────────────────────────────────────────────────────────────────────

const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, spawnSync } = require('child_process');

const APP_DIR = __dirname;
const IS_WIN = process.platform === 'win32';

const VENV_DIR = path.join(APP_DIR, 'venv');
const VENV_PYTHON = IS_WIN
  ? path.join(VENV_DIR, 'Scripts', 'python.exe')
  : path.join(VENV_DIR, 'bin', 'python');
const REQUIREMENTS = path.join(APP_DIR, 'requirements.txt');
const STAMP = path.join(VENV_DIR, '.qamate-env.json');

// Bump when requirements or provisioning steps change to force a re-provision.
const ENV_VERSION = '1';

// ── Interpreter discovery ────────────────────────────────────────────────────

/** Path to a bundled python-build-standalone interpreter, or null. */
function bundledPython() {
  const roots = app.isPackaged
    ? [path.join(process.resourcesPath, 'pybundle')]
    : [path.join(APP_DIR, 'build', 'pybundle')];
  const rel = IS_WIN ? ['python', 'python.exe'] : ['python', 'bin', 'python3'];
  for (const root of roots) {
    const p = path.join(root, ...rel);
    if (fs.existsSync(p)) return { cmd: p, args: [] };
  }
  return null;
}

/** A usable system Python 3.11+, or null. */
function systemPython() {
  const candidates = IS_WIN
    ? [['py', ['-3.12']], ['py', ['-3']], ['python', []], ['python3', []]]
    : [['python3', []], ['python', []]];
  for (const [cmd, pre] of candidates) {
    try {
      const r = spawnSync(cmd, [...pre, '-c', 'import sys;print("%d.%d" % sys.version_info[:2])'],
        { encoding: 'utf8', timeout: 15000, windowsHide: true });
      const out = ((r.stdout || '') + '').trim();
      const m = /^3\.(\d+)/.exec(out);
      if (r.status === 0 && m && parseInt(m[1], 10) >= 11) return { cmd, args: pre };
    } catch (_) { /* try next */ }
  }
  return null;
}

/** The base interpreter used to CREATE the venv (bundled preferred). */
function baseInterpreter() {
  return bundledPython() || systemPython();
}

// ── Readiness ────────────────────────────────────────────────────────────────

/**
 * True when the Python environment is provisioned and usable.
 * - A completed bootstrap writes a version stamp.
 * - A hand-made dev venv has no stamp: treat it as ready if Playwright is present
 *   so we never re-provision a working developer checkout.
 */
function pythonReady() {
  if (!fs.existsSync(VENV_PYTHON)) return false;
  try {
    const s = JSON.parse(fs.readFileSync(STAMP, 'utf8'));
    if (s && s.version === ENV_VERSION && s.complete === true) return true;
  } catch (_) { /* no/old stamp */ }
  const sp = IS_WIN
    ? path.join(VENV_DIR, 'Lib', 'site-packages', 'playwright')
    : path.join(VENV_DIR, 'lib');
  return fs.existsSync(sp);
}

// ── Command runner (streams output to a callback) ────────────────────────────

function run(cmd, args, onLine) {
  return new Promise((resolve) => {
    let proc;
    try {
      proc = spawn(cmd, args, {
        cwd: APP_DIR,
        env: { ...process.env, PYTHONUNBUFFERED: '1', PIP_DISABLE_PIP_VERSION_CHECK: '1' },
        windowsHide: true,
      });
    } catch (e) {
      onLine && onLine(`ERROR: ${e.message}`);
      return resolve(1);
    }
    const pump = (buf) => {
      for (const raw of buf.toString().split('\n')) {
        const line = raw.replace(/\r$/, '');
        if (line.trim() && onLine) onLine(line);
      }
    };
    proc.stdout.on('data', pump);
    proc.stderr.on('data', pump);
    proc.on('error', (e) => { onLine && onLine(`ERROR: ${e.message}`); resolve(1); });
    proc.on('close', (code) => resolve(code == null ? 1 : code));
  });
}

// ── Splash window ────────────────────────────────────────────────────────────

function createSplash(appIcon) {
  const win = new BrowserWindow({
    width: 560,
    height: 420,
    resizable: false,
    frame: false,
    show: true,
    backgroundColor: '#0B1220',
    title: 'Setting up QAmate',
    webPreferences: { contextIsolation: true, nodeIntegration: false },
    ...(appIcon ? { icon: appIcon } : {}),
  });
  win.loadFile(path.join(APP_DIR, 'src', 'setup.html'));
  return win;
}

async function splashCall(win, fn, ...args) {
  if (!win || win.isDestroyed()) return;
  const payload = JSON.stringify(args);
  try { await win.webContents.executeJavaScript(`window.${fn} && window.${fn}(...${payload});`); }
  catch (_) { /* window may be mid-load */ }
}

// ── Orchestration ────────────────────────────────────────────────────────────

const STEPS = [
  { key: 'venv', label: 'Creating Python environment' },
  { key: 'pip', label: 'Upgrading installer tools' },
  { key: 'deps', label: 'Installing Python dependencies' },
  { key: 'browser', label: 'Installing the Chromium browser' },
];

/**
 * Provision the Python environment with a progress splash.
 * Resolves { ok:true } or { ok:false, message } — the caller opens the main
 * window regardless so the app is usable (runs will report the missing env).
 */
async function runFirstRunSetup(appIcon) {
  const win = createSplash(appIcon);
  // Give the splash a moment to load before we start pushing updates.
  await new Promise((r) => win.webContents.once('did-finish-load', r));

  const log = (line) => splashCall(win, 'qamateLog', line);
  const setStep = (i, detail) => splashCall(win, 'qamateStep', i, STEPS.length, STEPS[i].label, detail || '');

  const base = baseInterpreter();
  if (!base) {
    await splashCall(win, 'qamateFail',
      'Python 3.11+ was not found. Install Python 3.12 from python.org (tick "Add to PATH"), then relaunch QAmate.');
    await new Promise((r) => setTimeout(r, 60000));
    if (!win.isDestroyed()) win.close();
    return { ok: false, message: 'No Python interpreter available' };
  }

  await log(`Using base interpreter: ${base.cmd} ${base.args.join(' ')}`.trim());

  try {
    fs.mkdirSync(APP_DIR, { recursive: true });

    // 1) venv
    await setStep(0);
    if (!fs.existsSync(VENV_PYTHON)) {
      const code = await run(base.cmd, [...base.args, '-m', 'venv', VENV_DIR], log);
      if (code !== 0 || !fs.existsSync(VENV_PYTHON)) throw new Error('Failed to create the virtual environment.');
    } else {
      await log('Existing environment found — reusing.');
    }

    // 2) pip / wheel
    await setStep(1);
    await run(VENV_PYTHON, ['-m', 'pip', 'install', '--upgrade', 'pip', 'wheel'], log);

    // 3) requirements
    await setStep(2);
    const depsCode = await run(VENV_PYTHON, ['-m', 'pip', 'install', '-r', REQUIREMENTS], log);
    if (depsCode !== 0) throw new Error('pip failed to install the Python dependencies.');

    // 4) Chromium
    await setStep(3);
    const browserCode = await run(VENV_PYTHON, ['-m', 'playwright', 'install', 'chromium'], log);
    if (browserCode !== 0) await log('WARNING: Chromium download reported an error — you can retry later from Settings.');

    fs.writeFileSync(STAMP, JSON.stringify({
      version: ENV_VERSION,
      complete: true,
      base: base.cmd,
      created_at: new Date().toISOString(),
    }, null, 2));

    await splashCall(win, 'qamateDone');
    await new Promise((r) => setTimeout(r, 900));
    if (!win.isDestroyed()) win.close();
    return { ok: true };
  } catch (e) {
    await splashCall(win, 'qamateFail', `Setup could not finish: ${e.message}`);
    await new Promise((r) => setTimeout(r, 60000));
    if (!win.isDestroyed()) win.close();
    return { ok: false, message: e.message };
  }
}

module.exports = {
  APP_DIR,
  VENV_DIR,
  VENV_PYTHON,
  ENV_VERSION,
  pythonReady,
  baseInterpreter,
  bundledPython,
  systemPython,
  runFirstRunSetup,
};
