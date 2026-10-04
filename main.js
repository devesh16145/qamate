const { app, BrowserWindow, ipcMain, shell, safeStorage, dialog, nativeImage } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');
const https = require('https');
const bootstrap = require('./bootstrap');

// Code lives in APP_ROOT (read-only inside a macOS .app); everything QAmate writes
// lives in DATA_ROOT. They are the same folder in dev and on Windows (see bootstrap.js).
const APP_ROOT = __dirname;
const DATA_ROOT = bootstrap.DATA_DIR;
bootstrap.ensureDataDir();
const LlmProviders = require('./src/llm_providers');

const PROVIDER_CATALOG = JSON.parse(fs.readFileSync(path.join(APP_ROOT, 'engine', 'provider_catalog.json'), 'utf8'));
LlmProviders.setCatalog(PROVIDER_CATALOG);

let mainWindow;
let pythonProcess = null;

const VENV_PYTHON = bootstrap.VENV_PYTHON;
const RUNNER_SCRIPT = path.join(APP_ROOT, 'engine', 'runner.py');
const CONFIG_PATH = path.join(DATA_ROOT, 'config.json');
const CONFIG_EXAMPLE_PATH = path.join(APP_ROOT, 'config.example.json');
const RESULTS_DIR = path.join(DATA_ROOT, 'results');

function ensureConfig() {
  if (fs.existsSync(CONFIG_PATH)) return CONFIG_PATH;
  if (fs.existsSync(CONFIG_EXAMPLE_PATH)) {
    fs.copyFileSync(CONFIG_EXAMPLE_PATH, CONFIG_PATH);
    console.log('[QAmate] Created config.json from config.example.json — configure URLs and credentials in Settings.');
  }
  return CONFIG_PATH;
}

function loadConfig() {
  ensureConfig();
  if (!fs.existsSync(CONFIG_PATH)) return {};
  const cfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  // One-time upgrade of pre-catalog llm settings (drops the old mock provider).
  if (LlmProviders.migrateConfig(cfg)) fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2));
  return cfg;
}

/** OS window/taskbar icon. Windows needs .ico/.png — export assets/branding/qamate-mark-32.png from the brand sheet. */
function resolveAppIcon() {
  const branding = path.join(APP_ROOT, 'assets', 'branding');
  const png = path.join(branding, 'qamate-mark-32.png');
  if (fs.existsSync(png)) {
    const img = nativeImage.createFromPath(png);
    if (!img.isEmpty()) return img;
  }
  const svg = path.join(branding, 'qamate-mark.svg');
  if (fs.existsSync(svg)) {
    try {
      const img = nativeImage.createFromPath(svg);
      if (!img.isEmpty()) return img;
    } catch (_) { /* SVG often unsupported as native window icon on Windows */ }
  }
  return undefined;
}

const APP_ICON = resolveAppIcon();

/** Marketing website capture: `electron . --capture-screenshots` */
const CAPTURE_SCREENSHOTS = process.argv.includes('--capture-screenshots');
const SCREENSHOT_DIR = path.join(APP_ROOT, 'website', 'assets', 'screenshots');

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    autoHideMenuBar: true,
    // Frameless: the app's own .titlebar IS the window title bar (DataGrip-style) — the
    // app toolbar lives in the same row as the native min/max/close, shown as an overlay
    // on the top-right. Overlay height matches .titlebar (44px); colors track the theme
    // via the 'set-titlebar-theme' IPC below.
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#ffffff', symbolColor: '#45454d', height: 44 },
    webPreferences: {
      preload: path.join(APP_ROOT, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    title: "QAmate — Automated Testing System",
    backgroundColor: '#ffffff',
    ...(APP_ICON ? { icon: APP_ICON } : {}),
  });

  mainWindow.loadFile(path.join(APP_ROOT, 'src', 'index.html'));
  if (!CAPTURE_SCREENSHOTS) mainWindow.webContents.openDevTools();
  if (CAPTURE_SCREENSHOTS) scheduleWebsiteCaptures(mainWindow);
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function writeScreenshot(win, filename) {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  const image = await win.webContents.capturePage();
  const out = path.join(SCREENSHOT_DIR, filename);
  fs.writeFileSync(out, image.toPNG());
  console.log('[QAmate] Screenshot saved:', out);
}

async function clickInRenderer(win, fnBody) {
  try {
    await win.webContents.executeJavaScript(`(() => { ${fnBody} })()`);
  } catch (e) {
    console.warn('[QAmate] capture click skipped:', e && e.message);
  }
}

/** After the IDE loads, grab PNGs for website/assets/screenshots/ and quit. */
async function waitForIdeReady(win, timeoutMs = 120000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const ready = await win.webContents.executeJavaScript(`
        !!document.querySelector('.titlebar') &&
        !!document.querySelector('.main-grid') &&
        !document.body.innerText.includes('Loading IDE...')
      `);
      if (ready) {
        await delay(1500);
        return;
      }
    } catch (_) { /* renderer not ready yet */ }
    await delay(500);
  }
  throw new Error('IDE did not finish loading within timeout');
}

async function scheduleWebsiteCaptures(win) {
  win.webContents.once('did-finish-load', async () => {
    try {
      win.webContents.closeDevTools();
      win.setContentSize(1440, 900);
      win.center();
      await waitForIdeReady(win);

      await writeScreenshot(win, 'app-main.png');

      // Results workspace tab (titlebar window control)
      await clickInRenderer(win, `
        const btn = [...document.querySelectorAll('.tb-window-btn, button, [title]')]
          .find(el => (el.getAttribute('title') || '').toLowerCase() === 'results');
        if (btn) btn.click();
      `);
      await delay(2000);
      await writeScreenshot(win, 'app-results.png');

      // History inner-right panel
      await clickInRenderer(win, `
        const btn = [...document.querySelectorAll('.tb-window-btn, button, [title]')]
          .find(el => (el.getAttribute('title') || '').toLowerCase().startsWith('history'));
        if (btn) btn.click();
      `);
      await delay(1500);
      await writeScreenshot(win, 'app-history.png');
    } catch (e) {
      console.error('[QAmate] Screenshot capture failed:', e && e.message);
      process.exitCode = 1;
    } finally {
      app.quit();
    }
  });
}

// Recolor the native window-control overlay (min/max/close) when the app theme flips,
// so they match the light/dark titlebar instead of staying white on a dark bar.
ipcMain.handle('set-titlebar-theme', (e, { theme } = {}) => {
  if (!mainWindow || mainWindow.isDestroyed()) return false;
  try {
    mainWindow.setTitleBarOverlay(theme === 'dark'
      ? { color: '#2b2d30', symbolColor: '#dfe1e5', height: 44 }
      : { color: '#ffffff', symbolColor: '#45454d', height: 44 });
    return true;
  } catch (_) { return false; }
});

// First-run bootstrap: a packaged install ships without the Python engine
// environment (venv + Playwright + Chromium). Provision it once, behind a
// progress splash, before opening the IDE. A dev checkout with an existing venv
// is detected as ready and skips this. See bootstrap.js.
app.whenReady().then(async () => {
  // Running from source on macOS: show the QAmate mark in the Dock, not Electron's.
  if (process.platform === 'darwin' && app.dock) {
    const dockPng = path.join(APP_ROOT, 'assets', 'branding', 'qamate-mark-1024.png');
    if (fs.existsSync(dockPng)) app.dock.setIcon(nativeImage.createFromPath(dockPng));
  }
  try {
    if (!bootstrap.pythonReady()) {
      await bootstrap.runFirstRunSetup(APP_ICON);
    }
  } catch (e) {
    console.error('[QAmate] First-run setup error:', e && e.message);
  }
  createWindow();
  _watchData();
});

// ── Keep the IDE's test list and run history live ─────────────────────────────
// Tests are written by the agent (docked or in its own window), the recorder, or
// edits outside the app; runs land in results/. Watch those trees and tell every
// window what changed, so lists refresh in place instead of needing a reload.
const _dataWatchers = [];
function _watchData() {
  const roots = [path.join(DATA_ROOT, 'tests'), path.join(DATA_ROOT, 'projects'), RESULTS_DIR];
  let pending = { tests: false, history: false };
  let timer = null;
  const flush = () => {
    timer = null;
    const kinds = pending;
    pending = { tests: false, history: false };
    for (const w of BrowserWindow.getAllWindows()) {
      try { w.webContents.send('ats-data-changed', kinds); } catch (e) { /* window closing */ }
    }
  };
  for (const root of roots) {
    try {
      fs.mkdirSync(root, { recursive: true });
      _dataWatchers.push(fs.watch(root, { recursive: true }, (_event, file) => {
        const base = path.basename(String(file || ''));
        let hit = false;
        if (/^test_.*\.py$/.test(base) || base === 'test_cases.json' || base === 'test_data.json') {
          pending.tests = true; hit = true;
        } else if (root === RESULTS_DIR && base === 'run_metadata.json') {
          pending.history = true; hit = true;
        }
        if (hit && !timer) timer = setTimeout(flush, 400);
      }));
    } catch (e) {
      console.warn('[QAmate] could not watch', root, e && e.message);
    }
  }
}

app.on('window-all-closed', () => {
  killPython();
  _agentKillAll();
  if (process.platform !== 'darwin') app.quit();
});

function killPython() {
  if (pythonProcess) {
    try { pythonProcess.kill('SIGTERM'); } catch (e) { /* ignore */ }
    pythonProcess = null;
  }
}

function sendToRenderer(channel, data) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, data);
  }
}

// ──────────────────────────────────────
// IPC: Get test flows from test_cases.json files
// ──────────────────────────────────────
ipcMain.handle('get-test-flows', async () => {
  const flowsDir = _flowsDir();
  const flows = [];

  if (!fs.existsSync(flowsDir)) return flows;

  const modules = fs.readdirSync(flowsDir).filter(f => {
    const full = path.join(flowsDir, f);
    return fs.lstatSync(full).isDirectory() && !f.startsWith('__');
  });

  for (const mod of modules) {
    const tcFile = path.join(flowsDir, mod, 'test_cases.json');
    if (fs.existsSync(tcFile)) {
      try {
        const tcs = JSON.parse(fs.readFileSync(tcFile, 'utf8'));
        const label = mod.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        flows.push({ name: label, id: mod, test_cases: tcs });
      } catch (e) {
        console.error(`Error parsing ${tcFile}:`, e.message);
      }
    }
  }
  return flows;
});

// ──────────────────────────────────────
// IPC: Run selected tests
// ──────────────────────────────────────
ipcMain.handle('run-tests', async (event, options) => {
  killPython(); // Kill any previous run

  if (!fs.existsSync(VENV_PYTHON)) {
    sendToRenderer('test-log', `ERROR: Python environment not ready (${VENV_PYTHON}). Restart QAmate to finish first-run setup, or ensure Python 3.11+ is installed and on PATH.`);
    return;
  }

  sendToRenderer('test-log', `Launching test engine...`);

  pythonProcess = spawn(VENV_PYTHON, [RUNNER_SCRIPT], {
    cwd: DATA_ROOT,
    env: {
      ...process.env,
      ATS_ROOT: DATA_ROOT,
      ATS_APP_ROOT: APP_ROOT,
      PYTHONPATH: APP_ROOT,
      PYTHONUNBUFFERED: '1',
    },
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: false,
    detached: true,
  });

  // Buffer for partial lines from stdout
  let stdoutBuffer = '';

  pythonProcess.stdout.on('data', (chunk) => {
    stdoutBuffer += chunk.toString();
    const lines = stdoutBuffer.split('\n');
    // Keep the last (possibly incomplete) line in the buffer
    stdoutBuffer = lines.pop();

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const parsed = JSON.parse(trimmed);
        sendToRenderer('test-progress', parsed);
      } catch (e) {
        // Not JSON — it's raw pytest output, send as log
        sendToRenderer('test-log', trimmed);
      }
    }
  });

  pythonProcess.stderr.on('data', (data) => {
    const lines = data.toString().split('\n');
    for (const line of lines) {
      if (line.trim()) {
        sendToRenderer('test-log', `STDERR: ${line.trim()}`);
      }
    }
  });

  pythonProcess.on('error', (err) => {
    sendToRenderer('test-log', `ERROR: Failed to start engine: ${err.message}`);
    pythonProcess = null;
  });

  pythonProcess.on('close', (code) => {
    // Flush remaining buffer
    if (stdoutBuffer.trim()) {
      try {
        const parsed = JSON.parse(stdoutBuffer.trim());
        sendToRenderer('test-progress', parsed);
      } catch (e) {
        sendToRenderer('test-log', stdoutBuffer.trim());
      }
    }
    sendToRenderer('test-log', `Engine exited with code ${code}`);
    pythonProcess = null;

    // Safety net: if a run is still "Running" (engine was stopped/killed or
    // crashed before finalizing), mark it terminal so history never gets stuck
    // on "Running". The runner finalizes normal/crashed runs itself; this only
    // catches the hard-kill (Stop) case.
    try {
      if (fs.existsSync(RESULTS_DIR)) {
        const dirs = fs.readdirSync(RESULTS_DIR)
          .filter(f => /^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$/.test(f))  // real runs only — skip _helper dirs that sort first
          .filter(f => { try { return fs.lstatSync(path.join(RESULTS_DIR, f)).isDirectory(); } catch { return false; } })
          .sort().reverse().slice(0, 3);
        for (const d of dirs) {
          const mp = path.join(RESULTS_DIR, d, 'run_metadata.json');
          if (!fs.existsSync(mp)) continue;
          const meta = JSON.parse(fs.readFileSync(mp, 'utf8'));
          if (meta.status === 'Running') {
            meta.status = 'Stopped';
            meta.note = 'Run did not finalize (stopped or engine exited early).';
            fs.writeFileSync(mp, JSON.stringify(meta, null, 2));
          }
        }
      }
    } catch (e) { /* best effort */ }
  });

  // Send the run command to the Python process via stdin
  const runCmd = JSON.stringify({
    action: 'run',
    tc_ids: options.tc_ids,
    env: options.env,
    mode: options.mode,
    execMode: options.execMode || 'sequential',
    parallel: options.parallel,
    project_id: _activeProjectId(),   // scope the run to the active project's suite
    zoom: options.zoom || '',
    userIndex: options.userIndex ?? 0,
    sellerUserIndex: options.sellerUserIndex ?? options.userIndex ?? 0,
    adminUserIndex: options.adminUserIndex ?? 0,
    variant: options.variant,
    project_id: options.project_id || options.projectId || null,
    ats_root: DATA_ROOT,
  });

  pythonProcess.stdin.write(runCmd + '\n');
});

// ──────────────────────────────────────
// IPC: Stop running tests
// ──────────────────────────────────────
ipcMain.handle('stop-tests', async () => {
  killPython();
  return true;
});

// ──────────────────────────────────────
// IPC: Config
// ──────────────────────────────────────
ipcMain.handle('get-config', async () => {
  return loadConfig();
});

ipcMain.handle('save-config', async (event, config) => {
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
  return true;
});

// ──────────────────────────────────────
// IPC: Open files/folders
// ──────────────────────────────────────
ipcMain.handle('open-report', async (event, filePath) => {
  if (filePath) {
    const abs = path.isAbsolute(filePath) ? filePath : path.join(DATA_ROOT, filePath);
    shell.openPath(abs);
  }
});

ipcMain.handle('open-folder', async (event, folderPath) => {
  if (folderPath) {
    const abs = path.isAbsolute(folderPath) ? folderPath : path.join(DATA_ROOT, folderPath);
    shell.openPath(abs);
  }
});

ipcMain.handle('open-file', async (event, filePath) => {
  if (filePath) {
    const abs = path.isAbsolute(filePath) ? filePath : path.join(DATA_ROOT, filePath);
    shell.openPath(abs);
  }
});

// Open a Playwright trace (.zip) in the interactive Trace Viewer — time-travel
// debugging with DOM snapshots, network, console and per-action screenshots.
ipcMain.handle('open-trace', async (event, tracePath) => {
  if (!tracePath) return { status: 'error', message: 'No trace path provided' };
  const abs = path.isAbsolute(tracePath) ? tracePath : path.join(DATA_ROOT, tracePath);
  if (!fs.existsSync(abs)) return { status: 'error', message: 'Trace file not found' };
  try {
    const proc = spawn(VENV_PYTHON, ['-m', 'playwright', 'show-trace', abs], {
      cwd: DATA_ROOT,
      detached: true,
      stdio: 'ignore',
    });
    proc.unref();
    return { status: 'success' };
  } catch (e) {
    return { status: 'error', message: e.message };
  }
});

// ════════════════════════════════════════════════════════════════
// Autonomous pipeline IPC: secrets, projects, explore (L1), PRD (L2),
// synthesize (L3). Mirrors the analyze-coverage spawn+stream pattern.
// ════════════════════════════════════════════════════════════════
const PROJECT_STORE = path.join(APP_ROOT, 'engine', 'project_store.py');

// ── App-level project scoping ────────────────────────────────────────────────
// The ACTIVE project (projects/_index.json) scopes the whole app: each project
// owns its own tests (projects/<id>/tests) so a new project starts fresh.
// No active project = the legacy built-in suite (<ats_root>/tests).
// Path convention mirrored in engine/project_store.py tests_root() — keep in sync.
function _activeProjectId() {
  try {
    const idx = JSON.parse(fs.readFileSync(path.join(DATA_ROOT, 'projects', '_index.json'), 'utf8'));
    const id = idx && idx.active;
    if (id && fs.existsSync(path.join(DATA_ROOT, 'projects', id, 'project.json'))) return id;
  } catch (e) { /* no projects yet */ }
  return null;
}
function _testsRoot() {
  // A project owns a suite only when projects/<id>/tests EXISTS (created by
  // project creation or explicit activation) — pre-suite projects (the original
  // 'test' agent project) keep showing the legacy built-in suite unchanged.
  const pid = _activeProjectId();
  if (pid) {
    const own = path.join(DATA_ROOT, 'projects', pid, 'tests');
    if (fs.existsSync(own)) return own;
  }
  return path.join(DATA_ROOT, 'tests');
}
function _flowsDir() { return path.join(_testsRoot(), 'flows'); }
const AGENT_RECORDER = path.join(APP_ROOT, 'engine', 'agent_recorder.py');
const SECRETS_PATH = path.join(app.getPath('userData'), 'ats_secrets.json');

// ── Secrets: encrypted at rest via OS keychain (safeStorage). Plaintext is
// only ever held in memory and injected into the Python child's env at spawn.
function _readSecrets() {
  try {
    if (!fs.existsSync(SECRETS_PATH) || !safeStorage.isEncryptionAvailable()) return {};
    const enc = JSON.parse(fs.readFileSync(SECRETS_PATH, 'utf8'));
    const out = {};
    for (const k of Object.keys(enc)) {
      try { out[k] = safeStorage.decryptString(Buffer.from(enc[k], 'base64')); } catch (e) { /* skip */ }
    }
    return out;
  } catch (e) { return {}; }
}
function _writeSecret(key, value) {
  let enc = {};
  try { if (fs.existsSync(SECRETS_PATH)) enc = JSON.parse(fs.readFileSync(SECRETS_PATH, 'utf8')); } catch (e) { /* fresh */ }
  if (value) {
    if (!safeStorage.isEncryptionAvailable()) throw new Error('OS keychain encryption unavailable');
    enc[key] = safeStorage.encryptString(value).toString('base64');
  } else {
    delete enc[key];
  }
  fs.writeFileSync(SECRETS_PATH, JSON.stringify(enc));
}
function _llmEnv() {
  // Inject ALL stored secrets whose names are env-var shaped. The old ATS_
  // prefix filter silently dropped MIMO_API_KEY / MIMO_API_KEY2 saved via
  // Settings (they only worked when present in the OS user environment).
  const s = _readSecrets();
  const env = {};
  for (const k of Object.keys(s)) if (/^[A-Z][A-Z0-9_]*$/.test(k)) env[k] = s[k];
  return env;
}

function _keyStatus(cfg) {
  const keys = {};
  const s = _readSecrets();
  for (const k of Object.keys(s)) keys[k] = !!s[k];
  for (const f of LlmProviders.keyFieldsFromConfig(cfg)) if (process.env[f.env]) keys[f.env] = true;
  return keys;
}

function _isProviderConfigured(providerId, cfg) {
  return LlmProviders.isConfigured(providerId, cfg, _keyStatus(cfg));
}

// ── BYOK "Test connection": validate a key and list the provider's models ──
async function _fetchJson(url, init) {
  const res = await fetch(url, { ...init, signal: AbortSignal.timeout(15000) });
  let body = null;
  try { body = await res.json(); } catch (e) { /* non-JSON */ }
  return { status: res.status, ok: res.ok, body };
}

function _errText(body) {
  const e = body && (body.error || body);
  return String((e && (e.message || e.type)) || (typeof e === 'string' ? e : '') || '').slice(0, 200);
}

async function _probeProvider(r, key) {
  const base = (r.base_url || '').replace(/\/+$/, '');
  if (!base) return { ok: false, message: 'Base URL is required.' };
  const auth = key ? { Authorization: `Bearer ${key}` } : {};
  // models: [{ id, label?, context?, vision? }]; listed=false when the API has no model listing.
  let res, models = [], listed = true;
  if (r.protocol === 'typesafe' || r.protocol === 'openrouter_decisions') {
    listed = false;
    // Decision APIs have no model listing: make one tiny two-choice decision.
    const question = { action: { type: 'choice', instructions: 'Connection test. Choose "ok".', criteria: { ok: 'Connection works', stop: 'Stop' } } };
    res = await _fetchJson(`${base}${r.protocol === 'typesafe' ? '/systemone' : '/decisions'}`, {
      method: 'POST', headers: { ...auth, 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: r.model, state: {}, questions: question }),
    });
  } else if (r.protocol === 'anthropic') {
    res = await _fetchJson(`${base}${base.endsWith('/v1') ? '' : '/v1'}/models?limit=100`, { headers: { 'x-api-key': key, 'anthropic-version': '2023-06-01' } });
    if (res.ok) models = (res.body?.data || []).map(m => ({ id: m.id, label: m.display_name, context: m.max_input_tokens }));
  } else if (r.protocol === 'google') {
    res = await _fetchJson(`${base}/models?pageSize=1000`, { headers: { 'x-goog-api-key': key } });
    if (res.ok) models = (res.body?.models || [])
      .filter(m => (m.supportedGenerationMethods || []).includes('generateContent') && !/embedding|aqa|imagen|veo|tts/i.test(m.name))
      .map(m => ({ id: String(m.name).replace(/^models\//, ''), label: m.displayName, context: m.inputTokenLimit }));
  } else if (r.preset === 'openrouter') {
    res = await _fetchJson(`${base}/key`, { headers: auth });
    if (res.ok) {
      // The agent needs tool calling, so only list models that support it.
      const list = await _fetchJson(`${base}/models`, {});
      models = (list.body?.data || []).filter(m => (m.supported_parameters || []).includes('tools'))
        .map(m => ({ id: m.id, label: m.name, context: m.context_length, vision: (m.architecture?.input_modalities || []).includes('image') }));
    }
  } else {
    const v1 = r.protocol === 'ollama' && !base.endsWith('/v1') ? base + '/v1' : base;
    res = await _fetchJson(`${v1}/models`, { headers: { ...auth, ...(r.headers || {}) } });
    if (res.ok) {
      models = (res.body?.data || []).map(m => ({ id: m.id, context: m.context_length || m.context_window }));
      // OpenAI's list also has embedding/audio/image models the agent can't use.
      if (r.preset === 'openai') models = models.filter(m => !/embed|tts|whisper|dall-e|davinci|babbage|moderation|transcribe|audio|realtime|image|sora/i.test(m.id));
    } else if (res.status === 404 || res.status === 405) {
      listed = false;
      // No model listing on this endpoint: fall back to a 1-token completion.
      const thinkingOff = (LlmProviders.preset(r.preset)?.thinking?.off?.extra_body) || {};
      res = await _fetchJson(`${v1}/chat/completions`, {
        method: 'POST', headers: { ...auth, ...(r.headers || {}), 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: r.model, messages: [{ role: 'user', content: 'ping' }], [r.token_parameter || 'max_tokens']: 1, ...thinkingOff }),
      });
    }
  }
  if (res.ok) return { ok: true, listed, models: models.sort((a, b) => a.id.localeCompare(b.id)) };
  const rejected = res.status === 401 || res.status === 403 || (r.protocol === 'google' && res.status === 400);
  return { ok: false, status: res.status, rejected, message: `${rejected ? 'Key rejected' : 'Request failed'} (HTTP ${res.status})${_errText(res.body) ? ': ' + _errText(res.body) : ''}` };
}

const _modelCache = new Map();  // in-memory only: hash(endpoint+key) -> { at, result }
const MODEL_CACHE_MS = 10 * 60 * 1000;

async function _listModels({ id, profile, refresh }) {
  const cfg = loadConfig();
  const draft = { ...cfg, llm: { ...(cfg.llm || {}), providers: { ...(cfg.llm?.providers || {}), [id]: profile || cfg.llm?.providers?.[id] } } };
  const r = LlmProviders.resolve(id, draft);
  if (!r) return { ok: false, message: `Unknown provider ${id}` };
  if (r.protocol === 'typesafe' || r.protocol === 'openrouter_decisions') return { ok: true, listed: false, models: [] };
  const needsKey = LlmProviders.listFromConfig(draft).find(p => p.id === id)?.needsKey;
  const k = (r.api_key_env && (_readSecrets()[r.api_key_env] || process.env[r.api_key_env])) || '';
  if (needsKey && !k) return { ok: false, needsKey: true, message: 'Save an API key to load models.' };
  const cacheKey = require('crypto').createHash('sha256').update(`${r.protocol}|${r.preset}|${r.base_url}|${k}`).digest('hex');
  const hit = _modelCache.get(cacheKey);
  if (!refresh && hit && Date.now() - hit.at < MODEL_CACHE_MS) return hit.result;
  let result;
  try {
    result = await _probeProvider(r, k);
    if (k && result.message) result.message = result.message.split(k).join('••••');
  } catch (err) {
    const msg = err && err.name === 'TimeoutError' ? 'Timed out reaching the provider' : (err && err.cause && err.cause.code) || (err && err.message) || String(err);
    return { ok: false, message: `Could not reach ${r.base_url}: ${msg}` };
  }
  if (result.ok) _modelCache.set(cacheKey, { at: Date.now(), result });
  return result;
}

async function _testProvider({ id, profile, key }) {
  const cfg = loadConfig();
  const draft = { ...cfg, llm: { ...(cfg.llm || {}), providers: { ...(cfg.llm?.providers || {}), [id]: profile || cfg.llm?.providers?.[id] } } };
  const r = LlmProviders.resolve(id, draft);
  if (!r) return { ok: false, message: `Unknown provider ${id}` };
  const needsKey = LlmProviders.listFromConfig(draft).find(p => p.id === id)?.needsKey;
  const k = key || (r.api_key_env && (_readSecrets()[r.api_key_env] || process.env[r.api_key_env])) || '';
  if (needsKey && !k) return { ok: false, message: 'Enter an API key first.' };
  // Some providers echo the submitted key in error text; never show it back.
  const redact = (res) => (k && res.message ? { ...res, message: res.message.split(k).join('••••') } : res);
  try {
    const first = redact(await _probeProvider(r, k));
    if (first.ok || !first.rejected) return { ...first, endpoint: r.endpoint };
    // Preset with regional endpoints (MiMo Token Plan): find the one this key belongs to.
    const eps = (LlmProviders.preset(r.preset)?.endpoints || []).filter(e => e.id !== r.endpoint && (!e.key_prefix || k.startsWith(e.key_prefix)));
    for (const ep of eps) {
      const res = redact(await _probeProvider({ ...r, base_url: ep.base_url }, k));
      if (res.ok) return { ...res, endpoint: ep.id, endpointChanged: true, message: `Key works on ${ep.label}` };
    }
    return { ...first, endpoint: r.endpoint };
  } catch (err) {
    const msg = err && err.name === 'TimeoutError' ? 'Timed out reaching the provider' : (err && err.cause && err.cause.code) || (err && err.message) || String(err);
    return { ok: false, message: `Could not reach ${r.base_url}: ${msg}` };
  }
}

// ── Conversational AI Agent (engine/agent_chat.py) — concurrent persisted sessions ─
// Each session is its own PERSISTENT child process driving its own browser, addressed by
// a sessionId. We keep a Map<sessionId,{proc,buf,projectId}>; every stdout {event:...} line
// is tagged with its sessionId and routed to the dedicated Agent window (not the IDE). The
// conversation/transcript/meta persist on disk under projects/<id>/agent_sessions/ — Python
// (engine/agent_sessions.py) owns messages.json; Node does the plain-JSON sidebar ops below.
const AGENT_CHAT = path.join(APP_ROOT, 'engine', 'agent_chat.py');
const AGENT_MAX_SESSIONS = parseInt(process.env.ATS_AGENT_MAX_SESSIONS || '4', 10);
const agentProcs = new Map(); // sessionId -> { proc, buf, projectId }

let agentWindow = null;
function createAgentWindow() {
  if (agentWindow && !agentWindow.isDestroyed()) { agentWindow.focus(); return agentWindow; }
  agentWindow = new BrowserWindow({
    width: 1240, height: 840,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(APP_ROOT, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    title: 'QAmate — AI Agent',
    backgroundColor: '#0f0f1a',
    ...(APP_ICON ? { icon: APP_ICON } : {}),
  });
  agentWindow.loadFile(path.join(APP_ROOT, 'src', 'agent.html'));
  // Closing the Agent window does NOT stop its sessions — they keep running (persisted +
  // resumable) and stay visible in the docked panel or when the window is reopened. Sessions
  // end on explicit Stop or app quit (window-all-closed -> _agentKillAll).
  agentWindow.on('closed', () => { agentWindow = null; });
  return agentWindow;
}

let historyWindow = null;
function createHistoryWindow() {
  if (historyWindow && !historyWindow.isDestroyed()) { historyWindow.focus(); return historyWindow; }
  historyWindow = new BrowserWindow({
    width: 420, height: 700,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(APP_ROOT, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    title: 'QAmate — History',
    backgroundColor: '#ffffff',
    ...(APP_ICON ? { icon: APP_ICON } : {}),
  });
  historyWindow.loadFile(path.join(APP_ROOT, 'src', 'history.html'));
  historyWindow.on('closed', () => { historyWindow = null; });
  return historyWindow;
}

let bugManagerWindow = null;
function createBugManagerWindow() {
  if (bugManagerWindow && !bugManagerWindow.isDestroyed()) { bugManagerWindow.focus(); return bugManagerWindow; }
  bugManagerWindow = new BrowserWindow({
    width: 1100, height: 760,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(APP_ROOT, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    title: 'QAmate — Bug & Story Manager',
    backgroundColor: '#ffffff',
    ...(APP_ICON ? { icon: APP_ICON } : {}),
  });
  bugManagerWindow.loadFile(path.join(APP_ROOT, 'src', 'bugs.html'));
  bugManagerWindow.on('closed', () => { bugManagerWindow = null; });
  return bugManagerWindow;
}

// Agent events are BROADCAST to every window: the dedicated agent window AND the main IDE
// (when the agent is docked into its right panel) both run the same UI and route by sessionId.
function sendToAgent(channel, data) {
  for (const w of BrowserWindow.getAllWindows()) {
    if (w && !w.isDestroyed()) w.webContents.send(channel, data);
  }
}

function _agentKill(sessionId) {
  const e = agentProcs.get(sessionId);
  if (!e) return;
  try { e.proc.stdin.write(JSON.stringify({ action: 'shutdown' }) + '\n'); } catch (err) { /* ignore */ }
  try { e.proc.kill('SIGTERM'); } catch (err) { /* ignore */ }
  agentProcs.delete(sessionId);
}

function _agentKillAll() { for (const sid of Array.from(agentProcs.keys())) _agentKill(sid); }

function _agentWrite(sessionId, obj) {
  const e = agentProcs.get(sessionId);
  if (!e || !e.proc.stdin || !e.proc.stdin.writable) return false;
  try { e.proc.stdin.write(JSON.stringify(obj) + '\n'); return true; }
  catch (err) { return false; }
}

function _spawnAgent(sessionId, projectId, initCmd) {
  const proc = spawn(VENV_PYTHON, [AGENT_CHAT], {
    cwd: DATA_ROOT,
    env: { ...process.env, ATS_ROOT: DATA_ROOT, ATS_APP_ROOT: APP_ROOT, PYTHONUNBUFFERED: '1', ..._llmEnv() },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const entry = { proc, buf: '', projectId };
  agentProcs.set(sessionId, entry);
  proc.stdout.on('data', (chunk) => {
    entry.buf += chunk.toString();
    const lines = entry.buf.split('\n');
    entry.buf = lines.pop();
    for (const line of lines) {
      const t = line.trim();
      if (!t) continue;
      let ev;
      try { ev = JSON.parse(t); } catch (e) { ev = { event: 'log', message: t }; }
      ev.sessionId = sessionId;  // route to the right session in the window
      sendToAgent('agent-event', ev);
    }
  });
  proc.stderr.on('data', (d) => {
    const t = d.toString().trim();
    if (t) sendToAgent('agent-event', { event: 'log', message: 'STDERR: ' + t, sessionId });
  });
  proc.on('error', (err) => { sendToAgent('agent-event', { event: 'error', message: err.message, sessionId }); agentProcs.delete(sessionId); });
  proc.on('close', (code) => { sendToAgent('agent-event', { event: 'exited', code, sessionId }); agentProcs.delete(sessionId); });
  _agentWrite(sessionId, initCmd);
}

ipcMain.handle('agent-open-window', async () => { createAgentWindow(); return { status: 'success' }; });
ipcMain.handle('history-open-window', async () => { createHistoryWindow(); return { status: 'success' }; });
ipcMain.handle('history-dock', async () => {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('history-dock-request');
  if (historyWindow && !historyWindow.isDestroyed()) historyWindow.close();
  return { status: 'success' };
});
ipcMain.handle('open-run-in-main', async (event, runId) => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.show(); mainWindow.focus();
    mainWindow.webContents.send('open-run-request', runId);
  }
  return { status: 'success' };
});
ipcMain.handle('agent-show-browser', async (event, { sessionId } = {}) => {
  if (!sessionId || !agentProcs.has(sessionId)) return { status: 'error', message: 'session not running' };
  return _agentWrite(sessionId, { action: 'show_browser' })
    ? { status: 'success' } : { status: 'error', message: 'write failed' };
});
ipcMain.handle('open-main-window', async () => {
  if (mainWindow && !mainWindow.isDestroyed()) { mainWindow.show(); mainWindow.focus(); }
  else createWindow();
  return { status: 'success' };
});
ipcMain.handle('bugs-open-window', async () => { createBugManagerWindow(); return { status: 'success' }; });
ipcMain.handle('open-external', async (event, url) => { try { if (url) await shell.openExternal(String(url)); return { success: true }; } catch (e) { return { success: false, error: e.message }; } });

// Dock the floating agent window INTO the main IDE's right panel: tell the main window to
// render the docked agent, then close the floating window. Sessions keep running (the window's
// 'closed' handler no longer kills them), and events broadcast to both, so the dock takes over
// seamlessly. The renderer flips its 'agentDock' state on the 'agent-dock-request' event.
ipcMain.handle('agent-dock', async () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('agent-dock-request');
    mainWindow.show();
    mainWindow.focus();
  }
  if (agentWindow && !agentWindow.isDestroyed()) agentWindow.close();
  return { status: 'success' };
});

ipcMain.handle('agent-start', async (event, opts = {}) => {
  if (!fs.existsSync(VENV_PYTHON)) return { status: 'error', message: `Python not found at ${VENV_PYTHON}` };
  const sessionId = opts.sessionId;
  if (!sessionId) return { status: 'error', message: 'sessionId required' };
  if (agentProcs.has(sessionId)) {
    _agentWrite(sessionId, { action: 'reattach' });  // already running -> reconnect (re-emit ready)
    return { status: 'success', reattached: true };
  }
  if (agentProcs.size >= AGENT_MAX_SESSIONS) {
    return { status: 'error', message: `Too many concurrent sessions (max ${AGENT_MAX_SESSIONS}). Stop one before starting another.` };
  }
  const cfg = loadConfig();
  const prov = opts.provider || LlmProviders.defaultFromConfig(cfg);
  if (!prov || !_isProviderConfigured(prov, cfg)) {
    return { status: 'error', message: LlmProviders.missingKeyMessage(prov, cfg) };
  }
  try {
    _spawnAgent(sessionId, opts.projectId || null, {
      action: 'init',
      session_id: sessionId,
      project_id: opts.projectId || null,
      env: opts.env || null,
      provider: opts.provider || null,
      headed: !!opts.headed,
      title: opts.title || '',
      start_path: opts.startPath || '',
      agentMode: (opts.agentMode === 'guided') ? 'guided' : 'auto',
      tool_budget: (opts.toolBudget != null) ? Number(opts.toolBudget) : null,
      engine: (opts.engine === 'classic') ? 'classic' : 'fast',
    });
  } catch (err) { return { status: 'error', message: err.message }; }
  return { status: 'success' };
});

ipcMain.handle('agent-send', async (event, { sessionId, message, attachments } = {}) => {
  if (!sessionId || !agentProcs.has(sessionId)) return { status: 'error', message: 'session not running — start it first' };
  // attachments: array of absolute file paths (the Python side reads + extracts them).
  const atts = Array.isArray(attachments) ? attachments.filter(Boolean).map(String) : [];
  return _agentWrite(sessionId, { action: 'chat', message: String(message || ''), attachments: atts })
    ? { status: 'success' } : { status: 'error', message: 'failed to write to agent' };
});

ipcMain.handle('agent-set-mode', async (event, { sessionId, mode } = {}) => {
  if (!sessionId || !agentProcs.has(sessionId)) return { status: 'error', message: 'session not running' };
  const m = (mode === 'guided') ? 'guided' : 'auto';
  return _agentWrite(sessionId, { action: 'set_mode', mode: m })
    ? { status: 'success', mode: m } : { status: 'error', message: 'write failed' };
});

ipcMain.handle('agent-reset', async (event, { sessionId } = {}) => (
  (sessionId && _agentWrite(sessionId, { action: 'reset' })) ? { status: 'success' } : { status: 'error', message: 'session not running' }
));

ipcMain.handle('agent-stop', async (event, { sessionId } = {}) => { if (sessionId) _agentKill(sessionId); return { status: 'success' }; });

// ── Session sidebar ops (plain JSON over projects/<id>/agent_sessions/). Path convention
// MUST match engine/agent_sessions.py `sessions_dir`. Python owns messages.json; Node only
// reads session.json/transcript.jsonl and does title-rename / delete here. ──
function _agentSessionsDir(projectId) {
  projectId = projectId || _activeProjectId();
  if (projectId) return path.join(DATA_ROOT, 'projects', projectId, 'agent_sessions');
  return path.join(DATA_ROOT, '.agent_context', 'agent_sessions');
}

function _mintSessionId(title) {
  const d = new Date(), p = (n) => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  const slug = (title || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32) || 'session';
  const rand = Math.random().toString(36).slice(2, 5);
  return `${stamp}-${slug}-${rand}`;
}

function _readSessionMeta(dir, id) {
  try { return JSON.parse(fs.readFileSync(path.join(dir, id, 'session.json'), 'utf8')); }
  catch (e) { return null; }
}

ipcMain.handle('agent-list-sessions', async (event, { projectId } = {}) => {
  const dir = _agentSessionsDir(projectId);
  let ids = [];
  try { ids = fs.readdirSync(dir); } catch (e) { /* none yet */ }
  const sessions = [];
  for (const id of ids) {
    const m = _readSessionMeta(dir, id);
    if (m && m.id) {
      // Reconcile a stale "running" left by a crashed/closed previous app run.
      if (m.status === 'running' && !agentProcs.has(m.id)) m.status = 'idle';
      m.live = agentProcs.has(m.id);
      sessions.push(m);
    }
  }
  sessions.sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
  return { status: 'success', dir, sessions };
});

// Mint a session id for the UI (the disk record is created by Python on first start, so an
// unstarted "new" session leaves no trace — which is correct).
ipcMain.handle('agent-new-session', async (event, { projectId, title } = {}) => {
  const now = new Date().toISOString().slice(0, 19);
  return {
    status: 'success',
    session: {
      id: _mintSessionId(title), project_id: projectId || _activeProjectId() || null,
      title: title || 'New session', created_at: now, updated_at: now,
      status: 'idle', message_count: 0, provider: '', model: '', live: false,
    },
  };
});

ipcMain.handle('agent-rename-session', async (event, { projectId, sessionId, title } = {}) => {
  const f = path.join(_agentSessionsDir(projectId), sessionId, 'session.json');
  try {
    const m = JSON.parse(fs.readFileSync(f, 'utf8'));
    m.title = String(title || '').slice(0, 120);
    m.updated_at = new Date().toISOString().slice(0, 19);
    fs.writeFileSync(f, JSON.stringify(m, null, 2));
    return { status: 'success', session: m };
  } catch (e) {
    // Not yet persisted (an unstarted session) — the renderer renames it locally.
    return { status: 'success', notPersisted: true };
  }
});

ipcMain.handle('agent-delete-session', async (event, { projectId, sessionId } = {}) => {
  if (agentProcs.has(sessionId)) _agentKill(sessionId);
  try { fs.rmSync(path.join(_agentSessionsDir(projectId), sessionId), { recursive: true, force: true }); }
  catch (e) { /* already gone */ }
  return { status: 'success' };
});

ipcMain.handle('agent-session-transcript', async (event, { projectId, sessionId } = {}) => {
  const f = path.join(_agentSessionsDir(projectId), sessionId, 'transcript.jsonl');
  const bubbles = [];
  try {
    for (const line of fs.readFileSync(f, 'utf8').split('\n')) {
      const t = line.trim();
      if (!t) continue;
      try { bubbles.push(JSON.parse(t)); } catch (e) { /* skip bad line */ }
    }
  } catch (e) { /* none yet */ }
  return { status: 'success', bubbles };
});

// ── AI agent: file attachments + per-project context folder & memory file ─────
// Path convention MUST match engine/project_store.py: projects/<id>/context
// (overridable by an absolute "context_dir" in project.json) + projects/<id>/
// AGENT_MEMORY.md, and <ats_root>/.agent_context/ when there is no project.
const ATTACH_FILTERS = [
  { name: 'Documents & Images', extensions: ['md', 'markdown', 'txt', 'rst', 'pdf', 'docx', 'xlsx', 'csv', 'tsv', 'json', 'log', 'yml', 'yaml', 'xml', 'html', 'htm', 'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'] },
  { name: 'Documents', extensions: ['md', 'markdown', 'txt', 'rst', 'pdf', 'docx', 'xlsx', 'csv', 'tsv', 'json', 'log', 'yml', 'yaml', 'xml', 'html', 'htm'] },
  { name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'] },
  { name: 'All Files', extensions: ['*'] },
];

function _showOpen(opts) {
  const win = BrowserWindow.getFocusedWindow() || mainWindow;
  return win ? dialog.showOpenDialog(win, opts) : dialog.showOpenDialog(opts);
}

// Mirror project_store.get_active_project_id: when the UI passes no project id,
// the agent falls back to the active project — the context/memory buttons must too.
function _activeProjectId() {
  try {
    const idx = JSON.parse(fs.readFileSync(path.join(DATA_ROOT, 'projects', '_index.json'), 'utf8'));
    if (idx && idx.active && fs.existsSync(path.join(DATA_ROOT, 'projects', idx.active, 'project.json'))) return idx.active;
  } catch (e) { /* no active project */ }
  return null;
}

function _agentContextPaths(projectId) {
  projectId = projectId || _activeProjectId();
  if (projectId) {
    const pdir = path.join(DATA_ROOT, 'projects', projectId);
    let contextDir = path.join(pdir, 'context');
    try {
      const pj = JSON.parse(fs.readFileSync(path.join(pdir, 'project.json'), 'utf8'));
      if (pj && pj.context_dir && path.isAbsolute(pj.context_dir)) contextDir = pj.context_dir;
    } catch (e) { /* use default */ }
    return { contextDir, memoryPath: path.join(pdir, 'AGENT_MEMORY.md') };
  }
  const base = path.join(DATA_ROOT, '.agent_context');
  return { contextDir: base, memoryPath: path.join(base, 'AGENT_MEMORY.md') };
}

function _fileMeta(p) {
  let size = 0;
  try { size = fs.statSync(p).size; } catch (e) { /* ignore */ }
  return { path: p, name: path.basename(p), ext: path.extname(p).toLowerCase().replace(/^\./, ''), size };
}

// Junk hidden when browsing/searching a scoped context folder — MIRRORS engine/agent_chat.py
// `_is_junk_name` (keep in sync). The folder is EXPLORED on demand, not imported, so we never
// deep-dump every file into the UI.
const _CTX_SKIP = new Set(['.git', 'node_modules', '.venv', 'venv', 'env', '__pycache__', '.idea',
  '.vscode', 'dist', 'build', '.next', '.cache', '.pytest_cache', '.mypy_cache', '.gradle', 'target',
  '.tox', 'coverage', '.turbo', '.parcel-cache', 'obj']);
function _isJunkName(name) { return !name || _CTX_SKIP.has(name) || name.startsWith('.'); }

// One directory LEVEL of the context folder (junk filtered), scoped so `subdir` can't escape it.
function _listContextLevel(contextDir, subdir) {
  const base = path.resolve(contextDir || '');
  const start = path.resolve(base, (subdir && subdir !== '.') ? subdir : '');
  if (!base || (start !== base && !start.startsWith(base + path.sep))) return { dirs: [], files: [] };
  let entries = [];
  try { entries = fs.readdirSync(start, { withFileTypes: true }); } catch (e) { return { dirs: [], files: [] }; }
  const dirs = [], files = [];
  for (const e of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    if (_isJunkName(e.name)) continue;
    const full = path.join(start, e.name);
    const rel = path.relative(base, full).split(path.sep).join('/');
    if (e.isDirectory()) dirs.push({ name: rel });
    else files.push({ ..._fileMeta(full), name: rel });
    if (dirs.length + files.length >= 400) break;
  }
  return { dirs, files };
}

// Flat list of file paths (junk filtered, capped) — ONLY to power the @-mention fuzzy search.
function _listContextFlat(contextDir, cap = 2000) {
  const base = path.resolve(contextDir || '');
  const out = [];
  const walk = (dir) => {
    if (out.length >= cap) return;
    let entries = [];
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (e) { return; }
    for (const e of entries.sort((a, b) => a.name.localeCompare(b.name))) {
      if (out.length >= cap) return;
      if (_isJunkName(e.name)) continue;
      const full = path.join(dir, e.name);
      if (e.isDirectory()) walk(full);
      else out.push(path.relative(base, full).split(path.sep).join('/'));
    }
  };
  if (base) walk(base);
  return out;
}

// Native picker for per-message attachments. Returns file metadata; the absolute
// paths are sent to Python on agent-send (Python reads + extracts the content).
ipcMain.handle('agent-pick-files', async () => {
  const r = await _showOpen({
    title: 'Attach files for the AI agent',
    properties: ['openFile', 'multiSelections'],
    filters: ATTACH_FILTERS,
  });
  if (!r || r.canceled || !Array.isArray(r.filePaths)) return { status: 'success', files: [] };
  return { status: 'success', files: r.filePaths.map(_fileMeta) };
});

ipcMain.handle('agent-context-list', async (event, { projectId, subdir } = {}) => {
  const { contextDir } = _agentContextPaths(projectId);
  const { dirs, files } = _listContextLevel(contextDir, subdir || '');
  return { status: 'success', dir: contextDir, subdir: subdir || '', dirs, files };
});

// Junk-filtered flat path list for the @-mention picker (paths only, capped).
ipcMain.handle('agent-context-files', async (event, { projectId } = {}) => {
  const { contextDir } = _agentContextPaths(projectId);
  return { status: 'success', dir: contextDir, files: _listContextFlat(contextDir) };
});

// Copy chosen files into the project's context folder (the agent reads them via tools).
ipcMain.handle('agent-context-add', async (event, { projectId } = {}) => {
  const { contextDir } = _agentContextPaths(projectId);
  const r = await _showOpen({
    title: 'Add files to the project context folder',
    properties: ['openFile', 'multiSelections'],
    filters: ATTACH_FILTERS,
  });
  if (!r || r.canceled || !r.filePaths || !r.filePaths.length) {
    return { status: 'success', added: 0, dir: contextDir, ..._listContextLevel(contextDir, '') };
  }
  try { fs.mkdirSync(contextDir, { recursive: true }); } catch (e) { /* ignore */ }
  let added = 0;
  for (const src of r.filePaths) {
    try { fs.copyFileSync(src, path.join(contextDir, path.basename(src))); added++; }
    catch (e) { /* skip unreadable */ }
  }
  return { status: 'success', added, dir: contextDir, ..._listContextLevel(contextDir, '') };
});

// Attach an EXISTING folder on disk as the project's context (sets project.json "context_dir",
// which both _agentContextPaths here and project_store.resolve_context_dir in Python honor).
// This is the "attach the context folder to the project" action — distinct from copying files.
ipcMain.handle('agent-context-set-folder', async (event, { projectId } = {}) => {
  projectId = projectId || _activeProjectId();
  if (!projectId) return { status: 'error', message: 'Select a project first to attach a context folder.' };
  const pf = path.join(DATA_ROOT, 'projects', projectId, 'project.json');
  if (!fs.existsSync(pf)) return { status: 'error', message: `Project '${projectId}' not found.` };
  const r = await _showOpen({ title: 'Choose a folder to use as this project context', properties: ['openDirectory'] });
  if (!r || r.canceled || !r.filePaths || !r.filePaths.length) return { status: 'cancelled' };
  const folder = r.filePaths[0];
  try {
    const pj = JSON.parse(fs.readFileSync(pf, 'utf8'));
    pj.context_dir = folder;
    fs.writeFileSync(pf, JSON.stringify(pj, null, 2));
  } catch (e) { return { status: 'error', message: 'Could not update project: ' + e.message }; }
  const { contextDir } = _agentContextPaths(projectId);
  return { status: 'success', dir: contextDir, folder, ..._listContextLevel(contextDir, '') };
});

ipcMain.handle('agent-context-open', async (event, { projectId } = {}) => {
  const { contextDir } = _agentContextPaths(projectId);
  try { fs.mkdirSync(contextDir, { recursive: true }); } catch (e) { /* ignore */ }
  shell.openPath(contextDir);
  return { status: 'success', dir: contextDir };
});

ipcMain.handle('agent-memory-get', async (event, { projectId } = {}) => {
  const { memoryPath } = _agentContextPaths(projectId);
  let content = '';
  try { content = fs.readFileSync(memoryPath, 'utf8'); } catch (e) { /* empty/missing */ }
  return { status: 'success', path: memoryPath, content };
});

ipcMain.handle('agent-memory-open', async (event, { projectId } = {}) => {
  const { memoryPath } = _agentContextPaths(projectId);
  try {
    fs.mkdirSync(path.dirname(memoryPath), { recursive: true });
    if (!fs.existsSync(memoryPath)) {
      fs.writeFileSync(memoryPath, '# Agent memory\n\nDurable facts the AI agent has learned about this app.\n');
    }
  } catch (e) { /* ignore */ }
  shell.openPath(memoryPath);
  return { status: 'success', path: memoryPath };
});

ipcMain.handle('set-secret', async (e, { key, value }) => {
  try { _writeSecret(key, value); return { status: 'success' }; }
  catch (err) { return { status: 'error', message: err.message }; }
});
ipcMain.handle('get-secret-status', async () => {
  return { status: 'success', keys: _keyStatus(loadConfig()), available: safeStorage.isEncryptionAvailable() };
});
ipcMain.handle('get-provider-catalog', async () => PROVIDER_CATALOG);
ipcMain.handle('llm-test-provider', async (e, opts) => _testProvider(opts || {}));
ipcMain.handle('llm-list-models', async (e, opts) => _listModels(opts || {}));

// Spawn a Python engine script; stream {event:"log"} lines to `progressChannel`,
// resolve with the final {event:"result"|status:...} object.
function runEngine(event, args, progressChannel, extraEnv) {
  return new Promise((resolve) => {
    let result = null;
    let proc;
    try {
      proc = spawn(VENV_PYTHON, args, { cwd: DATA_ROOT, env: { ...process.env, ATS_ROOT: DATA_ROOT, ATS_APP_ROOT: APP_ROOT, ..._llmEnv(), ...(extraEnv || {}) } });
    } catch (err) { return resolve({ status: 'error', message: err.message }); }
    proc.stdout.on('data', (chunk) => {
      for (const raw of chunk.toString().split('\n')) {
        const line = raw.trim();
        if (!line) continue;
        try {
          const evt = JSON.parse(line);
          if (evt.event === 'log') { if (progressChannel) event.sender.send(progressChannel, { message: evt.message }); }
          else if (evt.event === 'result') result = evt;
          else if (evt.status) result = evt;
        } catch (e2) { if (progressChannel) event.sender.send(progressChannel, { message: line }); }
      }
    });
    proc.stderr.on('data', (d) => { if (progressChannel) event.sender.send(progressChannel, { message: 'STDERR: ' + d.toString().trim() }); });
    proc.on('close', (code) => resolve(result || { status: code === 0 ? 'success' : 'error', message: 'engine exited ' + code }));
    proc.on('error', (err) => resolve({ status: 'error', message: err.message }));
  });
}

// Projects (project_store.py CLI emits a single JSON status line)
ipcMain.handle('list-projects', async (e) => runEngine(e, [PROJECT_STORE, 'list'], null));
ipcMain.handle('create-project', async (e, { name, baseUrl, environment, apps }) => {
  // Multi-app projects (e.g. storefront + admin panel) go through create-json.
  if (Array.isArray(apps) && apps.length) {
    const spec = JSON.stringify({ name, baseUrl: baseUrl || '', environment: environment || 'dev', apps });
    return runEngine(e, [PROJECT_STORE, 'create-json', spec], null);
  }
  return runEngine(e, [PROJECT_STORE, 'create', name, baseUrl, environment || 'dev'], null);
});
ipcMain.handle('set-active-project', async (e, { projectId }) =>
  runEngine(e, [PROJECT_STORE, 'set-active', projectId], null));
ipcMain.handle('update-project', async (e, { projectId, patch }) =>
  runEngine(e, [PROJECT_STORE, 'update', projectId, JSON.stringify(patch || {})], null));
ipcMain.handle('delete-project', async (e, { projectId }) =>
  runEngine(e, [PROJECT_STORE, 'delete', projectId], null));
ipcMain.handle('capture-login', async (e, { projectId, url }) => {
  return new Promise((resolve) => {
    try {
      const authDir = path.join(DATA_ROOT, 'projects', projectId, 'auth');
      fs.mkdirSync(authDir, { recursive: true });
      const storageFile = path.join(authDir, 'storage_state.json');
      const proc = spawn(VENV_PYTHON, ['-m', 'playwright', 'codegen', '--channel', 'chrome', '--save-storage', storageFile, url || 'about:blank'],
        { cwd: DATA_ROOT, env: { ...process.env }, detached: false });
      proc.on('close', (code) => resolve({ status: code === 0 ? 'success' : 'error', captured: fs.existsSync(storageFile) }));
      proc.on('error', (err) => resolve({ status: 'error', message: err.message }));
    } catch (err) { resolve({ status: 'error', message: err.message }); }
  });
});

// ──────────────────────────────────────
// IPC: Authoring benchmark (engine/agent_bench.py) — run vs any provider, list scorecards
// ──────────────────────────────────────
let benchProcess = null;

function benchBroadcast(data) {
  for (const w of BrowserWindow.getAllWindows()) {
    if (!w.isDestroyed()) w.webContents.send('bench-event', data);
  }
}

ipcMain.handle('bench-run', async (event, opts) => {
  if (benchProcess) return { ok: false, error: 'a benchmark is already running' };
  if (!fs.existsSync(VENV_PYTHON)) return { ok: false, error: `Python not found at ${VENV_PYTHON}` };
  const provider = (opts && opts.provider) || '';
  const only = (opts && opts.only) || '';
  const args = [path.join(APP_ROOT, 'engine', 'agent_bench.py')];
  if (provider) args.push('--provider', provider);
  if (only) args.push('--only', only);
  benchProcess = spawn(VENV_PYTHON, args, {
    cwd: DATA_ROOT,
    env: { ...process.env, ATS_ROOT: DATA_ROOT, ATS_APP_ROOT: APP_ROOT, PYTHONPATH: APP_ROOT, ..._llmEnv(),
           PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    detached: process.platform !== 'win32', // own process group so bench-stop can kill the tree
  });
  const pid = benchProcess.pid;
  let buf = '';
  benchProcess.stdout.on('data', (chunk) => {
    buf += chunk.toString();
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).replace(/\r$/, '');
      buf = buf.slice(i + 1);
      if (line.trim()) benchBroadcast({ type: 'log', line });
    }
  });
  benchProcess.stderr.on('data', (chunk) => {
    const s = chunk.toString().trim();
    if (s) benchBroadcast({ type: 'log', line: `[stderr] ${s.slice(0, 300)}` });
  });
  benchProcess.on('close', (code) => {
    benchProcess = null;
    benchBroadcast({ type: 'exit', code });
  });
  benchBroadcast({ type: 'started', provider, pid });
  return { ok: true, pid };
});

ipcMain.handle('bench-stop', async () => {
  if (!benchProcess) return { ok: false, error: 'no benchmark running' };
  const pid = benchProcess.pid;
  try {
    // Kill the whole tree: the bench spawns agent processes which spawn Chrome.
    if (process.platform === 'win32') {
      require('child_process').execSync(`taskkill /F /T /PID ${pid}`, { timeout: 30000 });
    } else {
      // POSIX: kill the child's process group when it leads one, else the child itself.
      try { process.kill(-pid, 'SIGKILL'); } catch (_) { process.kill(pid, 'SIGKILL'); }
    }
  } catch (e) { /* may already be gone */ }
  benchProcess = null;
  benchBroadcast({ type: 'exit', code: -1, stopped: true });
  return { ok: true };
});

ipcMain.handle('bench-status', async () => ({ running: !!benchProcess, pid: benchProcess ? benchProcess.pid : null }));

ipcMain.handle('bench-results', async () => {
  const benchDir = path.join(RESULTS_DIR, '_agent_bench');
  if (!fs.existsSync(benchDir)) return [];
  const out = [];
  const dirs = fs.readdirSync(benchDir)
    .filter(f => { try { return fs.lstatSync(path.join(benchDir, f)).isDirectory(); } catch { return false; } })
    .sort().reverse();
  for (const d of dirs) {
    if (out.length >= 15) break;
    const p = path.join(benchDir, d, 'scorecard.json');
    if (!fs.existsSync(p)) continue;
    try {
      const sc = JSON.parse(fs.readFileSync(p, 'utf-8'));
      out.push({ id: d, generated: sc.generated, provider: sc.provider, env: sc.env,
                 scorecard: sc.scorecard, tasks: sc.tasks, folder: path.join(benchDir, d) });
    } catch { /* partial write — skip */ }
  }
  return out;
});

// ──────────────────────────────────────
// IPC: Run history
// ──────────────────────────────────────
ipcMain.handle('get-run-history', async () => {
  if (!fs.existsSync(RESULTS_DIR)) return [];

  const candidates = fs.readdirSync(RESULTS_DIR)
    .filter(f => {
      // Only real run folders, which are timestamped YYYY-MM-DD_HH-MM-SS. This excludes
      // helper dirs (_coverage, _agent_verify, _agent_eval, …) that have no run_metadata
      // and — because '_' sorts ABOVE digits — would otherwise top the list as empty
      // 0·0 / 0.0s history cards.
      if (!/^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$/.test(f)) return false;
      try { return fs.lstatSync(path.join(RESULTS_DIR, f)).isDirectory(); }
      catch { return false; }
    })
    .sort().reverse();

  // History is scoped to the ACTIVE project: a project sees only its own runs;
  // the built-in suite sees legacy runs (which have no project_id) + its own.
  const active = _activeProjectId();
  const out = [];
  for (const run of candidates) {
    if (out.length >= 10) break;
    const metaPath = path.join(RESULTS_DIR, run, 'run_metadata.json');
    let meta = {};
    try {
      if (fs.existsSync(metaPath)) {
        meta = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
      }
    } catch (e) { /* ignore corrupt metadata */ }
    if ((meta.project_id || null) !== (active || null)) continue;
    out.push({ id: run, ...meta });
  }
  return out;
});

// ──────────────────────────────────────
// IPC: Get run artifacts (videos, screenshots, report)
// ──────────────────────────────────────
ipcMain.handle('get-run-artifacts', async (event, runId) => {
  // Accept either a run ID (e.g. "2026-04-28_20-21-59") or full folder path
  let runDir;
  if (path.isAbsolute(runId)) {
    runDir = runId;
  } else {
    runDir = path.join(RESULTS_DIR, runId);
  }
  console.log('[get-run-artifacts] runId:', runId, '-> runDir:', runDir, 'exists:', fs.existsSync(runDir));
  if (!fs.existsSync(runDir)) return { videos: [], screenshots: [], report: null };

  const videosDir = path.join(runDir, 'videos');
  console.log('[get-run-artifacts] videosDir:', videosDir, 'exists:', fs.existsSync(videosDir));
  const screenshotsDir = path.join(runDir, 'screenshots');

  const videos = fs.existsSync(videosDir)
    ? fs.readdirSync(videosDir)
        .filter(f => f.endsWith('.webm'))
        .map(f => ({ name: f.replace('.webm', ''), path: path.join(videosDir, f) }))
        .sort((a, b) => a.name.localeCompare(b.name))
    : [];

  const screenshots = fs.existsSync(screenshotsDir)
    ? fs.readdirSync(screenshotsDir)
        .filter(f => f.endsWith('.png'))
        .map(f => ({ name: f.replace('_FAILED.png', ''), path: path.join(screenshotsDir, f) }))
        .sort((a, b) => a.name.localeCompare(b.name))
    : [];

  const tracesDir = path.join(runDir, 'traces');
  const traces = fs.existsSync(tracesDir)
    ? fs.readdirSync(tracesDir)
        .filter(f => f.endsWith('.zip'))
        .map(f => ({ name: f.replace('.zip', ''), path: path.join(tracesDir, f) }))
        .sort((a, b) => a.name.localeCompare(b.name))
    : [];

  // Self-heal events: which locators drifted and how they were recovered.
  const healsDir = path.join(runDir, 'heals');
  const heals = fs.existsSync(healsDir)
    ? fs.readdirSync(healsDir)
        .filter(f => f.endsWith('.json'))
        .map(f => {
          let events = [];
          try { events = (JSON.parse(fs.readFileSync(path.join(healsDir, f), 'utf8')).heals) || []; }
          catch (e) { /* skip corrupt */ }
          return { name: f.replace('.json', ''), count: events.length, events };
        })
        .sort((a, b) => a.name.localeCompare(b.name))
    : [];

  const reportFile = fs.readdirSync(runDir).find(f => f.endsWith('.xlsx'));
  const report = reportFile ? path.join(runDir, reportFile) : null;

  const junitFile = fs.readdirSync(runDir).find(f => f.endsWith('.xml'));
  const junit = junitFile ? path.join(runDir, junitFile) : null;

  return { videos, screenshots, traces, heals, report, junit, folder: runDir };
});

// Artifacts (screenshots/videos) for a single test case from its latest run — for the Jira attach picker.
ipcMain.handle('get-tc-artifacts', async (event, { tcId, runFolder } = {}) => {
  try {
    const folder = runFolder || _findLatestRunForTc(tcId);
    if (!folder || !fs.existsSync(folder)) return { videos: [], screenshots: [], folder: null };
    const tcNorm = String(tcId || '').replace(/-/g, '_');
    const pick = (sub, ext) => {
      const d = path.join(folder, sub);
      if (!fs.existsSync(d)) return [];
      return fs.readdirSync(d)
        .filter(f => f.endsWith(ext) && (f.includes(tcId) || f.startsWith(tcNorm)))
        .map(f => ({ name: f, path: path.join(d, f) }));
    };
    return { videos: pick('videos', '.webm'), screenshots: pick('screenshots', '.png'), folder };
  } catch (e) { return { videos: [], screenshots: [], folder: null, error: e.message }; }
});

// ──────────────────────────────────────
// IPC: Get run checkpoints
// ──────────────────────────────────────
ipcMain.handle('get-run-checkpoints', async (event, runId) => {
  let runDir;
  if (path.isAbsolute(runId)) {
    runDir = runId;
  } else {
    runDir = path.join(RESULTS_DIR, runId);
  }
  const cpDir = path.join(runDir, 'checkpoints');
  if (!fs.existsSync(cpDir)) return {};

  const result = {};
  for (const f of fs.readdirSync(cpDir).filter(f => f.endsWith('.json'))) {
    try {
      const data = JSON.parse(fs.readFileSync(path.join(cpDir, f), 'utf8'));
      if (data.tc_id && data.checkpoints) {
        result[data.tc_id] = data.checkpoints;
      }
    } catch (e) { /* skip corrupt files */ }
  }
  return result;
});

// ──────────────────────────────────────
// IPC: Get run network log (per-test API calls + payloads/responses) — keyed by file stem
// (the variant tc_id). Lazily loaded by ResultsView when a test row is expanded.
// ──────────────────────────────────────
ipcMain.handle('get-run-network', async (event, runId) => {
  const runDir = path.isAbsolute(runId) ? runId : path.join(RESULTS_DIR, runId);
  const netDir = path.join(runDir, 'network');
  if (!fs.existsSync(netDir)) return {};
  const result = {};
  for (const f of fs.readdirSync(netDir).filter(f => f.endsWith('.json'))) {
    try {
      const data = JSON.parse(fs.readFileSync(path.join(netDir, f), 'utf8'));
      result[f.replace(/\.json$/, '')] = (data && data.calls) || [];
    } catch (e) { /* skip corrupt files */ }
  }
  return result;
});

// ──────────────────────────────────────
// IPC: Record Test & Data Management
// ──────────────────────────────────────
// IPC: Record Test (Enhanced — returns structured steps for review)
// ──────────────────────────────────────
ipcMain.handle('record-test', async (event, { flowId, tcId, description, env, platform }) => {
  return new Promise((resolve, reject) => {
    try {
      const config = loadConfig();

      let baseUrl = '';
      let loginPath = 'login';

      if (platform && config.platforms && config.platforms[platform]) {
          baseUrl = config.platforms[platform].urls?.[env] || '';
          loginPath = config.platforms[platform].login_path || loginPath;
      } else if (config.environments?.[env]?.base_url) {
          baseUrl = config.environments[env].base_url;
      }

      if (!baseUrl) {
          resolve({ status: 'error', error: 'No base URL configured. Add platform URLs in Settings (config.json).' });
          return;
      }

      if (!baseUrl.endsWith('/')) baseUrl += '/';
      if (loginPath.startsWith('/')) loginPath = loginPath.substring(1);
      
      const startUrl = baseUrl + loginPath;

      const tempFile = path.join(DATA_ROOT, 'temp_recording.py');

      // Clean up any previous temp file
      try { fs.unlinkSync(tempFile); } catch (e) { /* ok */ }

      // ── Persist login across recordings ──
      // codegen saves the browser storage state on close (--save-storage) and
      // reloads it next time (--load-storage), so you log in ONCE per
      // platform/env and every later recording starts already authenticated —
      // no repetitive logins. When a saved session exists we open the app home
      // instead of the login page.
      const authDir = path.join(DATA_ROOT, '.auth');
      try { fs.mkdirSync(authDir, { recursive: true }); } catch (e) { /* ok */ }
      const storageFile = path.join(authDir, `${platform || 'seller'}_${env || 'dev'}_storage.json`);
      const hasStorage = fs.existsSync(storageFile);
      const recordUrl = hasStorage ? baseUrl : startUrl;

      const codegenArgs = [
        '-m', 'playwright', 'codegen',
        '--target', 'python-pytest',
        '--channel', 'chrome',
        '-o', tempFile,
      ];
      if (hasStorage) codegenArgs.push('--load-storage', storageFile);
      codegenArgs.push('--save-storage', storageFile);
      codegenArgs.push(recordUrl);

      const codegenProcess = spawn(VENV_PYTHON, codegenArgs, {
        cwd: DATA_ROOT,
        env: { ...process.env },
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: false,
        detached: true
      });
      codegenProcess.stderr?.on('data', (d) => {
        console.log('[codegen stderr]', d.toString());
      });

      // Live polling: send step count updates to renderer every 2s
      let pollTimer = setInterval(() => {
        try {
          if (fs.existsSync(tempFile)) {
            const content = fs.readFileSync(tempFile, 'utf8');
            const actionCount = (content.match(/\.(click|fill|type|press|select_option|check|goto|dblclick|hover)\(/g) || []).length;
            event.sender.send('recording-progress', { steps: actionCount });
          }
        } catch (e) { /* ignore polling errors */ }
      }, 2000);

      codegenProcess.on('close', (code) => {
        clearInterval(pollTimer);

        if (code !== 0) {
          resolve({ status: 'error', message: `Codegen exited with code ${code}` });
          return;
        }

        // Parse the recorded steps into structured JSON
        const PARSER_SCRIPT = path.join(APP_ROOT, 'engine', 'recorder_parser.py');
        const parserProcess = spawn(VENV_PYTHON, [
          PARSER_SCRIPT, 'parse', DATA_ROOT
        ], { cwd: DATA_ROOT, env: { ...process.env, ATS_ROOT: DATA_ROOT, ATS_APP_ROOT: APP_ROOT } });

        let out = '';
        parserProcess.stdout.on('data', d => out += d.toString());

        parserProcess.on('close', (pcode) => {
          if (pcode === 0) {
            try {
              const lines = out.trim().split('\n');
              const result = JSON.parse(lines[lines.length - 1]);
              if (result.status === 'success') {
                // Return parsed steps to renderer for review UI
                resolve({
                  status: 'needs_review',
                  steps: result.steps,
                  tc_id: tcId,
                  description: description,
                  flowId: flowId
                });
              } else {
                resolve(result);
              }
            } catch (e) {
              resolve({ status: 'error', message: 'Failed to parse recording output: ' + e.message });
            }
          } else {
            resolve({ status: 'error', message: 'Parser script failed' });
          }
        });
      });
    } catch (err) {
      resolve({ status: 'error', message: err.message });
    }
  });
});

// ──────────────────────────────────────
// IPC: Save Recording Review (generate test from reviewed steps + assertions)
// ──────────────────────────────────────
ipcMain.handle('save-recording-review', async (event, payload) => {
  return new Promise((resolve, reject) => {
    try {
      const PARSER_SCRIPT = path.join(APP_ROOT, 'engine', 'recorder_parser.py');
      const parserProcess = spawn(VENV_PYTHON, [
        PARSER_SCRIPT, 'generate', DATA_ROOT
      ], { cwd: DATA_ROOT, env: { ...process.env, ATS_ROOT: DATA_ROOT, ATS_APP_ROOT: APP_ROOT } });

      let out = '';
      parserProcess.stdin.write(JSON.stringify(payload));
      parserProcess.stdin.end();
      parserProcess.stdout.on('data', d => out += d.toString());

      parserProcess.on('close', (pcode) => {
        if (pcode === 0) {
          try {
            const lines = out.trim().split('\n');
            const res = JSON.parse(lines[lines.length - 1]);
            resolve(res);
          } catch (e) {
            resolve({ status: 'error', message: 'Failed to parse generator output' });
          }
        } else {
          resolve({ status: 'error', message: 'Generator script failed' });
        }
      });
    } catch (err) {
      resolve({ status: 'error', message: err.message });
    }
  });
});

// ──────────────────────────────────────
// IPC: Analyze Coverage (replay TC + DOM snapshots → suggestions)
// ──────────────────────────────────────
ipcMain.handle('analyze-coverage', async (event, { flowId, tcId, headed, stopTerminal, variant }) => {
  return new Promise((resolve) => {
    try {
      const INSPECTOR_SCRIPT = path.join(APP_ROOT, 'engine', 'dom_inspector.py');
      const args = [INSPECTOR_SCRIPT, 'analyze', flowId, tcId];
      if (headed) args.push('--headed');
      if (stopTerminal) args.push('--stop-terminal');
      if (variant) args.push('--variant', variant);

      const proc = spawn(VENV_PYTHON, args, {
        cwd: DATA_ROOT,
        env: { ...process.env, ATS_ROOT: DATA_ROOT, ATS_APP_ROOT: APP_ROOT },
      });

      let stderrBuf = '';
      let resultEvent = null;

      proc.stdout.on('data', (chunk) => {
        const lines = chunk.toString().split('\n');
        for (const raw of lines) {
          const line = raw.trim();
          if (!line) continue;
          try {
            const evt = JSON.parse(line);
            if (evt.event === 'log') {
              event.sender.send('analyze-coverage-progress', { message: evt.message });
            } else if (evt.event === 'result') {
              resultEvent = evt;
            } else if (evt.status === 'error') {
              resultEvent = evt;
            }
          } catch (e) {
            // Non-JSON line — forward as log
            event.sender.send('analyze-coverage-progress', { message: line });
          }
        }
      });

      proc.stderr.on('data', (d) => { stderrBuf += d.toString(); });

      proc.on('close', (code) => {
        if (resultEvent) {
          resolve(resultEvent);
        } else if (code !== 0) {
          resolve({ status: 'error', message: `Inspector exited with code ${code}: ${stderrBuf.slice(-400)}` });
        } else {
          resolve({ status: 'error', message: 'Inspector finished without emitting a result event' });
        }
      });

      proc.on('error', (err) => {
        resolve({ status: 'error', message: 'Failed to start inspector: ' + err.message });
      });
    } catch (err) {
      resolve({ status: 'error', message: err.message });
    }
  });
});

ipcMain.handle('get-user-stories', async (event, { flowId }) => {
  try {
    const usFile = path.join(_flowsDir(),flowId, `${flowId}_user_stories.json`);
    if (fs.existsSync(usFile)) {
      return JSON.parse(fs.readFileSync(usFile, 'utf8'));
    }
    return {};
  } catch (e) {
    return {};
  }
});
ipcMain.handle('save-user-stories', async (event, { flowId, data }) => {
  try {
    const usFile = path.join(_flowsDir(),flowId, `${flowId}_user_stories.json`);
    fs.writeFileSync(usFile, JSON.stringify(data, null, 2), 'utf8');
    return { success: true };
  } catch (e) {
    return { success: false, error: e.message };
  }
});
ipcMain.handle('get-tc-meta', async (event, { flowId, tcId }) => {
  try {
    const tcFile = path.join(_flowsDir(),flowId, 'test_cases.json');
    if (fs.existsSync(tcFile)) {
      const tcs = JSON.parse(fs.readFileSync(tcFile, 'utf8'));
      const tc = tcs.find(t => t.tc_id === tcId);
      if (tc) return { heading: tc.description || '', description: tc.description || '', steps: tc.steps || [], expected_result: tc.expected_result || '', preconditions: tc.preconditions || '' };
    }
    return {};
  } catch (e) {
    return {};
  }
});
ipcMain.handle('save-tc-meta', async (event, { flowId, tcId, heading, description }) => {
  try {
    const tcFile = path.join(_flowsDir(),flowId, 'test_cases.json');
    if (fs.existsSync(tcFile)) {
      const tcs = JSON.parse(fs.readFileSync(tcFile, 'utf8'));
      const tc = tcs.find(t => t.tc_id === tcId);
      if (tc) {
        if (heading !== undefined) tc.description = heading;
        if (description !== undefined) tc.description = description;
        fs.writeFileSync(tcFile, JSON.stringify(tcs, null, 4), 'utf8');
        return { success: true };
      }
    }
    return { success: false, error: 'Test case not found' };
  } catch (e) {
    return { success: false, error: e.message };
  }
});
// IPC: Answer a paused test's manual_input() request (OTP etc.) — writes the
// response file the conftest fixture is polling for.
ipcMain.handle('manual-input-respond', async (event, { responsePath, value, cancel } = {}) => {
  try {
    const p = String(responsePath || '');
    // Containment: only ever write *.response.json inside a manual_input dir under results.
    if (!/[\\\/]manual_input[\\\/][0-9a-f]+\.response\.json$/.test(p) || !path.resolve(p).startsWith(path.resolve(RESULTS_DIR))) {
      return { status: 'error', message: 'invalid response path' };
    }
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, JSON.stringify(cancel ? { cancel: true } : { value: String(value == null ? '' : value) }));
    return { status: 'success' };
  } catch (err) { return { status: 'error', message: err.message }; }
});

// IPC: Create a new flow folder in the active suite (a flow = a tree folder).
ipcMain.handle('create-flow', async (event, { flowId } = {}) => {
  try {
    const slug = String(flowId || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
    if (!slug) return { status: 'error', message: 'Folder name is required' };
    const dir = path.join(_flowsDir(), slug);
    if (fs.existsSync(dir)) return { status: 'error', message: `Folder "${slug}" already exists` };
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, '__init__.py'), '');
    fs.writeFileSync(path.join(dir, 'test_cases.json'), '[]\n');
    fs.writeFileSync(path.join(dir, 'test_data.json'), '{}\n');
    return { status: 'success', flowId: slug };
  } catch (err) { return { status: 'error', message: err.message }; }
});

// IPC: Add a test case to a flow's test_cases.json. It starts as a SPEC entry
// (id/description/steps) — runnable code is authored via Record or the Agent.
ipcMain.handle('create-test-case', async (event, { flowId, tcId, description, preconditions, expectedResult, steps } = {}) => {
  try {
    const dir = path.join(_flowsDir(), flowId || '');
    if (!flowId || !fs.existsSync(dir)) return { status: 'error', message: `Folder "${flowId}" not found` };
    const id = String(tcId || '').trim().toUpperCase().replace(/\s+/g, '-');
    if (!/^TC(-[A-Z0-9]+)+$/.test(id)) return { status: 'error', message: 'TC ID must look like TC-AREA-001' };
    const tcFile = path.join(dir, 'test_cases.json');
    let list = [];
    if (fs.existsSync(tcFile)) { try { list = JSON.parse(fs.readFileSync(tcFile, 'utf8')) || []; } catch (e) { list = []; } }
    if (list.some(t => t && t.tc_id === id)) return { status: 'error', message: `${id} already exists in "${flowId}"` };
    list.push({
      tc_id: id,
      module: flowId,
      description: String(description || '').trim() || id,
      preconditions: String(preconditions || '').trim(),
      steps: (Array.isArray(steps) ? steps : []).map(s => String(s).trim()).filter(Boolean),
      expected_result: String(expectedResult || '').trim(),
    });
    fs.writeFileSync(tcFile, JSON.stringify(list, null, 4), 'utf8');
    return { status: 'success', tcId: id, flowId };
  } catch (err) { return { status: 'error', message: err.message }; }
});

// IPC: Clone test flows from another suite (built-in or another project) into
// the ACTIVE suite. Never overwrites an existing flow folder.
function _suiteFlowsDirFor(projectId) {
  return projectId ? path.join(DATA_ROOT, 'projects', projectId, 'tests', 'flows')
                   : path.join(DATA_ROOT, 'tests', 'flows');
}
function _listSuiteFlows(flowsDir) {
  const out = [];
  try {
    for (const name of fs.readdirSync(flowsDir)) {
      if (name.startsWith('_') || name.startsWith('.')) continue;
      const dir = path.join(flowsDir, name);
      try { if (!fs.lstatSync(dir).isDirectory()) continue; } catch (e) { continue; }
      let tcCount = 0;
      try { tcCount = (JSON.parse(fs.readFileSync(path.join(dir, 'test_cases.json'), 'utf8')) || []).length; } catch (e) { /* no tc file */ }
      out.push({ id: name, tcCount });
    }
  } catch (e) { /* missing dir */ }
  return out;
}
ipcMain.handle('list-clone-sources', async () => {
  try {
    const active = _activeProjectId();
    const sources = [];
    if (active !== null) {   // built-in suite is a source unless it IS the target
      sources.push({ id: null, name: 'ATS (built-in suite)', flows: _listSuiteFlows(_suiteFlowsDirFor(null)) });
    }
    const projDir = path.join(DATA_ROOT, 'projects');
    if (fs.existsSync(projDir)) {
      for (const pid of fs.readdirSync(projDir)) {
        if (pid === active) continue;
        const pf = path.join(projDir, pid, 'project.json');
        const flowsDir = _suiteFlowsDirFor(pid);
        if (!fs.existsSync(pf) || !fs.existsSync(flowsDir)) continue;
        let name = pid;
        try { name = JSON.parse(fs.readFileSync(pf, 'utf8')).name || pid; } catch (e) { /* keep id */ }
        const flows = _listSuiteFlows(flowsDir);
        if (flows.length) sources.push({ id: pid, name, flows });
      }
    }
    return { status: 'success', target: active, sources: sources.filter(s => s.flows.length) };
  } catch (err) { return { status: 'error', message: err.message }; }
});
ipcMain.handle('clone-tests', async (e, { sourceProjectId, flowIds } = {}) => {
  try {
    const active = _activeProjectId();
    if ((sourceProjectId || null) === (active || null)) return { status: 'error', message: 'Source and target are the same suite' };
    if (active) {   // make sure the target project has a suite (conftest shim etc.)
      await runEngine(e, [PROJECT_STORE, 'ensure-suite', active], null);
    }
    const srcRoot = _suiteFlowsDirFor(sourceProjectId || null);
    const dstRoot = _flowsDir();
    const copied = [], skipped = [];
    for (const flowId of (flowIds || [])) {
      const src = path.join(srcRoot, flowId);
      const dst = path.join(dstRoot, flowId);
      if (!fs.existsSync(src)) { skipped.push(`${flowId} (not found)`); continue; }
      if (fs.existsSync(dst)) { skipped.push(`${flowId} (already exists here)`); continue; }
      fs.cpSync(src, dst, {
        recursive: true,
        filter: (p) => !/__pycache__|\.pytest_cache/.test(p),
      });
      copied.push(flowId);
    }
    return { status: 'success', copied, skipped };
  } catch (err) { return { status: 'error', message: err.message }; }
});

// IPC: Delete test case — removes from test_cases.json, test_data.json, and Python file
ipcMain.handle('delete-test', async (event, { flowId, tcId }) => {
  try {
    const flowDir = path.join(_flowsDir(),flowId);
    const tcFile = path.join(flowDir, 'test_cases.json');
    const dataFile = path.join(flowDir, 'test_data.json');

    // 1. Remove from test_cases.json
    if (fs.existsSync(tcFile)) {
      const tcs = JSON.parse(fs.readFileSync(tcFile, 'utf8'));
      const filtered = tcs.filter(t => t.tc_id !== tcId);
      fs.writeFileSync(tcFile, JSON.stringify(filtered, null, 4), 'utf8');
    }

    // 2. Remove from test_data.json
    if (fs.existsSync(dataFile)) {
      const data = JSON.parse(fs.readFileSync(dataFile, 'utf8'));
      delete data[tcId];
      fs.writeFileSync(dataFile, JSON.stringify(data, null, 4), 'utf8');
    }

    // 3. Remove Python test function from the test file
    // TC IDs like TC-CATALOG-009 -> test function name: test_TC_CATALOG_009_*
    const tcUnderscore = tcId.replace(/-/g, '_');
    const pyFiles = fs.readdirSync(flowDir).filter(f => f.startsWith('test_') && f.endsWith('.py'));
    for (const pyFile of pyFiles) {
      const pyPath = path.join(flowDir, pyFile);
      const content = fs.readFileSync(pyPath, 'utf8');
      // Find the decorator + function for this TC
      const pattern = new RegExp(
        `(?:^|\\n)(@pytest\\.mark\\.tc\\("${tcId}"\\)[\\s\\S]*?def test_${tcUnderscore}[\\s\\S]*?(?=\\n@pytest|\\n\\nclass |\\n\\ndef [a-z]|\\Z))`,
        'm'
      );
      const match = content.match(pattern);
      if (match) {
        let newContent = content.replace(match[0], '');
        // Clean up extra blank lines
        newContent = newContent.replace(/\n{3,}/g, '\n\n');
        fs.writeFileSync(pyPath, newContent, 'utf8');
      }
    }

    return { success: true };
  } catch (e) {
    return { success: false, error: e.message };
  }
});
ipcMain.handle('get-tc-data', async (event, { flowId, tcId }) => {
  try {
    const dataFile = path.join(_flowsDir(),flowId, 'test_data.json');
    if (fs.existsSync(dataFile)) {
      const data = JSON.parse(fs.readFileSync(dataFile, 'utf-8'));
      const tcData = data[tcId] || {};
      // Detect variants format: dict of dicts
      const isVariant = typeof tcData === 'object' && tcData !== null &&
                        !Array.isArray(tcData) &&
                        Object.keys(tcData).length > 0 &&
                        Object.values(tcData).every(v => typeof v === 'object' && v !== null && !Array.isArray(v));
      if (isVariant) {
        return { isVariant: true, variants: tcData };
      }
      return { isVariant: false, data: tcData };
    }
    return { isVariant: false, data: {} };
  } catch (e) {
    return { isVariant: false, data: {} };
  }
});

ipcMain.handle('save-tc-data', async (event, { flowId, tcId, data, variant }) => {
  try {
    const dataFile = path.join(_flowsDir(),flowId, 'test_data.json');
    let allData = {};
    if (fs.existsSync(dataFile)) {
      allData = JSON.parse(fs.readFileSync(dataFile, 'utf-8'));
    }
    if (variant) {
      // Save specific variant
      if (!allData[tcId] || typeof allData[tcId] !== 'object' || Array.isArray(allData[tcId])) {
        allData[tcId] = {};
      } else {
        Object.keys(allData[tcId]).forEach(k => {
          if (typeof allData[tcId][k] !== 'object') {
            delete allData[tcId][k];
          }
        });
      }
      allData[tcId][variant] = data;
    } else {
      allData[tcId] = data;
    }
    fs.writeFileSync(dataFile, JSON.stringify(allData, null, 4), 'utf-8');
    return { success: true };
  } catch (e) {
    return { success: false, error: e.message };
  }
});

ipcMain.handle('add-tc-variant', async (event, { flowId, tcId, variantName, copyFrom }) => {
  try {
    const dataFile = path.join(_flowsDir(),flowId, 'test_data.json');
    let allData = {};
    if (fs.existsSync(dataFile)) {
      allData = JSON.parse(fs.readFileSync(dataFile, 'utf-8'));
    }
    let tcData = allData[tcId] || {};
    // Convert flat to variants if needed
    if (typeof tcData !== 'object' || Array.isArray(tcData) ||
        (Object.keys(tcData).length > 0 && !Object.values(tcData).every(v => typeof v === 'object'))) {
      tcData = Object.keys(tcData).length > 0 ? { default: tcData } : {};
    }
    if (copyFrom && tcData[copyFrom]) {
      tcData[variantName] = JSON.parse(JSON.stringify(tcData[copyFrom]));
    } else {
      tcData[variantName] = {};
    }
    allData[tcId] = tcData;
    fs.writeFileSync(dataFile, JSON.stringify(allData, null, 4), 'utf-8');
    return { success: true, variants: tcData };
  } catch (e) {
    return { success: false, error: e.message };
  }
});

ipcMain.handle('delete-tc-variant', async (event, { flowId, tcId, variantName }) => {
  try {
    const dataFile = path.join(_flowsDir(),flowId, 'test_data.json');
    if (!fs.existsSync(dataFile)) return { success: false, error: 'File not found' };
    const allData = JSON.parse(fs.readFileSync(dataFile, 'utf-8'));
    const tcData = allData[tcId];
    if (!tcData || typeof tcData !== 'object') return { success: false, error: 'TC not found' };
    delete tcData[variantName];
    const remaining = Object.keys(tcData);
    if (remaining.length === 1) {
      allData[tcId] = tcData[remaining[0]]; // flatten back
    } else if (remaining.length === 0) {
      delete allData[tcId];
    }
    fs.writeFileSync(dataFile, JSON.stringify(allData, null, 4), 'utf-8');
    return { success: true, variants: allData[tcId] || {} };
  } catch (e) {
    return { success: false, error: e.message };
  }
});

ipcMain.handle('rename-tc-variant', async (event, { flowId, tcId, oldName, newName }) => {
  try {
    const dataFile = path.join(_flowsDir(),flowId, 'test_data.json');
    if (!fs.existsSync(dataFile)) return { success: false, error: 'File not found' };
    const allData = JSON.parse(fs.readFileSync(dataFile, 'utf-8'));
    const tcData = allData[tcId];
    if (!tcData || !tcData[oldName]) return { success: false, error: 'Variant not found' };
    tcData[newName] = tcData[oldName];
    delete tcData[oldName];
    fs.writeFileSync(dataFile, JSON.stringify(allData, null, 4), 'utf-8');
    return { success: true, variants: tcData };
  } catch (e) {
    return { success: false, error: e.message };
  }
});

// ──────────────────────────────────────
// IPC: Bulk test data + Templates
// ──────────────────────────────────────
ipcMain.handle('get-bulk-tc-data', async (event, items) => {
  // items = [{flowId, tcId}, ...]
  const result = {};
  for (const { flowId, tcId } of items) {
    try {
      const dataFile = path.join(_flowsDir(),flowId, 'test_data.json');
      if (fs.existsSync(dataFile)) {
        const data = JSON.parse(fs.readFileSync(dataFile, 'utf-8'));
        result[tcId] = data[tcId] || {};
      } else {
        result[tcId] = {};
      }
    } catch (e) {
      result[tcId] = {};
    }
  }
  return result;
});

ipcMain.handle('save-bulk-tc-data', async (event, items) => {
  // items = [{flowId, tcId, data}, ...] — group by flowId to avoid file conflicts
  const byFlow = {};
  for (const { flowId, tcId, data } of items) {
    if (!byFlow[flowId]) byFlow[flowId] = {};
    byFlow[flowId][tcId] = data;
  }
  try {
    for (const [flowId, updates] of Object.entries(byFlow)) {
      const dataFile = path.join(_flowsDir(),flowId, 'test_data.json');
      let allData = {};
      if (fs.existsSync(dataFile)) {
        allData = JSON.parse(fs.readFileSync(dataFile, 'utf-8'));
      }
      Object.assign(allData, updates);
      fs.writeFileSync(dataFile, JSON.stringify(allData, null, 4), 'utf-8');
    }
    return { success: true };
  } catch (e) {
    return { success: false, error: e.message };
  }
});

const TEMPLATES_DIR = path.join(DATA_ROOT, 'templates');

ipcMain.handle('save-template', async (event, { name, data }) => {
  try {
    if (!fs.existsSync(TEMPLATES_DIR)) fs.mkdirSync(TEMPLATES_DIR, { recursive: true });
    const file = path.join(TEMPLATES_DIR, `${name}.json`);
    fs.writeFileSync(file, JSON.stringify(data, null, 2), 'utf-8');
    return { success: true };
  } catch (e) {
    return { success: false, error: e.message };
  }
});

ipcMain.handle('load-template', async (event, name) => {
  try {
    const file = path.join(TEMPLATES_DIR, `${name}.json`);
    if (!fs.existsSync(file)) return null;
    return JSON.parse(fs.readFileSync(file, 'utf-8'));
  } catch (e) {
    return null;
  }
});

ipcMain.handle('list-templates', async () => {
  try {
    if (!fs.existsSync(TEMPLATES_DIR)) return [];
    return fs.readdirSync(TEMPLATES_DIR)
      .filter(f => f.endsWith('.json'))
      .map(f => f.replace('.json', ''));
  } catch (e) {
    return [];
  }
});

// ──────────────────────────────────────
// Jira Integration
// ──────────────────────────────────────

function _getJiraConfig() {
  try {
    const config = loadConfig();
    const jira = config.jira || {};
    const apiKey = process.env.JIRA_API_KEY || jira.apiToken || '';
    // Strip any path from URL — Jira REST API only needs the origin
    // e.g. "https://x.atlassian.net/jira/core/projects" → "https://x.atlassian.net"
    let rawUrl = (jira.url || '').replace(/\/+$/, '');
    try {
      const parsed = new URL(rawUrl);
      rawUrl = parsed.origin;
    } catch (e) { /* keep as-is if not parseable */ }
    return {
      url: rawUrl,
      email: jira.email || '',
      apiKey,
      projectKey: jira.projectKey || '',
    };
  } catch (e) {
    return { url: '', email: '', apiKey: '', projectKey: '' };
  }
}

function _jiraRequest(config, method, apiPath, body) {
  return new Promise((resolve, reject) => {
    const url = new URL(`${config.url}/rest/api/2${apiPath}`);
    const auth = Buffer.from(`${config.email}:${config.apiKey}`).toString('base64');
    const bodyStr = body ? JSON.stringify(body) : null;

    const opts = {
      hostname: url.hostname,
      port: 443,
      path: url.pathname + url.search,
      method,
      headers: {
        'Authorization': `Basic ${auth}`,
        'Accept': 'application/json',
        'Content-Type': 'application/json',
      },
    };
    if (bodyStr) opts.headers['Content-Length'] = Buffer.byteLength(bodyStr);

    const req = https.request(opts, (res) => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
        catch { resolve({ status: res.statusCode, body: data }); }
      });
    });
    req.on('error', reject);
    if (bodyStr) req.write(bodyStr);
    req.end();
  });
}

function _jiraUploadAttachment(config, issueKey, filePath, fileName) {
  return new Promise((resolve, reject) => {
    if (!fs.existsSync(filePath)) return resolve({ status: 404, body: 'File not found' });
    const fileContent = fs.readFileSync(filePath);
    const boundary = '----ATSFormBoundary' + Date.now();

    let body = `--${boundary}\r\n`;
    body += `Content-Disposition: form-data; name="file"; filename="${fileName}"\r\n`;
    body += `Content-Type: application/octet-stream\r\n\r\n`;
    body += fileContent.toString('binary');
    body += `\r\n--${boundary}--\r\n`;

    const url = new URL(`${config.url}/rest/api/2/issue/${issueKey}/attachments`);
    const auth = Buffer.from(`${config.email}:${config.apiKey}`).toString('base64');

    const opts = {
      hostname: url.hostname,
      port: 443,
      path: url.pathname,
      method: 'POST',
      headers: {
        'Authorization': `Basic ${auth}`,
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'X-Atlassian-Token': 'no-check',
        'Content-Length': Buffer.byteLength(body, 'binary'),
      },
    };

    const req = https.request(opts, (res) => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => resolve({ status: res.statusCode, body: data }));
    });
    req.on('error', reject);
    req.write(body, 'binary');
    req.end();
  });
}

function _buildBugDescription(tcId, description, errors, runFolder) {
  let text = `Test Case: ${tcId}\n`;
  text += `Status: FAILED\n`;
  text += `Environment: Dev\n`;
  text += `Detected by: QAmate (automated)\n`;
  if (description) text += `Description: ${description}\n`;
  text += `\n`;
  if (errors && errors.length) {
    text += `--- Errors Found ---\n`;
    errors.forEach(e => { text += `${e}\n`; });
    text += `\n`;
  }
  // Include checkpoint breakdown if available
  if (runFolder) {
    const cpDir = path.join(runFolder, 'checkpoints');
    const safeId = tcId.replace(/-/g, '_');
    const cpFile = path.join(cpDir, `${safeId}.json`);
    try {
      if (fs.existsSync(cpFile)) {
        const cpData = JSON.parse(fs.readFileSync(cpFile, 'utf8'));
        const cps = cpData.checkpoints || [];
        if (cps.length > 0) {
          text += `--- Checkpoints ---\n`;
          cps.forEach(cp => {
            const icon = cp.status === 'PASS' ? 'PASS' : cp.status === 'FAIL' ? 'FAIL' : 'SKIP';
            text += `[${icon}] ${cp.name}${cp.error ? ' — ' + cp.error.substring(0, 200) : ''}\n`;
          });
          text += `\n`;
        }
      }
    } catch (e) { /* ignore */ }
  }
  text += `--- Steps to Reproduce ---\n`;
  text += `1. Open QAmate\n`;
  text += `2. Select test case ${tcId}\n`;
  text += `3. Click Run\n`;
  text += `4. Observe failure\n`;
  return text;
}

function _buildStoryDescription(userStory) {
  const s = userStory.user_story || {};
  let text = '';
  if (s.role && s.want && s.benefit) {
    text += `As a ${s.role}, I want ${s.want}, so that ${s.benefit}.\n\n`;
  }
  if (s.acceptance_criteria && s.acceptance_criteria.length) {
    text += `Acceptance Criteria:\n`;
    s.acceptance_criteria.forEach((ac, i) => { text += `${i + 1}. ${ac}\n`; });
    text += `\n`;
  }
  return text;
}

// ── Save Jira config ──
ipcMain.handle('save-jira-config', async (event, jiraConfig) => {
  try {
    const config = loadConfig();
    config.jira = jiraConfig;
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2), 'utf-8');
    return { success: true };
  } catch (e) {
    return { success: false, error: e.message };
  }
});

// ── Test Jira connection ──
ipcMain.handle('test-jira-connection', async () => {
  const cfg = _getJiraConfig();
  if (!cfg.url || !cfg.email || !cfg.apiKey) {
    return { success: false, error: 'Jira not configured. Set URL, Email, and API Token.' };
  }
  try {
    const res = await _jiraRequest(cfg, 'GET', '/myself');
    if (res.status === 200 && res.body.displayName) {
      return { success: true, user: res.body.displayName, email: res.body.emailAddress };
    }
    return { success: false, error: `HTTP ${res.status}: ${JSON.stringify(res.body)}` };
  } catch (e) {
    return { success: false, error: e.message };
  }
});

// ── Helper: find latest run folder that contains artifacts for a TC ──
function _findLatestRunForTc(tcId) {
  if (!fs.existsSync(RESULTS_DIR)) return null;
  const runs = fs.readdirSync(RESULTS_DIR)
    .filter(f => {
      try { return fs.lstatSync(path.join(RESULTS_DIR, f)).isDirectory(); }
      catch { return false; }
    })
    .sort().reverse();
  for (const run of runs) {
    const runDir = path.join(RESULTS_DIR, run);
    const metaPath = path.join(runDir, 'run_metadata.json');
    try {
      if (fs.existsSync(metaPath)) {
        const meta = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
        if (meta.tc_ids && meta.tc_ids.includes(tcId)) return runDir;
      }
    } catch (e) { /* skip */ }
    // Fallback: check if any artifact filename contains the TC ID
    const vidDir = path.join(runDir, 'videos');
    const ssDir = path.join(runDir, 'screenshots');
    const hasVid = fs.existsSync(vidDir) && fs.readdirSync(vidDir).some(f => f.includes(tcId));
    const hasSs = fs.existsSync(ssDir) && fs.readdirSync(ssDir).some(f => f.includes(tcId));
    if (hasVid || hasSs) return runDir;
  }
  return null;
}

// ── Create Jira Bug from failed test ──
ipcMain.handle('create-jira-bug', async (event, { tcId, runFolder, errors, summary, labels, description }) => {
  const cfg = _getJiraConfig();
  if (!cfg.url || !cfg.email || !cfg.apiKey) {
    return { success: false, error: 'Jira not configured. Set URL, Email, and API Token in Settings.' };
  }
  try {
    // Auto-find latest run if no folder provided
    if (!runFolder) {
      runFolder = _findLatestRunForTc(tcId);
    }

    const finalSummary = summary || `BUG: ${tcId} — Automated test failure`;
    const finalDesc = description || _buildBugDescription(tcId, '', errors, runFolder);
    const finalLabels = labels && labels.length ? labels : ['automated-test', 'ats'];
    const body = {
      fields: {
        project: { key: cfg.projectKey },
        issuetype: { name: 'Bug' },
        summary: finalSummary,
        description: finalDesc,
        labels: finalLabels,
      },
    };
    const res = await _jiraRequest(cfg, 'POST', '/issue', body);
    if (res.status !== 201) {
      return { success: false, error: `Jira returned HTTP ${res.status}: ${JSON.stringify(res.body)}` };
    }
    const issueKey = res.body.key;
    let attachCount = 0;

    // Attach screenshots + videos
    if (runFolder) {
      const tcNorm = tcId.replace(/-/g, '_');
      // Screenshots
      const ssDir = path.join(runFolder, 'screenshots');
      if (fs.existsSync(ssDir)) {
        for (const f of fs.readdirSync(ssDir)) {
          if (f.startsWith(tcNorm) || f.includes(tcId)) {
            await _jiraUploadAttachment(cfg, issueKey, path.join(ssDir, f), f);
            attachCount++;
          }
        }
      }
      // Videos
      const vidDir = path.join(runFolder, 'videos');
      if (fs.existsSync(vidDir)) {
        for (const f of fs.readdirSync(vidDir)) {
          if (f.includes(tcId) || f.startsWith(tcNorm)) {
            await _jiraUploadAttachment(cfg, issueKey, path.join(vidDir, f), f);
            attachCount++;
          }
        }
      }
    }

    return { success: true, key: issueKey, url: `${cfg.url}/browse/${issueKey}`, attachments: attachCount };
  } catch (e) {
    return { success: false, error: e.message };
  }
});

// ── Create Jira Story from test case ──
ipcMain.handle('create-jira-story', async (event, { tcId, flowId, userStory, summary, labels, description }) => {
  const cfg = _getJiraConfig();
  if (!cfg.url || !cfg.email || !cfg.apiKey) {
    return { success: false, error: 'Jira not configured. Set URL, Email, and API Token in Settings.' };
  }
  try {
    const s = (userStory && userStory.user_story) ? userStory.user_story : {};
    const finalSummary = summary || s.summary || `${tcId} — ${flowId} test story`;
    const finalDesc = description || _buildStoryDescription(userStory || {});
    const finalLabels = labels && labels.length ? labels : ['ats-generated', flowId || 'general'];
    const body = {
      fields: {
        project: { key: cfg.projectKey },
        issuetype: { name: 'Story' },
        summary: finalSummary,
        description: finalDesc,
        labels: finalLabels,
      },
    };
    const res = await _jiraRequest(cfg, 'POST', '/issue', body);
    if (res.status !== 201) {
      return { success: false, error: `Jira returned HTTP ${res.status}: ${JSON.stringify(res.body)}` };
    }
    return { success: true, key: res.body.key, url: `${cfg.url}/browse/${res.body.key}` };
  } catch (e) {
    return { success: false, error: e.message };
  }
});

// ──────────────────────────────────────────────────────────────────────────
// Bug & Story store (local sidecar jira_items.json) + raise / comment / status
// Each item: { id, type:'bug'|'story', tcId, flowId, summary, description,
//   labels:[], status:'draft'|'raised', jiraKey, jiraUrl, jiraStatus,
//   runFolder, attachments:[paths], createdAt, updatedAt }
// ──────────────────────────────────────────────────────────────────────────
function _jiraItemsPath() { return path.join(DATA_ROOT, 'jira_items.json'); }
function _readJiraItems() {
  try {
    const p = _jiraItemsPath();
    if (!fs.existsSync(p)) return [];
    const data = JSON.parse(fs.readFileSync(p, 'utf8'));
    return Array.isArray(data) ? data : (data.items || []);
  } catch (e) { return []; }
}
function _writeJiraItems(items) {
  fs.writeFileSync(_jiraItemsPath(), JSON.stringify(items, null, 2), 'utf8');
}
// Attach a TC's run screenshots+videos (and any explicit files) to an issue.
async function _attachArtifacts(cfg, issueKey, { tcId, runFolder, files } = {}) {
  let count = 0;
  const upload = async (fp, name) => { try { await _jiraUploadAttachment(cfg, issueKey, fp, name); count++; } catch (e) {} };
  // Explicit file selection wins; otherwise auto-attach the TC's latest-run screenshots+videos.
  if (files && files.length) {
    for (const fp of files) { if (fp && fs.existsSync(fp)) await upload(fp, path.basename(fp)); }
    return count;
  }
  if (tcId) {
    const folder = runFolder || _findLatestRunForTc(tcId);
    if (folder) {
      const tcNorm = tcId.replace(/-/g, '_');
      for (const sub of ['screenshots', 'videos']) {
        const d = path.join(folder, sub);
        if (fs.existsSync(d)) {
          for (const f of fs.readdirSync(d)) {
            if (f.includes(tcId) || f.startsWith(tcNorm)) await upload(path.join(d, f), f);
          }
        }
      }
    }
  }
  return count;
}

ipcMain.handle('jira-items-list', async () => ({ success: true, items: _readJiraItems() }));

ipcMain.handle('jira-item-save', async (event, item) => {
  try {
    const items = _readJiraItems();
    const now = new Date().toISOString();
    if (item && item.id) {
      const i = items.findIndex(x => x.id === item.id);
      if (i >= 0) items[i] = { ...items[i], ...item, updatedAt: now };
      else items.push({ ...item, createdAt: now, updatedAt: now });
    } else {
      item = { ...item, id: 'JI-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        status: (item && item.status) || 'draft', createdAt: now, updatedAt: now };
      items.push(item);
    }
    _writeJiraItems(items);
    return { success: true, item: items.find(x => x.id === item.id), items };
  } catch (e) { return { success: false, error: e.message }; }
});

ipcMain.handle('jira-item-delete', async (event, { id }) => {
  try { const items = _readJiraItems().filter(x => x.id !== id); _writeJiraItems(items); return { success: true, items }; }
  catch (e) { return { success: false, error: e.message }; }
});

ipcMain.handle('jira-item-raise', async (event, { id }) => {
  const cfg = _getJiraConfig();
  if (!cfg.url || !cfg.email || !cfg.apiKey) return { success: false, error: 'Jira not configured. Set URL, Email, and API Token in Settings.' };
  const items = _readJiraItems();
  const item = items.find(x => x.id === id);
  if (!item) return { success: false, error: 'Item not found' };
  try {
    const isBug = item.type === 'bug';
    const labels = (item.labels && item.labels.length) ? item.labels : (isBug ? ['automated-test', 'ats'] : ['ats-generated']);
    const body = { fields: {
      project: { key: cfg.projectKey },
      issuetype: { name: isBug ? 'Bug' : 'Story' },
      summary: item.summary || `${isBug ? 'BUG' : 'STORY'}: ${item.tcId || ''}`,
      description: item.description || '',
      labels,
    } };
    const res = await _jiraRequest(cfg, 'POST', '/issue', body);
    if (res.status !== 201) return { success: false, error: `Jira HTTP ${res.status}: ${JSON.stringify(res.body)}` };
    const key = res.body.key;
    const attachCount = await _attachArtifacts(cfg, key, { tcId: item.tcId, runFolder: item.runFolder, files: item.attachments });
    item.status = 'raised'; item.jiraKey = key; item.jiraUrl = `${cfg.url}/browse/${key}`;
    item.jiraStatus = 'To Do'; item.updatedAt = new Date().toISOString();
    _writeJiraItems(items);
    return { success: true, item, key, url: item.jiraUrl, attachments: attachCount };
  } catch (e) { return { success: false, error: e.message }; }
});

ipcMain.handle('jira-issue-status', async (event, { key }) => {
  const cfg = _getJiraConfig();
  if (!cfg.url || !cfg.email || !cfg.apiKey) return { success: false, error: 'Jira not configured' };
  try {
    const res = await _jiraRequest(cfg, 'GET', `/issue/${key}?fields=status,summary`);
    if (res.status !== 200) return { success: false, error: `HTTP ${res.status}` };
    const st = res.body.fields && res.body.fields.status ? res.body.fields.status.name : null;
    return { success: true, status: st, summary: res.body.fields && res.body.fields.summary };
  } catch (e) { return { success: false, error: e.message }; }
});

ipcMain.handle('jira-add-comment', async (event, { key, body, attachments, tcId, runFolder }) => {
  const cfg = _getJiraConfig();
  if (!cfg.url || !cfg.email || !cfg.apiKey) return { success: false, error: 'Jira not configured. Set URL, Email, and API Token in Settings.' };
  try {
    let attachCount = 0;
    if ((attachments && attachments.length) || tcId) attachCount = await _attachArtifacts(cfg, key, { tcId, runFolder, files: attachments });
    if (body && body.trim()) {
      const res = await _jiraRequest(cfg, 'POST', `/issue/${key}/comment`, { body });
      if (res.status !== 201) return { success: false, error: `Comment HTTP ${res.status}: ${JSON.stringify(res.body)}` };
    }
    return { success: true, attachments: attachCount };
  } catch (e) { return { success: false, error: e.message }; }
});

