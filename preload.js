const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('ats', {
  getFlows: () => ipcRenderer.invoke('get-test-flows'),
  runTests: (options) => ipcRenderer.invoke('run-tests', options),
  stopTests: () => ipcRenderer.invoke('stop-tests'),
  onProgress: (callback) => ipcRenderer.on('test-progress', (_, data) => callback(data)),
  onLog: (callback) => ipcRenderer.on('test-log', (_, data) => callback(data)),
  getHistory: () => ipcRenderer.invoke('get-run-history'),
  openReport: (path) => ipcRenderer.invoke('open-report', path),
  openFolder: (path) => ipcRenderer.invoke('open-folder', path),
  openFile: (path) => ipcRenderer.invoke('open-file', path),
  getRunArtifacts: (runId) => ipcRenderer.invoke('get-run-artifacts', runId),
  getConfig: () => ipcRenderer.invoke('get-config'),
  saveConfig: (config) => ipcRenderer.invoke('save-config', config),
  recordTest: (options) => ipcRenderer.invoke('record-test', options),
  getTcData: (options) => ipcRenderer.invoke('get-tc-data', options),
  saveTcData: (options) => ipcRenderer.invoke('save-tc-data', options),
});
