const { app, BrowserWindow, ipcMain, shell } = require('electron');
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
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    title: "Agrim ATS - Automated Testing System",
    backgroundColor: '#0f0f1a',
  });

  mainWindow.loadFile(path.join(__dirname, 'src', 'index.html'));
  // mainWindow.webContents.openDevTools(); // Uncomment for debugging
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  killPython();
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

  const reportFile = fs.readdirSync(runDir).find(f => f.endsWith('.xlsx'));
  const report = reportFile ? path.join(runDir, reportFile) : null;

  const junitFile = fs.readdirSync(runDir).find(f => f.endsWith('.xml'));
  const junit = junitFile ? path.join(runDir, junitFile) : null;

  return { videos, screenshots, report, junit, folder: runDir };
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
// IPC: Record Test & Data Management
// ──────────────────────────────────────
// IPC: Record Test (Enhanced — returns structured steps for review)
// ──────────────────────────────────────
ipcMain.handle('record-test', async (event, { flowId, tcId, description, env }) => {
  return new Promise((resolve, reject) => {
    try {
      const configPath = path.join(__dirname, 'config.json');
      const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
      const baseUrl = config.environments[env]?.base_url || 'https://supplier-dev.agrim.app/';

      const tempFile = path.join(__dirname, 'temp_recording.py');

      // Clean up any previous temp file
      try { fs.unlinkSync(tempFile); } catch (e) { /* ok */ }

      const codegenProcess = spawn(VENV_PYTHON, [
        '-m', 'playwright', 'codegen',
        '--target', 'python-pytest',
        '-o', tempFile,
        baseUrl
      ], {
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

