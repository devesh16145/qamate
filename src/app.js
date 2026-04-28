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
    const settingsBtn = document.getElementById('settings-btn');
    const settingsModal = document.getElementById('settings-modal');
    const closeModalBtn = document.querySelector('.close-modal');
    const saveSettingsBtn = document.getElementById('save-settings-btn');
    const historyList = document.getElementById('history-list');
    const lastRunCard = document.getElementById('last-run-card');

    // ── State ──
    let allFlows = [];
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
                        <span>${run.status || 'Unknown'}</span>
                    </div>
                    <div class="history-tc-list">
                        ${(run.tc_ids || []).map(id => `<span class="history-tc-tag">${id}</span>`).join('')}
                    </div>
                    <div class="history-stats">
                        ✅ ${run.passed || 0} | ❌ ${run.failed || 0} | ⏭ ${run.skipped || 0} | ⏱ ${run.duration || '—'}
                    </div>
                `;
                item.onclick = () => {
                    if (run.folder_path) {
                        loadArtifacts(run.folder_path);
                        showResultCard(run);
                    } else if (run.id) {
                        // Fallback: construct path from run ID
                        loadArtifacts(run.id);
                        showResultCard(run);
                    }
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
        addLog(`  Environment: ${envSelect.value} | Mode: ${config.execution?.default_mode || 'headless'} | Zoom: ${zoomSelect.value}%`, 'system');

        const options = {
            tc_ids: selectedTCs,
            env: envSelect.value,
            mode: config.execution?.default_mode || 'headless',
            parallel: config.execution?.parallel_workers || 1,
            zoom: zoomSelect.value,
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
                    // Delay artifact loading slightly to ensure video rename completes
                    setTimeout(() => {
                        if (data.summary.folder_path) {
                            loadArtifacts(data.summary.folder_path);
                        }
                    }, 2000);
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

    // ── Artifacts (videos, screenshots, reports) ──
    async function loadArtifacts(runIdOrPath) {
        const section = document.getElementById('artifacts-section');
        const list = document.getElementById('artifacts-list');
        const titleEl = document.getElementById('artifacts-title');
        list.innerHTML = '<div class="loading" style="font-size:11px;">Loading...</div>';
        section.classList.remove('hidden');

        // Extract run timestamp for display
        const runName = runIdOrPath.split(/[/\\]/).pop();
        titleEl.textContent = `── ARTIFACTS: ${runName.replace(/_/g, ' ')} ──`;

        try {
            addLog(`📦 Loading artifacts for: ${runIdOrPath}`, 'system');
            const artifacts = await window.ats.getRunArtifacts(runIdOrPath);
            list.innerHTML = '';

            addLog(`📦 Found ${artifacts.videos.length} videos, ${artifacts.screenshots.length} screenshots`, 'system');

            if (artifacts.videos.length === 0 && artifacts.screenshots.length === 0 && !artifacts.report) {
                list.innerHTML = '<div style="font-size:11px;color:var(--text-muted);">No artifacts found</div>';
                return;
            }

            // Videos
            if (artifacts.videos.length > 0) {
                const title = document.createElement('div');
                title.className = 'artifact-group-title';
                title.textContent = `Videos (${artifacts.videos.length})`;
                list.appendChild(title);

                artifacts.videos.forEach(v => {
                    const item = document.createElement('div');
                    item.className = 'artifact-item';
                    item.innerHTML = `<span class="artifact-icon">🎬</span><span class="artifact-name">${v.name}</span>`;
                    item.onclick = () => window.ats.openFile(v.path);
                    list.appendChild(item);
                });
            }

            // Screenshots (failures)
            if (artifacts.screenshots.length > 0) {
                const title = document.createElement('div');
                title.className = 'artifact-group-title';
                title.textContent = `Failed Screenshots (${artifacts.screenshots.length})`;
                list.appendChild(title);

                artifacts.screenshots.forEach(s => {
                    const item = document.createElement('div');
                    item.className = 'artifact-item fail-artifact';
                    item.innerHTML = `<span class="artifact-icon">📸</span><span class="artifact-name">${s.name}</span>`;
                    item.onclick = () => window.ats.openFile(s.path);
                    list.appendChild(item);
                });
            }

            // Report
            if (artifacts.report) {
                const title = document.createElement('div');
                title.className = 'artifact-group-title';
                title.textContent = 'Report';
                list.appendChild(title);

                const item = document.createElement('div');
                item.className = 'artifact-item report-artifact';
                item.innerHTML = `<span class="artifact-icon">📊</span><span class="artifact-name">Excel Report</span>`;
                item.onclick = () => window.ats.openFile(artifacts.report);
                list.appendChild(item);
            }
        } catch (err) {
            list.innerHTML = `<div style="font-size:11px;color:var(--accent-red);">Error: ${err.message}</div>`;
        }
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

    // Kick off
    init();
});
