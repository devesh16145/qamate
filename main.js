const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

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
        env: { ...process.env }
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
      return data[tcId] || {};
    }
    return {};
  } catch (e) {
    return {};
  }
});

ipcMain.handle('save-tc-data', async (event, { flowId, tcId, data }) => {
  try {
    const dataFile = path.join(__dirname, 'tests', 'flows', flowId, 'test_data.json');
    let allData = {};
    if (fs.existsSync(dataFile)) {
      allData = JSON.parse(fs.readFileSync(dataFile, 'utf-8'));
    }
    allData[tcId] = data;
    fs.writeFileSync(dataFile, JSON.stringify(allData, null, 4), 'utf-8');
    return { success: true };
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

