document.addEventListener('DOMContentLoaded', async () => {
    // ── DOM Elements ──
    const flowTree = document.getElementById('flow-tree');
    const flowSearch = document.getElementById('flow-search');
    const runBtn = document.getElementById('run-btn');
    const stopBtn = document.getElementById('stop-btn');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const logConsole = document.getElementById('log-console');
    const envSelect = document.getElementById('env-select');
    const browserSelect = document.getElementById('browser-select');
    const zoomSelect = document.getElementById('viewport-select');
    const userSelect = document.getElementById('user-select');
    const execModeSelect = document.getElementById('exec-mode-select');
    const settingsBtn = document.getElementById('settings-btn');
    const settingsModal = document.getElementById('settings-modal');
    const closeModalBtn = document.querySelector('.close-modal');
    const saveSettingsBtn = document.getElementById('save-settings-btn');
    const historyList = document.getElementById('history-list');
    const lastRunCard = document.getElementById('last-run-card');
    const tdSection = document.getElementById('test-data-section');
    const tdEditor = document.getElementById('td-editor');
    const tdSelectedInfo = document.getElementById('td-selected-info');
    const tdStatus = document.getElementById('td-status');
    const tdSaveBtn = document.getElementById('td-save-btn');
    const tdTemplateSaveBtn = document.getElementById('td-template-save-btn');
    const tdTemplateLoadBtn = document.getElementById('td-template-load-btn');

    // ── State ──
    let allFlows = [];
    let currentTdData = {};  // { tcId: { ...data }, ... }
    let selectedTcItems = []; // [{flowId, tcId}, ...]
    let config = {};
    let isRunning = false;
    let totalTests = 0;
    let completedTests = 0;

    // ── Initialization ──
    async function init() {
        try {
            allFlows = await window.ats.getFlows();
            config = await window.ats.getConfig();
            renderFlows(allFlows);
            await renderHistory();
            updateUIFromConfig();
            addLog('ATS System Ready. Select test cases and click Run.', 'system');
        } catch (err) {
            addLog(`Initialization error: ${err.message}`, 'fail');
        }
    }

    // ── Render Flows Tree ──
    function renderFlows(flows) {
        flowTree.innerHTML = '';
        if (flows.length === 0) {
            flowTree.innerHTML = '<div class="loading">No test flows found.</div>';
            return;
        }

        flows.forEach(flow => {
            const moduleEl = document.createElement('div');
            moduleEl.className = 'flow-module';

            const icon = flow.id === 'catalog' ? '📦' : flow.id === 'sign_in' ? '🔐' : '📝';

            moduleEl.innerHTML = `
                <div class="module-header">
                    <span class="toggle-icon">▼</span>
                    <span class="module-title">${icon} ${flow.name} (${flow.test_cases.length})</span>
                    <div class="module-actions">
                        <button class="select-all-btn" title="Select all">All</button>
                        <button class="deselect-all-btn" title="Deselect all">None</button>
                    </div>
                </div>
                <div class="test-case-list">
                    ${flow.test_cases.map(tc => `
                        <div class="test-case-item-container">
                            <label class="test-case-item" title="Preconditions: ${tc.preconditions || 'None'}\nExpected: ${tc.expected_result || ''}">
                                <input type="checkbox" data-tc-id="${tc.tc_id}" data-module="${flow.id}">
                                <div class="tc-info">
                                    <span class="tc-id">${tc.tc_id}</span>
                                    <span class="tc-desc">${tc.description}</span>
                                </div>
                            </label>
                            <button class="icon-btn config-data-btn" data-tc-id="${tc.tc_id}" data-module="${flow.id}" title="Configure test data">📋</button>
                        </div>
                    `).join('')}
                </div>
            `;
            flowTree.appendChild(moduleEl);

            // Toggle collapse/expand
            moduleEl.querySelector('.module-header').addEventListener('click', (e) => {
                if (e.target.closest('.module-actions')) return;
                const list = moduleEl.querySelector('.test-case-list');
                const toggleIcon = moduleEl.querySelector('.toggle-icon');
                list.classList.toggle('hidden');
                toggleIcon.textContent = list.classList.contains('hidden') ? '▶' : '▼';
            });
            
            // Select All / Deselect All
            moduleEl.querySelector('.select-all-btn').addEventListener('click', () => {
                moduleEl.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
            });
            moduleEl.querySelector('.deselect-all-btn').addEventListener('click', () => {
                moduleEl.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
            });

            // Configure Data button listeners
            moduleEl.querySelectorAll('.config-data-btn').forEach(btn => {
                btn.addEventListener('click', () => openDataModal(btn.dataset.module, btn.dataset.tcId));
            });
        });
    }

    function truncate(str, len) {
        return str.length > len ? str.substring(0, len) + '...' : str;
    }

    function guessFlowId(tcIds) {
        // Map TC ID prefix to flow folder: TC-CATALOG-001 -> catalog, TC-SIGNIN-001 -> sign_in
        if (!tcIds.length) return 'catalog';
        const prefix = tcIds[0].split('-').slice(1, -1).join('-').toLowerCase();
        const flowMap = { 'catalog': 'catalog', 'signin': 'sign_in' };
        return flowMap[prefix] || prefix;
    }

    // ── Render History ──
    async function renderHistory() {
        try {
            const history = await window.ats.getHistory();
            historyList.innerHTML = '';
            if (history.length === 0) {
                historyList.innerHTML = '<div class="loading" style="font-size:12px;">No previous runs</div>';
                return;
            }
            history.forEach(run => {
                const item = document.createElement('div');
                item.className = 'history-item';
                const parts = (run.id || '').split('_');
                const dateStr = parts[0] || 'Unknown';
                const timeStr = (parts[1] || '').replace(/-/g, ':');

                item.innerHTML = `
                    <div class="history-info">
                        <strong>${dateStr} ${timeStr}</strong>
                        <span class="history-status">${run.status || 'Unknown'}</span>
                    </div>
                    <div class="history-tc-list">
                        ${(run.tc_ids || []).map(id => `<span class="history-tc-tag">${id}</span>`).join('')}
                    </div>
                    <div class="history-stats">
                        ✅ ${run.passed || 0} | ❌ ${run.failed || 0} | ⏭ ${run.skipped || 0} | ⏱ ${run.duration || '—'}
                    </div>
                    <div class="history-detail hidden"></div>
                `;

                const detailEl = item.querySelector('.history-detail');

                item.onclick = async () => {
                    // Toggle expand/collapse
                    const isExpanded = !detailEl.classList.contains('hidden');

                    // Collapse all others
                    historyList.querySelectorAll('.history-detail').forEach(d => d.classList.add('hidden'));

                    if (isExpanded) {
                        detailEl.classList.add('hidden');
                        return;
                    }

                    detailEl.classList.remove('hidden');
                    detailEl.innerHTML = '<div style="font-size:11px;color:var(--text-muted);padding:4px;">Loading...</div>';

                    showResultCard(run);

                    // Load test data for this run's TCs
                    const tcIds = run.tc_ids || [];
                    const flowId = guessFlowId(tcIds);
                    const items = tcIds.map(tcId => ({ flowId, tcId }));
                    const runPath = run.folder_path || run.id;

                    let html = '';
                    if (items.length > 0) {
                        try {
                            const bulkData = await window.ats.getBulkTcData(items);
                            html += `<div class="hd-section-title">Test Data</div>`;
                            for (const [tcId, data] of Object.entries(bulkData)) {
                                html += `<div class="hd-tc-block">
                                    <div class="hd-tc-id">${tcId}</div>
                                    <pre class="hd-tc-json">${JSON.stringify(data, null, 2)}</pre>
                                </div>`;
                            }
                        } catch (e) {
                            html += `<div style="color:var(--accent-red);font-size:10px;">Error loading data</div>`;
                        }
                    }

                    // Check for artifacts inline
                    try {
                        const arts = await window.ats.getRunArtifacts(runPath);
                        if (arts.videos.length > 0) {
                            html += `<div class="hd-section-title">Videos</div>`;
                            arts.videos.forEach(v => {
                                html += `<div class="hd-artifact" data-path="${v.path}">🎬 ${v.name}</div>`;
                            });
                        }
                        if (arts.screenshots.length > 0) {
                            html += `<div class="hd-section-title">Screenshots</div>`;
                            arts.screenshots.forEach(s => {
                                html += `<div class="hd-artifact" data-path="${s.path}">📸 ${s.name}</div>`;
                            });
                        }
                    } catch (e) { /* ignore */ }

                    detailEl.innerHTML = html || '<div style="font-size:11px;color:var(--text-muted);padding:4px;">No data</div>';

                    // Attach click handlers for artifacts
                    detailEl.querySelectorAll('.hd-artifact').forEach(el => {
                        el.onclick = (ev) => {
                            ev.stopPropagation();
                            window.ats.openFile(el.dataset.path);
                        };
                    });
                };
                historyList.appendChild(item);
            });
        } catch (err) {
            // Silently ignore history errors
        }
    }

    function updateUIFromConfig() {
        if (config.execution) {
            browserSelect.value = config.execution.browser || 'chromium';
            document.getElementById('mode-status').textContent = `Mode: ${config.execution.default_mode || 'headless'}`;
            document.getElementById('parallel-status').textContent = `Parallel: ${config.execution.parallel_workers || 1} workers`;
        }
        // Populate user selector
        if (config.users && config.users.length > 0) {
            const prev = userSelect.value;
            userSelect.innerHTML = config.users.map((u, i) =>
                `<option value="${i}">${u.label || 'User ' + (i+1)} (${u.email})</option>`
            ).join('');
            // Restore previous selection if still valid
            if (prev && parseInt(prev) < config.users.length) {
                userSelect.value = prev;
            }
        }
        // Update parallel status based on exec mode
        const execMode = execModeSelect.value;
        document.getElementById('parallel-status').textContent =
            execMode === 'parallel' ? `Parallel: ${config.execution?.parallel_workers || 1} workers` : 'Sequential: 1 session';
    }

    // ── Search ──
    flowSearch.addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase();
        document.querySelectorAll('.test-case-item-container').forEach(item => {
            const text = item.textContent.toLowerCase();
            item.style.display = text.includes(query) ? '' : 'none';
        });
    });

    // ── Run Tests ──
    runBtn.addEventListener('click', async () => {
        const selectedTCs = Array.from(document.querySelectorAll('input[type="checkbox"]:checked'))
            .map(cb => cb.dataset.tcId);

        if (selectedTCs.length === 0) {
            addLog('⚠ Please select at least one test case.', 'warn');
            return;
        }

        // Reset state
        isRunning = true;
        totalTests = selectedTCs.length;
        completedTests = 0;
        logConsole.innerHTML = '';
        progressBar.style.width = '0%';
        progressText.textContent = `0% (0/${totalTests})`;
        runBtn.disabled = true;
        stopBtn.disabled = false;
        lastRunCard.classList.add('hidden');

        addLog(`▶ Starting ${selectedTCs.length} test(s)...`, 'system');
        addLog(`  Environment: ${envSelect.value} | Mode: ${execModeSelect.value} | Zoom: ${zoomSelect.value}% | User: ${config.users?.[userSelect.value]?.label || 'Default'}`, 'system');

        const options = {
            tc_ids: selectedTCs,
            env: envSelect.value,
            mode: config.execution?.default_mode || 'headless',
            execMode: execModeSelect.value,
            parallel: execModeSelect.value === 'parallel' ? (config.execution?.parallel_workers || 1) : 1,
            zoom: zoomSelect.value,
            userIndex: parseInt(userSelect.value) || 0,
        };

        try {
            await window.ats.runTests(options);
        } catch (err) {
            addLog(`ERROR: ${err.message}`, 'fail');
            resetRunState();
        }
    });

    // ── Stop Tests ──
    stopBtn.addEventListener('click', async () => {
        await window.ats.stopTests();
        addLog('⏹ Execution stopped by user.', 'warn');
        resetRunState();
    });

    function resetRunState() {
        isRunning = false;
        runBtn.disabled = false;
        stopBtn.disabled = true;
    }

    // ── Listen for Progress Events from Python Engine ──
    window.ats.onProgress((data) => {
        if (!data || !data.event) return;

        switch (data.event) {
            case 'log':
                addLog(data.message || '');
                break;

            case 'tc_start':
                addLog(`🔄 Running: ${data.tc_id} — ${data.description || ''}`, 'system');
                break;

            case 'tc_result':
                completedTests++;
                if (data.status === 'PASS') {
                    addLog(`✅ ${data.tc_id} — PASSED`, 'pass');
                } else {
                    addLog(`❌ ${data.tc_id} — FAILED`, 'fail');
                }
                updateProgress();
                break;

            case 'progress':
                // Bulk update from engine
                completedTests = (data.passed || 0) + (data.failed || 0) + (data.skipped || 0);
                totalTests = Math.max(totalTests, data.total || totalTests);
                updateProgress();
                break;

            case 'run_complete':
                addLog('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━', 'system');
                addLog('✔ Execution Complete.', 'system');
                resetRunState();
                renderHistory();
                if (data.summary) {
                    showResultCard(data.summary);
                }
                break;

            default:
                // Unknown event — just log it
                if (data.message) addLog(data.message);
                break;
        }
    });

    // Raw log lines from stderr or non-JSON stdout
    window.ats.onLog((msg) => {
        if (msg && typeof msg === 'string') {
            addLog(msg);
        }
    });

    // ── UI Helpers ──
    function updateProgress() {
        const pct = totalTests > 0 ? Math.round((completedTests / totalTests) * 100) : 0;
        progressBar.style.width = `${pct}%`;
        progressText.textContent = `${pct}% (${completedTests}/${totalTests})`;
    }

    function addLog(msg, type = '') {
        const entry = document.createElement('div');
        entry.className = `log-entry ${type}`;
        const now = new Date().toLocaleTimeString('en-GB', { hour12: false });
        entry.textContent = `[${now}] ${msg}`;
        logConsole.appendChild(entry);
        logConsole.scrollTop = logConsole.scrollHeight;
    }

    function showResultCard(summary) {
        if (!summary) return;
        lastRunCard.classList.remove('hidden');
        document.getElementById('stat-passed').textContent = summary.passed || 0;
        document.getElementById('stat-failed').textContent = summary.failed || 0;
        document.getElementById('stat-skipped').textContent = summary.skipped || 0;
        document.getElementById('run-duration').textContent = summary.duration || '0s';
        document.getElementById('last-run-time').textContent = `Last Run: ${summary.timestamp || 'Just now'}`;

        document.getElementById('view-report-btn').onclick = () => {
            if (summary.report_path) window.ats.openReport(summary.report_path);
        };
        document.getElementById('open-folder-btn').onclick = () => {
            if (summary.folder_path) window.ats.openFolder(summary.folder_path);
        };
    }

    // ── Settings Modal ──
    settingsBtn.onclick = () => {
        document.getElementById('setting-mode').value = config.execution?.default_mode || 'headless';
        document.getElementById('setting-workers').value = config.execution?.parallel_workers || 4;

        const usersList = document.getElementById('users-config-list');
        usersList.innerHTML = (config.users || []).map((user, i) => `
            <div style="margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid #333;">
                <h4 style="color: #94a3b8; margin-bottom: 8px;">${user.label || 'User ' + (i + 1)}</h4>
                <div class="setting-item">
                    <label>Email</label>
                    <input type="text" class="user-email" data-index="${i}" value="${user.email || ''}">
                </div>
                <div class="setting-item">
                    <label>Password</label>
                    <input type="password" class="user-pass" data-index="${i}" value="${user.password || ''}">
                </div>
            </div>
        `).join('');

        settingsModal.classList.remove('hidden');
    };

    closeModalBtn.onclick = () => settingsModal.classList.add('hidden');

    saveSettingsBtn.onclick = async () => {
        config.execution.default_mode = document.getElementById('setting-mode').value;
        config.execution.parallel_workers = parseInt(document.getElementById('setting-workers').value) || 1;

        document.querySelectorAll('.user-email').forEach((input) => {
            const idx = parseInt(input.dataset.index);
            if (config.users[idx]) {
                config.users[idx].email = input.value;
            }
        });
        document.querySelectorAll('.user-pass').forEach((input) => {
            const idx = parseInt(input.dataset.index);
            if (config.users[idx]) {
                config.users[idx].password = input.value;
            }
        });

        await window.ats.saveConfig(config);
        updateUIFromConfig();
        settingsModal.classList.add('hidden');
        addLog('⚙ Settings saved.', 'system');
    };

    // ── Record Test Modal ──
    const recordModal = document.getElementById('record-modal');
    const recordBtn = document.getElementById('record-test-btn');
    const startRecordBtn = document.getElementById('start-record-btn');
    
    recordBtn.addEventListener('click', () => {
        const flowSelect = document.getElementById('record-flow');
        flowSelect.innerHTML = allFlows.map(f => `<option value="${f.id}">${f.name}</option>`).join('');
        
        // Auto-generate TC ID for the first flow
        updateRecordTcId();
        
        document.getElementById('record-desc').value = '';
        recordModal.classList.remove('hidden');
    });

    document.getElementById('record-flow').addEventListener('change', updateRecordTcId);

    function updateRecordTcId() {
        const flowId = document.getElementById('record-flow').value;
        const flow = allFlows.find(f => f.id === flowId);
        const modulePrefix = flowId.toUpperCase().replace(/_/g, '');
        
        if (!flow || !flow.test_cases || flow.test_cases.length === 0) {
            document.getElementById('record-tc-id').value = `TC-${modulePrefix}-001`;
            return;
        }
        
        let maxNum = 0;
        flow.test_cases.forEach(tc => {
            const parts = tc.tc_id.split('-');
            if (parts.length >= 3) {
                const num = parseInt(parts[parts.length - 1], 10);
                if (!isNaN(num) && num > maxNum) {
                    maxNum = num;
                }
            }
        });
        
        const nextNum = (maxNum + 1).toString().padStart(3, '0');
        document.getElementById('record-tc-id').value = `TC-${modulePrefix}-${nextNum}`;
    }

    startRecordBtn.addEventListener('click', async () => {
        const flowId = document.getElementById('record-flow').value;
        const tcId = document.getElementById('record-tc-id').value.trim();
        const desc = document.getElementById('record-desc').value.trim();
        
        if (!tcId || !desc) {
            alert("Please enter TC ID and Description");
            return;
        }

        recordModal.classList.add('hidden');
        addLog(`⏺ Launching recorder for ${tcId}...`, 'system');
        
        startRecordBtn.disabled = true;
        try {
            const res = await window.ats.recordTest({
                flowId, tcId, description: desc, env: envSelect.value
            });
            if (res.status === 'success') {
                addLog(`✅ Recording saved for ${tcId}! Auto-extracted ${Object.keys(res.data || {}).length} variables.`, 'pass');
                init(); // Refresh UI to show new test
            } else {
                addLog(`❌ Recording failed: ${res.message}`, 'fail');
            }
        } catch (e) {
            addLog(`❌ Recording error: ${e.message}`, 'fail');
        }
        startRecordBtn.disabled = false;
    });

    // ── Configure Data Modal ──
    const dataModal = document.getElementById('data-modal');
    const saveDataBtn = document.getElementById('save-data-btn');
    let currentDataFlow = null;
    let currentDataTc = null;

    async function openDataModal(flowId, tcId) {
        currentDataFlow = flowId;
        currentDataTc = tcId;
        document.getElementById('data-tc-label').textContent = `Data for ${tcId}`;
        
        const container = document.getElementById('data-fields-container');
        container.innerHTML = '<div class="loading">Loading...</div>';
        dataModal.classList.remove('hidden');

        const data = await window.ats.getTcData({ flowId, tcId });
        
        container.innerHTML = '';
        if (Object.keys(data).length === 0) {
            container.innerHTML = '<p>No data variables found for this test. Record a test with text inputs to auto-generate variables.</p>';
        } else {
            for (const [key, val] of Object.entries(data)) {
                container.innerHTML += `
                    <div class="setting-item">
                        <label>${key}</label>
                        <input type="text" class="data-input-field" data-key="${key}" value="${val}">
                    </div>
                `;
            }
        }
    }

    saveDataBtn.addEventListener('click', async () => {
        const inputs = document.querySelectorAll('.data-input-field');
        const data = {};
        inputs.forEach(input => {
            data[input.dataset.key] = input.value;
        });

        const res = await window.ats.saveTcData({ flowId: currentDataFlow, tcId: currentDataTc, data });
        if (res.success) {
            dataModal.classList.add('hidden');
            addLog(`🗄️ Test data updated for ${currentDataTc}`, 'system');
        } else {
            alert("Failed to save data: " + res.error);
        }
    });

    // Close all modals generic handler
    document.querySelectorAll('.close-modal').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.target.closest('.modal').classList.add('hidden');
        });
    });

    // ── Test Data Panel ──
    async function refreshTestDataPanel() {
        const checkboxes = Array.from(document.querySelectorAll('input[type="checkbox"][data-tc-id]:checked'));
        selectedTcItems = checkboxes.map(cb => ({
            flowId: cb.dataset.module,
            tcId: cb.dataset.tcId
        }));

        if (selectedTcItems.length === 0) {
            tdSection.classList.add('hidden');
            currentTdData = {};
            return;
        }

        tdSection.classList.remove('hidden');
        tdSelectedInfo.textContent = selectedTcItems.map(t => t.tcId).join(', ');

        try {
            currentTdData = await window.ats.getBulkTcData(selectedTcItems);
            tdEditor.value = JSON.stringify(currentTdData, null, 2);
            setTdStatus('');
        } catch (e) {
            setTdStatus('Error loading data: ' + e.message, 'error');
        }
    }

    function setTdStatus(msg, type = '') {
        tdStatus.textContent = msg;
        tdStatus.className = `td-status ${type}`;
        if (type === 'saved') {
            setTimeout(() => { if (tdStatus.textContent === msg) tdStatus.textContent = ''; }, 3000);
        }
    }

    function parseEditorData() {
        try {
            const parsed = JSON.parse(tdEditor.value);
            setTdStatus('');
            return parsed;
        } catch (e) {
            setTdStatus('Invalid JSON: ' + e.message, 'error');
            return null;
        }
    }

    // Save button
    tdSaveBtn.addEventListener('click', async () => {
        const parsed = parseEditorData();
        if (!parsed) return;

        const items = selectedTcItems.map(({ flowId, tcId }) => ({
            flowId,
            tcId,
            data: parsed[tcId] || {}
        }));

        const res = await window.ats.saveBulkTcData(items);
        if (res.success) {
            currentTdData = parsed;
            setTdStatus('Saved', 'saved');
            addLog('💾 Test data saved for ' + items.length + ' test case(s)', 'system');
        } else {
            setTdStatus('Save failed: ' + res.error, 'error');
        }
    });

    // Save template — use inline input (prompt() doesn't work in Electron)
    tdTemplateSaveBtn.addEventListener('click', async () => {
        const parsed = parseEditorData();
        if (!parsed) return;

        // Show inline name input
        tdStatus.innerHTML = '';
        const wrapper = document.createElement('span');
        wrapper.innerHTML = `Name: <input id="td-template-name-input" type="text" placeholder="template name" style="background:rgba(0,0,0,0.3);border:1px solid var(--border-color);color:white;padding:2px 6px;border-radius:3px;font-size:11px;width:100px;outline:none;"> <button style="background:var(--accent-green);border:none;color:white;padding:2px 8px;border-radius:3px;font-size:10px;cursor:pointer;">OK</button> <button style="background:transparent;border:1px solid var(--border-color);color:var(--text-muted);padding:2px 8px;border-radius:3px;font-size:10px;cursor:pointer;">Cancel</button>`;
        tdStatus.appendChild(wrapper);
        const nameInput = document.getElementById('td-template-name-input');
        nameInput.focus();

        const okBtn = wrapper.querySelector('button:first-of-type');
        const cancelBtn = wrapper.querySelector('button:last-of-type');

        const doSave = async () => {
            const name = nameInput.value.trim();
            if (!name) { setTdStatus('Template name required', 'error'); return; }
            const res = await window.ats.saveTemplate({ name, data: parsed });
            if (res.success) {
                setTdStatus(`Template "${name}" saved`, 'saved');
                addLog(`📋 Template "${name}" saved`, 'system');
            } else {
                setTdStatus('Save failed: ' + res.error, 'error');
            }
        };

        okBtn.onclick = doSave;
        cancelBtn.onclick = () => setTdStatus('');
        nameInput.onkeydown = (e) => { if (e.key === 'Enter') doSave(); if (e.key === 'Escape') setTdStatus(''); };
    });

    // Load template
    let templateDropdown = null;
    tdTemplateLoadBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        // Close existing dropdown
        if (templateDropdown) { templateDropdown.remove(); templateDropdown = null; return; }

        const templates = await window.ats.listTemplates();
        templateDropdown = document.createElement('div');
        templateDropdown.className = 'td-template-dropdown';

        if (templates.length === 0) {
            templateDropdown.innerHTML = '<div class="td-template-empty">No templates saved</div>';
        } else {
            templates.forEach(name => {
                const item = document.createElement('div');
                item.className = 'td-template-item';
                item.textContent = name;
                item.onclick = async () => {
                    const data = await window.ats.loadTemplate(name);
                    if (data) {
                        // Merge template data into current editor
                        const current = parseEditorData() || {};
                        const merged = { ...current, ...data };
                        tdEditor.value = JSON.stringify(merged, null, 2);
                        setTdStatus(`Loaded template "${name}"`, 'saved');
                    }
                    templateDropdown.remove();
                    templateDropdown = null;
                };
                templateDropdown.appendChild(item);
            });
        }

        // Position dropdown relative to the button
        document.body.appendChild(templateDropdown);
        const rect = tdTemplateLoadBtn.getBoundingClientRect();
        templateDropdown.style.position = 'fixed';
        templateDropdown.style.left = (rect.right - 160) + 'px';
        templateDropdown.style.top = (rect.bottom + 4) + 'px';
    });

    // Close template dropdown on outside click
    document.addEventListener('click', (e) => {
        if (templateDropdown && !templateDropdown.contains(e.target) && e.target !== tdTemplateLoadBtn) {
            templateDropdown.remove();
            templateDropdown = null;
        }
    });

    // Listen for checkbox changes to refresh test data panel
    flowTree.addEventListener('change', (e) => {
        if (e.target.type === 'checkbox' && e.target.dataset.tcId) {
            refreshTestDataPanel();
        }
    });

    // Update status bar when exec mode changes
    execModeSelect.addEventListener('change', () => {
        const execMode = execModeSelect.value;
        document.getElementById('parallel-status').textContent =
            execMode === 'parallel' ? `Parallel: ${config.execution?.parallel_workers || 1} workers` : 'Sequential: 1 session';
    });

    // Also refresh when select-all/deselect-all is clicked
    flowTree.addEventListener('click', (e) => {
        if (e.target.closest('.select-all-btn') || e.target.closest('.deselect-all-btn')) {
            setTimeout(refreshTestDataPanel, 50);
        }
    });

    // Kick off
    init();
});
