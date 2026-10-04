#!/usr/bin/env node
// Run a script with the project venv's Python on any OS: node scripts/run-python.js <script> [args...]
const path = require('path');
const { spawnSync } = require('child_process');
const { VENV_PYTHON } = (() => {
  const win = process.platform === 'win32';
  const root = path.join(__dirname, '..', 'venv');
  return { VENV_PYTHON: win ? path.join(root, 'Scripts', 'python.exe') : path.join(root, 'bin', 'python') };
})();
const r = spawnSync(VENV_PYTHON, process.argv.slice(2), { stdio: 'inherit' });
process.exit(r.status === null ? 1 : r.status);
