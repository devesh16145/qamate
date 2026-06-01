const { contextBridge, ipcRenderer, webUtils } = require('electron');

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
  openTrace: (path) => ipcRenderer.invoke('open-trace', path),
  getRunArtifacts: (runId) => ipcRenderer.invoke('get-run-artifacts', runId),
  // ── Autonomous pipeline (projects, explore, PRD, synthesize) ──
  setSecret: (opts) => ipcRenderer.invoke('set-secret', opts),
  getSecretStatus: () => ipcRenderer.invoke('get-secret-status'),
  listProjects: () => ipcRenderer.invoke('list-projects'),
  createProject: (opts) => ipcRenderer.invoke('create-project', opts),
  setActiveProject: (opts) => ipcRenderer.invoke('set-active-project', opts),
  deleteProject: (opts) => ipcRenderer.invoke('delete-project', opts),
  getAppModel: (opts) => ipcRenderer.invoke('get-app-model', opts),
  getRequirements: (opts) => ipcRenderer.invoke('get-requirements', opts),
  exploreApp: (opts) => ipcRenderer.invoke('explore-app', opts),
  extractRequirements: (opts) => ipcRenderer.invoke('extract-requirements', opts),
  synthesizeTests: (opts) => ipcRenderer.invoke('synthesize-tests', opts),
  agentRecord: (opts) => ipcRenderer.invoke('agent-record', opts),
  captureLogin: (opts) => ipcRenderer.invoke('capture-login', opts),
  // ── Conversational AI agent (agent_chat.py): concurrent persisted sessions ──
  // Each call carries a sessionId; events arrive on 'agent-event' tagged with sessionId.
  agentOpenWindow: () => ipcRenderer.invoke('agent-open-window'),
  agentDock: () => ipcRenderer.invoke('agent-dock'),                     // from the floating window: dock into the IDE
  onAgentDockRequest: (callback) => {                                    // main window: react to a dock request
    const handler = () => callback();
    ipcRenderer.on('agent-dock-request', handler);
    return () => ipcRenderer.removeListener('agent-dock-request', handler);
  },
  agentStart: (opts) => ipcRenderer.invoke('agent-start', opts),         // {sessionId, projectId, provider, headed, env, title}
  agentSend: (opts) => ipcRenderer.invoke('agent-send', opts),           // {sessionId, message, attachments}
  agentReset: (opts) => ipcRenderer.invoke('agent-reset', opts),         // {sessionId}
  agentStop: (opts) => ipcRenderer.invoke('agent-stop', opts),           // {sessionId}
  // Session management (sidebar): list/new/rename/delete + transcript redraw
  agentListSessions: (opts) => ipcRenderer.invoke('agent-list-sessions', opts),       // {projectId}
  agentNewSession: (opts) => ipcRenderer.invoke('agent-new-session', opts),           // {projectId, title}
  agentRenameSession: (opts) => ipcRenderer.invoke('agent-rename-session', opts),     // {projectId, sessionId, title}
  agentDeleteSession: (opts) => ipcRenderer.invoke('agent-delete-session', opts),     // {projectId, sessionId}
  agentSessionTranscript: (opts) => ipcRenderer.invoke('agent-session-transcript', opts), // {projectId, sessionId}
  onAgentEvent: (callback) => {
    const handler = (_, data) => callback(data);
    ipcRenderer.on('agent-event', handler);
    return () => ipcRenderer.removeListener('agent-event', handler);
  },
  // ── AI agent file context: per-message attachments, context folder, memory ──
  agentPickFiles: () => ipcRenderer.invoke('agent-pick-files'),
  agentContextList: (opts) => ipcRenderer.invoke('agent-context-list', opts),     // {projectId, subdir} → {dir,dirs,files}
  agentContextFiles: (opts) => ipcRenderer.invoke('agent-context-files', opts),    // {projectId} → flat paths for @-mention
  agentContextAdd: (opts) => ipcRenderer.invoke('agent-context-add', opts),
  agentContextSetFolder: (opts) => ipcRenderer.invoke('agent-context-set-folder', opts), // attach a folder as project context_dir
  agentContextOpen: (opts) => ipcRenderer.invoke('agent-context-open', opts),
  agentMemoryGet: (opts) => ipcRenderer.invoke('agent-memory-get', opts),
  agentMemoryOpen: (opts) => ipcRenderer.invoke('agent-memory-open', opts),
  // Resolve a dropped File's absolute path (sandbox/contextIsolation-safe); falls
  // back to the legacy File.path if webUtils is unavailable.
  getPathForFile: (file) => {
    try { return webUtils && webUtils.getPathForFile ? webUtils.getPathForFile(file) : ((file && file.path) || ''); }
    catch (e) { return (file && file.path) || ''; }
  },
  onAutopilotProgress: (callback) => {
    const handler = (_, data) => callback(data);
    ipcRenderer.on('autopilot-progress', handler);
    return () => ipcRenderer.removeListener('autopilot-progress', handler);
  },
  getRunCheckpoints: (runId) => ipcRenderer.invoke('get-run-checkpoints', runId),
  getRunNetwork: (runId) => ipcRenderer.invoke('get-run-network', runId),
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
