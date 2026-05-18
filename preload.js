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
  getRunCheckpoints: (runId) => ipcRenderer.invoke('get-run-checkpoints', runId),
  getConfig: () => ipcRenderer.invoke('get-config'),
  saveConfig: (config) => ipcRenderer.invoke('save-config', config),
  recordTest: (options) => ipcRenderer.invoke('record-test', options),
  saveRecordingReview: (payload) => ipcRenderer.invoke('save-recording-review', payload),
  onRecordingProgress: (callback) => {
    const handler = (_, data) => callback(data);
    ipcRenderer.on('recording-progress', handler);
    return () => ipcRenderer.removeListener('recording-progress', handler);
  },
  getTcData: (options) => ipcRenderer.invoke('get-tc-data', options),
  saveTcData: (options) => ipcRenderer.invoke('save-tc-data', options),
  addTcVariant: (options) => ipcRenderer.invoke('add-tc-variant', options),
  deleteTcVariant: (options) => ipcRenderer.invoke('delete-tc-variant', options),
  renameTcVariant: (options) => ipcRenderer.invoke('rename-tc-variant', options),
  getBulkTcData: (items) => ipcRenderer.invoke('get-bulk-tc-data', items),
  saveBulkTcData: (items) => ipcRenderer.invoke('save-bulk-tc-data', items),
  saveTemplate: (options) => ipcRenderer.invoke('save-template', options),
  loadTemplate: (name) => ipcRenderer.invoke('load-template', name),
  listTemplates: () => ipcRenderer.invoke('list-templates'),
  getUserStories: (options) => ipcRenderer.invoke('get-user-stories', options),
  saveUserStories: (options) => ipcRenderer.invoke('save-user-stories', options),
  getTcMeta: (options) => ipcRenderer.invoke('get-tc-meta', options),
  saveTcMeta: (options) => ipcRenderer.invoke('save-tc-meta', options),
  deleteTest: (options) => ipcRenderer.invoke('delete-test', options),
  // Coverage analyzer
  analyzeCoverage: (options) => ipcRenderer.invoke('analyze-coverage', options),
  onAnalyzeProgress: (callback) => {
    const handler = (_, data) => callback(data);
    ipcRenderer.on('analyze-coverage-progress', handler);
    return () => ipcRenderer.removeListener('analyze-coverage-progress', handler);
  },
  // Jira
  saveJiraConfig: (cfg) => ipcRenderer.invoke('save-jira-config', cfg),
  testJiraConnection: () => ipcRenderer.invoke('test-jira-connection'),
  createJiraBug: (options) => ipcRenderer.invoke('create-jira-bug', options),
  createJiraStory: (options) => ipcRenderer.invoke('create-jira-story', options),
});
