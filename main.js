const { app, BrowserWindow, ipcMain, shell, safeStorage, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');
const https = require('https');

let mainWindow;
let pythonProcess = null;

const VENV_PYTHON = path.join(__dirname, 'venv', 'Scripts', 'python.exe');
const RUNNER_SCRIPT = path.join(__dirname, 'engine', 'runner.py');
const CONFIG_PATH = path.join(__dirname, 'config.json');
const RESULTS_DIR = path.join(__dirname, 'results');

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    title: "Agrim ATS - Automated Testing System",
    backgroundColor: '#0f0f1a',
  });

  mainWindow.loadFile(path.join(__dirname, 'src', 'index.html'));
  mainWindow.webContents.openDevTools();
}

app.whenReady().then(createWindow);

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
  const flowsDir = path.join(__dirname, 'tests', 'flows');
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
    sendToRenderer('test-log', `ERROR: Python not found at ${VENV_PYTHON}`);
    return;
  }

  sendToRenderer('test-log', `Launching test engine...`);

  pythonProcess = spawn(VENV_PYTHON, [RUNNER_SCRIPT], {
    cwd: __dirname,
    env: {
      ...process.env,
      PYTHONPATH: __dirname,
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
    zoom: options.zoom || '',
    userIndex: options.userIndex ?? 0,
    sellerUserIndex: options.sellerUserIndex ?? options.userIndex ?? 0,
    adminUserIndex: options.adminUserIndex ?? 0,
    variant: options.variant,
    project_id: options.project_id || options.projectId || null,
    ats_root: __dirname,
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
  return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
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
    const abs = path.isAbsolute(filePath) ? filePath : path.join(__dirname, filePath);
    shell.openPath(abs);
  }
});

ipcMain.handle('open-folder', async (event, folderPath) => {
  if (folderPath) {
    const abs = path.isAbsolute(folderPath) ? folderPath : path.join(__dirname, folderPath);
    shell.openPath(abs);
  }
});

ipcMain.handle('open-file', async (event, filePath) => {
  if (filePath) {
    const abs = path.isAbsolute(filePath) ? filePath : path.join(__dirname, filePath);
    shell.openPath(abs);
  }
});

// Open a Playwright trace (.zip) in the interactive Trace Viewer — time-travel
// debugging with DOM snapshots, network, console and per-action screenshots.
ipcMain.handle('open-trace', async (event, tracePath) => {
  if (!tracePath) return { status: 'error', message: 'No trace path provided' };
  const abs = path.isAbsolute(tracePath) ? tracePath : path.join(__dirname, tracePath);
  if (!fs.existsSync(abs)) return { status: 'error', message: 'Trace file not found' };
  try {
    const proc = spawn(VENV_PYTHON, ['-m', 'playwright', 'show-trace', abs], {
      cwd: __dirname,
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
const PROJECT_STORE = path.join(__dirname, 'engine', 'project_store.py');
const APP_EXPLORER = path.join(__dirname, 'engine', 'app_explorer.py');
const PRD_EXTRACTOR = path.join(__dirname, 'engine', 'prd_extractor.py');
const TEST_SYNTH = path.join(__dirname, 'engine', 'test_synthesizer.py');
const AGENT_RECORDER = path.join(__dirname, 'engine', 'agent_recorder.py');
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
  const s = _readSecrets();
  const env = {};
  for (const k of Object.keys(s)) if (k.startsWith('ATS_')) env[k] = s[k];
  return env;
}

// ── Conversational AI Agent (engine/agent_chat.py) — concurrent persisted sessions ─
// Each session is its own PERSISTENT child process driving its own browser, addressed by
// a sessionId. We keep a Map<sessionId,{proc,buf,projectId}>; every stdout {event:...} line
// is tagged with its sessionId and routed to the dedicated Agent window (not the IDE). The
// conversation/transcript/meta persist on disk under projects/<id>/agent_sessions/ — Python
// (engine/agent_sessions.py) owns messages.json; Node does the plain-JSON sidebar ops below.
const AGENT_CHAT = path.join(__dirname, 'engine', 'agent_chat.py');
const AGENT_MAX_SESSIONS = parseInt(process.env.ATS_AGENT_MAX_SESSIONS || '4', 10);
const agentProcs = new Map(); // sessionId -> { proc, buf, projectId }

let agentWindow = null;
function createAgentWindow() {
  if (agentWindow && !agentWindow.isDestroyed()) { agentWindow.focus(); return agentWindow; }
  agentWindow = new BrowserWindow({
    width: 1240, height: 840,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    title: 'Agrim ATS — AI Agent',
    backgroundColor: '#0f0f1a',
  });
  agentWindow.loadFile(path.join(__dirname, 'src', 'agent.html'));
  // Closing the Agent window does NOT stop its sessions — they keep running (persisted +
  // resumable) and stay visible in the docked panel or when the window is reopened. Sessions
  // end on explicit Stop or app quit (window-all-closed -> _agentKillAll).
  agentWindow.on('closed', () => { agentWindow = null; });
  return agentWindow;
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
    cwd: __dirname,
    env: { ...process.env, ATS_ROOT: __dirname, PYTHONUNBUFFERED: '1', ..._llmEnv() },
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
  if (agentProcs.has(sessionId)) return { status: 'success', already: true };  // already running
  if (agentProcs.size >= AGENT_MAX_SESSIONS) {
    return { status: 'error', message: `Too many concurrent sessions (max ${AGENT_MAX_SESSIONS}). Stop one before starting another.` };
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

ipcMain.handle('agent-reset', async (event, { sessionId } = {}) => (
  (sessionId && _agentWrite(sessionId, { action: 'reset' })) ? { status: 'success' } : { status: 'error', message: 'session not running' }
));

ipcMain.handle('agent-stop', async (event, { sessionId } = {}) => { if (sessionId) _agentKill(sessionId); return { status: 'success' }; });

// ── Session sidebar ops (plain JSON over projects/<id>/agent_sessions/). Path convention
// MUST match engine/agent_sessions.py `sessions_dir`. Python owns messages.json; Node only
// reads session.json/transcript.jsonl and does title-rename / delete here. ──
function _agentSessionsDir(projectId) {
  projectId = projectId || _activeProjectId();
  if (projectId) return path.join(__dirname, 'projects', projectId, 'agent_sessions');
  return path.join(__dirname, '.agent_context', 'agent_sessions');
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
    const idx = JSON.parse(fs.readFileSync(path.join(__dirname, 'projects', '_index.json'), 'utf8'));
    if (idx && idx.active && fs.existsSync(path.join(__dirname, 'projects', idx.active, 'project.json'))) return idx.active;
  } catch (e) { /* no active project */ }
  return null;
}

function _agentContextPaths(projectId) {
  projectId = projectId || _activeProjectId();
  if (projectId) {
    const pdir = path.join(__dirname, 'projects', projectId);
    let contextDir = path.join(pdir, 'context');
    try {
      const pj = JSON.parse(fs.readFileSync(path.join(pdir, 'project.json'), 'utf8'));
      if (pj && pj.context_dir && path.isAbsolute(pj.context_dir)) contextDir = pj.context_dir;
    } catch (e) { /* use default */ }
    return { contextDir, memoryPath: path.join(pdir, 'AGENT_MEMORY.md') };
  }
  const base = path.join(__dirname, '.agent_context');
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
  const pf = path.join(__dirname, 'projects', projectId, 'project.json');
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
  const s = _readSecrets();
  const keys = {};
  for (const k of Object.keys(s)) keys[k] = !!s[k];
  return { status: 'success', keys, available: safeStorage.isEncryptionAvailable() };
});

// Spawn a Python engine script; stream {event:"log"} lines to `progressChannel`,
// resolve with the final {event:"result"|status:...} object.
function runEngine(event, args, progressChannel, extraEnv) {
  return new Promise((resolve) => {
    let result = null;
    let proc;
    try {
      proc = spawn(VENV_PYTHON, args, { cwd: __dirname, env: { ...process.env, ATS_ROOT: __dirname, ...(extraEnv || {}) } });
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
ipcMain.handle('create-project', async (e, { name, baseUrl, environment }) =>
  runEngine(e, [PROJECT_STORE, 'create', name, baseUrl, environment || 'dev'], null));
ipcMain.handle('set-active-project', async (e, { projectId }) =>
  runEngine(e, [PROJECT_STORE, 'set-active', projectId], null));
ipcMain.handle('delete-project', async (e, { projectId }) =>
  runEngine(e, [PROJECT_STORE, 'delete', projectId], null));
ipcMain.handle('get-app-model', async (e, { projectId }) => {
  try { const p = path.join(__dirname, 'projects', projectId, 'app_model.json'); return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : null; }
  catch (err) { return null; }
});
ipcMain.handle('get-requirements', async (e, { projectId }) => {
  try { const p = path.join(__dirname, 'projects', projectId, 'requirements.json'); return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : null; }
  catch (err) { return null; }
});

// Pipeline (stream progress to 'autopilot-progress')
ipcMain.handle('explore-app', async (e, { projectId, maxPages, maxDepth, headed }) => {
  const args = [APP_EXPLORER, projectId];
  if (maxPages) args.push('--max-pages', String(maxPages));
  if (maxDepth) args.push('--max-depth', String(maxDepth));
  if (headed) args.push('--headed');
  return runEngine(e, args, 'autopilot-progress', _llmEnv());
});
ipcMain.handle('extract-requirements', async (e, { projectId, prdText, prdPath, provider }) => {
  let pth = prdPath;
  if (!pth && prdText != null) {
    pth = path.join(app.getPath('temp'), `ats_prd_${Date.now()}.md`);
    fs.writeFileSync(pth, prdText, 'utf8');
  }
  if (!pth) return { status: 'error', message: 'no PRD provided' };
  const args = [PRD_EXTRACTOR, pth, '--project', projectId];
  if (provider) args.push('--provider', provider);
  return runEngine(e, args, 'autopilot-progress', _llmEnv());
});
ipcMain.handle('synthesize-tests', async (e, { projectId, flowId, provider }) => {
  const args = [TEST_SYNTH, projectId];
  if (flowId) args.push('--flow', flowId);
  if (provider) args.push('--provider', provider);
  return runEngine(e, args, 'autopilot-progress', _llmEnv());
});

// Autonomous record: the LLM drives the browser to accomplish a goal and emits
// an ordinary test case via the existing recorder pipeline. Credentials (if
// given) are folded into the goal so the agent logs in first.
ipcMain.handle('agent-record', async (e, { projectId, goal, username, password, startPath, provider, maxSteps, tcId, flowId, headed }) => {
  let fullGoal = (goal && goal.trim()) || 'Explore the app and exercise its main happy-path flow, verifying the key results along the way.';
  if (username) {
    fullGoal = `First, log in with username "${username}" and password "${password || ''}". Then: ${fullGoal}`;
  }
  const args = [AGENT_RECORDER, projectId, '--goal', fullGoal,
    '--tc', tcId || ('TC-AUTO-' + String(Date.now()).slice(-6)),
    '--flow', flowId || 'autopilot'];
  if (startPath) args.push('--start', startPath);
  if (provider) args.push('--provider', provider);
  if (maxSteps) args.push('--max-steps', String(maxSteps));
  if (headed) args.push('--headed');
  return runEngine(e, args, 'autopilot-progress', _llmEnv());
});

// Capture a project's login once (Playwright codegen --save-storage). The
// generated code is discarded; we only keep the storage_state for reuse.
ipcMain.handle('capture-login', async (e, { projectId, url }) => {
  return new Promise((resolve) => {
    try {
      const authDir = path.join(__dirname, 'projects', projectId, 'auth');
      fs.mkdirSync(authDir, { recursive: true });
      const storageFile = path.join(authDir, 'storage_state.json');
      const proc = spawn(VENV_PYTHON, ['-m', 'playwright', 'codegen', '--channel', 'chrome', '--save-storage', storageFile, url || 'about:blank'],
        { cwd: __dirname, env: { ...process.env }, detached: false });
      proc.on('close', (code) => resolve({ status: code === 0 ? 'success' : 'error', captured: fs.existsSync(storageFile) }));
      proc.on('error', (err) => resolve({ status: 'error', message: err.message }));
    } catch (err) { resolve({ status: 'error', message: err.message }); }
  });
});

// ──────────────────────────────────────
// IPC: Run history
// ──────────────────────────────────────
ipcMain.handle('get-run-history', async () => {
  if (!fs.existsSync(RESULTS_DIR)) return [];

  const runs = fs.readdirSync(RESULTS_DIR)
    .filter(f => {
      try { return fs.lstatSync(path.join(RESULTS_DIR, f)).isDirectory(); }
      catch { return false; }
    })
    .sort().reverse().slice(0, 10);

  return runs.map(run => {
    const metaPath = path.join(RESULTS_DIR, run, 'run_metadata.json');
    let meta = {};
    try {
      if (fs.existsSync(metaPath)) {
        meta = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
      }
    } catch (e) { /* ignore corrupt metadata */ }
    return { id: run, ...meta };
  });
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
      const configPath = path.join(__dirname, 'config.json');
      const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
      
      let baseUrl = 'https://supplier-dev.agrim.app/';
      let loginPath = 'login';
      
      if (platform && config.platforms && config.platforms[platform]) {
          baseUrl = config.platforms[platform].urls[env] || baseUrl;
          loginPath = config.platforms[platform].login_path || loginPath;
      } else if (config.environments[env]?.base_url) {
          baseUrl = config.environments[env].base_url;
      }
      
      if (!baseUrl.endsWith('/')) baseUrl += '/';
      if (loginPath.startsWith('/')) loginPath = loginPath.substring(1);
      
      const startUrl = baseUrl + loginPath;

      const tempFile = path.join(__dirname, 'temp_recording.py');

      // Clean up any previous temp file
      try { fs.unlinkSync(tempFile); } catch (e) { /* ok */ }

      // ── Persist login across recordings ──
      // codegen saves the browser storage state on close (--save-storage) and
      // reloads it next time (--load-storage), so you log in ONCE per
      // platform/env and every later recording starts already authenticated —
      // no repetitive logins. When a saved session exists we open the app home
      // instead of the login page.
      const authDir = path.join(__dirname, '.auth');
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
        cwd: __dirname,
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
        const PARSER_SCRIPT = path.join(__dirname, 'engine', 'recorder_parser.py');
        const parserProcess = spawn(VENV_PYTHON, [
          PARSER_SCRIPT, 'parse', __dirname
        ], { cwd: __dirname });

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
      const PARSER_SCRIPT = path.join(__dirname, 'engine', 'recorder_parser.py');
      const parserProcess = spawn(VENV_PYTHON, [
        PARSER_SCRIPT, 'generate', __dirname
      ], { cwd: __dirname });

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
      const INSPECTOR_SCRIPT = path.join(__dirname, 'engine', 'dom_inspector.py');
      const args = [INSPECTOR_SCRIPT, 'analyze', flowId, tcId];
      if (headed) args.push('--headed');
      if (stopTerminal) args.push('--stop-terminal');
      if (variant) args.push('--variant', variant);

      const proc = spawn(VENV_PYTHON, args, {
        cwd: __dirname,
        env: { ...process.env, ATS_ROOT: __dirname },
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
    const usFile = path.join(__dirname, 'tests', 'flows', flowId, `${flowId}_user_stories.json`);
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
    const usFile = path.join(__dirname, 'tests', 'flows', flowId, `${flowId}_user_stories.json`);
    fs.writeFileSync(usFile, JSON.stringify(data, null, 2), 'utf8');
    return { success: true };
  } catch (e) {
    return { success: false, error: e.message };
  }
});
ipcMain.handle('get-tc-meta', async (event, { flowId, tcId }) => {
  try {
    const tcFile = path.join(__dirname, 'tests', 'flows', flowId, 'test_cases.json');
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
    const tcFile = path.join(__dirname, 'tests', 'flows', flowId, 'test_cases.json');
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
// IPC: Delete test case — removes from test_cases.json, test_data.json, and Python file
ipcMain.handle('delete-test', async (event, { flowId, tcId }) => {
  try {
    const flowDir = path.join(__dirname, 'tests', 'flows', flowId);
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
    const dataFile = path.join(__dirname, 'tests', 'flows', flowId, 'test_data.json');
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
    const dataFile = path.join(__dirname, 'tests', 'flows', flowId, 'test_data.json');
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
    const dataFile = path.join(__dirname, 'tests', 'flows', flowId, 'test_data.json');
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
    const dataFile = path.join(__dirname, 'tests', 'flows', flowId, 'test_data.json');
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
    const dataFile = path.join(__dirname, 'tests', 'flows', flowId, 'test_data.json');
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
      const dataFile = path.join(__dirname, 'tests', 'flows', flowId, 'test_data.json');
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
      const dataFile = path.join(__dirname, 'tests', 'flows', flowId, 'test_data.json');
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

const TEMPLATES_DIR = path.join(__dirname, 'templates');

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
    const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
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
      projectKey: jira.projectKey || 'PM',
    };
  } catch (e) {
    return { url: '', email: '', apiKey: '', projectKey: 'PM' };
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
  text += `Detected by: Agrim ATS (automated)\n`;
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
  text += `1. Open Agrim ATS\n`;
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
    const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
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

