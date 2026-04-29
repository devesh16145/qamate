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
    const bottomPanel = document.getElementById('bottom-panel');
    const tdEditor = document.getElementById('td-editor');
    const tdSelectedInfo = document.getElementById('td-selected-info');
    const tdStatus = document.getElementById('td-status');
    const tdSaveBtn = document.getElementById('td-save-btn');
    const tdTemplateSaveBtn = document.getElementById('td-template-save-btn');
    const tdTemplateLoadBtn = document.getElementById('td-template-load-btn');
    const usContent = document.getElementById('us-content');
    const tsContent = document.getElementById('ts-content');
    const tabBtns = document.querySelectorAll('.tab-btn');

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
        addLog(`Launching recorder for ${tcId}... Close the browser when done.`, 'system');

        // Listen for live step count during recording
        const progressCleanup = window.ats.onRecordingProgress((data) => {
            addLog(`Recording... ${data.steps} actions captured so far`, 'system');
        });

        startRecordBtn.disabled = true;
        try {
            const res = await window.ats.recordTest({
                flowId, tcId, description: desc, env: envSelect.value
            });
            progressCleanup(); // remove listener

            if (res.status === 'needs_review') {
                // Open review modal with parsed steps
                openReviewModal(res.steps, res.tc_id, res.description, res.flowId);
                addLog(`Recording captured ${res.steps.length} steps. Review and add assertions.`, 'system');
            } else if (res.status === 'success') {
                addLog(`Recording saved for ${tcId}! Auto-extracted ${Object.keys(res.data || {}).length} variables.`, 'pass');
                init();
            } else {
                addLog(`Recording failed: ${res.message}`, 'fail');
            }
        } catch (e) {
            addLog(`Recording error: ${e.message}`, 'fail');
        }
        startRecordBtn.disabled = false;
    });

    // ── Recording Review Modal ──
    const reviewModal = document.getElementById('review-modal');
    const reviewStepsList = document.getElementById('review-steps-list');
    const reviewAssertionsList = document.getElementById('review-assertions-list');
    const reviewCriteriaList = document.getElementById('review-criteria-list');
    const reviewAbBuilder = document.getElementById('review-assertion-builder');
    const reviewCbBuilder = document.getElementById('review-criteria-builder');

    const STEP_ICONS = {
        navigate: '&#x1F310;', click: '&#x1F446;', fill: '&#x270F;',
        type: '&#x2328;', select: '&#x1F4C7;', check: '&#x2611;',
        dblclick: '&#x1F446;&#x1F446;', hover: '&#x1F447;', press: '&#x2328;',
        wait: '&#x23F3;', viewport: '&#x1F4FA;', other: '&#x25B6;'
    };

    let reviewState = { steps: [], assertions: [], criteria: [], tcId: '', desc: '', flowId: '' };

    function openReviewModal(steps, tcId, description, flowId) {
        reviewState = { steps, assertions: [], criteria: [], tcId, description, flowId };
        document.getElementById('review-modal-title').textContent = `Review Recording - ${tcId}`;
        document.getElementById('review-preconditions').value = '';
        document.getElementById('review-expected').value = '';
        reviewAbBuilder.classList.add('hidden');
        reviewCbBuilder.classList.add('hidden');
        renderReviewSteps();
        renderReviewAssertions();
        renderReviewCriteria();
        reviewModal.classList.remove('hidden');
    }

    function renderReviewSteps() {
        reviewStepsList.innerHTML = '';
        for (const step of reviewState.steps) {
            const icon = STEP_ICONS[step.type] || STEP_ICONS.other;
            const isInput = step.type === 'fill' || step.type === 'type';
            const el = document.createElement('div');
            el.className = 'review-step';
            el.innerHTML = `
                <div class="review-step-num">${step.id}</div>
                <div class="review-step-icon">${icon}</div>
                <div class="review-step-body">
                    <div class="review-step-desc">${step.targetDescription || step.type}</div>
                    ${isInput ? `
                    <div class="review-step-detail">
                        <label>Variable name:</label>
                        <input type="text" class="review-step-var-input" data-step-id="${step.id}" value="${step.varName || ''}">
                        <span class="review-step-val" title="${step.value || ''}">${step.value || ''}</span>
                    </div>` : ''}
                </div>
                <button class="review-step-assert-btn" data-step-id="${step.id}" title="Add assertion after this step">+ Assert</button>
            `;
            reviewStepsList.appendChild(el);
        }

        // Var name change handlers
        reviewStepsList.querySelectorAll('.review-step-var-input').forEach(inp => {
            inp.addEventListener('change', () => {
                const sid = parseInt(inp.dataset.stepId);
                const step = reviewState.steps.find(s => s.id === sid);
                if (step) step.varName = inp.value.trim();
            });
        });

        // Assert button handlers
        reviewStepsList.querySelectorAll('.review-step-assert-btn').forEach(btn => {
            btn.addEventListener('click', () => openAssertionBuilder(parseInt(btn.dataset.stepId)));
        });
    }

    function openAssertionBuilder(afterStep) {
        reviewCbBuilder.classList.add('hidden');
        document.getElementById('review-ab-step-num').textContent = afterStep;
        reviewAbBuilder.dataset.afterStep = afterStep;
        document.getElementById('review-ab-type').value = 'element_visible';
        document.getElementById('review-ab-selector').value = '';
        document.getElementById('review-ab-value').value = '';
        document.getElementById('review-ab-desc').value = '';
        document.getElementById('review-ab-count').value = '1';
        document.getElementById('review-ab-ms').value = '2000';
        updateAbFieldVisibility('element_visible');
        reviewAbBuilder.classList.remove('hidden');
        reviewAbBuilder.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function updateAbFieldVisibility(type) {
        const selectorRow = document.getElementById('review-ab-selector-row');
        const valueRow = document.getElementById('review-ab-value-row');
        const countRow = document.getElementById('review-ab-count-row');
        const msRow = document.getElementById('review-ab-ms-row');

        const needsSelector = [
            'element_visible', 'element_not_visible', 'element_contains_text',
            'element_has_text', 'element_has_value', 'element_enabled',
            'element_disabled', 'element_count'
        ];
        const needsValue = [
            'element_contains_text', 'element_has_text', 'element_has_value',
            'page_contains_text', 'page_not_contains_text',
            'url_contains', 'url_equals', 'title_contains', 'toast_contains'
        ];

        selectorRow.style.display = needsSelector.includes(type) ? 'flex' : 'none';
        valueRow.style.display = needsValue.includes(type) ? 'flex' : 'none';
        countRow.style.display = type === 'element_count' ? 'flex' : 'none';
        msRow.style.display = type === 'wait_ms' ? 'flex' : 'none';
    }

    document.getElementById('review-ab-type').addEventListener('change', (e) => {
        updateAbFieldVisibility(e.target.value);
    });

    // Add assertion
    document.getElementById('review-ab-add').addEventListener('click', () => {
        const afterStep = parseInt(reviewAbBuilder.dataset.afterStep);
        const type = document.getElementById('review-ab-type').value;
        const selector = document.getElementById('review-ab-selector').value.trim();
        const value = document.getElementById('review-ab-value').value.trim();
        const desc = document.getElementById('review-ab-desc').value.trim();
        const count = parseInt(document.getElementById('review-ab-count').value) || 1;
        const countOp = document.getElementById('review-ab-count-op').value;
        const ms = parseInt(document.getElementById('review-ab-ms').value) || 2000;

        const assertion = { afterStep, type, description: desc || type };
        if (selector) assertion.selector = selector;
        if (value) assertion.value = value;
        if (type === 'element_count') { assertion.count = count; assertion.operator = countOp; }
        if (type === 'wait_ms') { assertion.value = ms; }

        reviewState.assertions.push(assertion);
        reviewAbBuilder.classList.add('hidden');
        renderReviewAssertions();
        addLog(`Assertion added after step ${afterStep}: ${desc || type}`, 'system');
    });

    document.getElementById('review-ab-cancel').addEventListener('click', () => {
        reviewAbBuilder.classList.add('hidden');
    });

    function renderReviewAssertions() {
        const label = document.getElementById('review-assertions-label');
        if (reviewState.assertions.length === 0) {
            label.style.display = 'none';
            reviewAssertionsList.innerHTML = '';
            return;
        }
        label.style.display = '';
        reviewAssertionsList.innerHTML = '';
        reviewState.assertions.forEach((a, idx) => {
            const detail = [a.selector, a.value].filter(Boolean).join(' | ');
            const el = document.createElement('div');
            el.className = 'review-assertion-item';
            el.innerHTML = `
                <div class="review-assertion-step-badge">${a.afterStep}</div>
                <span class="review-assertion-type">${a.type.replace(/_/g, ' ')}</span>
                <span class="review-assertion-detail">${detail || a.description}</span>
                <button class="review-assertion-delete" data-idx="${idx}" title="Remove">&times;</button>
            `;
            reviewAssertionsList.appendChild(el);
        });
        reviewAssertionsList.querySelectorAll('.review-assertion-delete').forEach(btn => {
            btn.addEventListener('click', () => {
                reviewState.assertions.splice(parseInt(btn.dataset.idx), 1);
                renderReviewAssertions();
            });
        });
    }

    // Criteria builder (reuse similar pattern)
    document.getElementById('review-add-criteria-btn').addEventListener('click', () => {
        reviewAbBuilder.classList.add('hidden');
        document.getElementById('review-cb-type').value = 'success_toast';
        document.getElementById('review-cb-selector').value = '';
        document.getElementById('review-cb-value').value = '';
        document.getElementById('review-cb-desc').value = '';
        updateCbFieldVisibility('success_toast');
        reviewCbBuilder.classList.remove('hidden');
        reviewCbBuilder.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });

    function updateCbFieldVisibility(type) {
        const selectorRow = document.getElementById('review-cb-selector-row');
        const valueRow = document.getElementById('review-cb-value-row');
        const needsSelector = [
            'element_visible', 'element_not_visible', 'element_contains_text',
            'element_count', 'success_toast', 'no_error_toast'
        ];
        const needsValue = [
            'element_contains_text', 'page_contains_text', 'page_not_contains_text',
            'url_contains', 'url_equals', 'title_contains', 'toast_contains'
        ];
        selectorRow.style.display = needsSelector.includes(type) ? 'flex' : 'none';
        valueRow.style.display = needsValue.includes(type) ? 'flex' : 'none';
    }

    document.getElementById('review-cb-type').addEventListener('change', (e) => {
        updateCbFieldVisibility(e.target.value);
    });

    document.getElementById('review-cb-add').addEventListener('click', () => {
        const type = document.getElementById('review-cb-type').value;
        const selector = document.getElementById('review-cb-selector').value.trim();
        const value = document.getElementById('review-cb-value').value.trim();
        const desc = document.getElementById('review-cb-desc').value.trim();

        const criterion = { type, description: desc || type };
        if (selector) criterion.selector = selector;
        if (value) criterion.value = value;

        reviewState.criteria.push(criterion);
        reviewCbBuilder.classList.add('hidden');
        renderReviewCriteria();
        addLog(`Criteria added: ${desc || type}`, 'system');
    });

    document.getElementById('review-cb-cancel').addEventListener('click', () => {
        reviewCbBuilder.classList.add('hidden');
    });

    function renderReviewCriteria() {
        reviewCriteriaList.innerHTML = '';
        reviewState.criteria.forEach((c, idx) => {
            const detail = [c.selector, c.value].filter(Boolean).join(' | ');
            const el = document.createElement('div');
            el.className = 'review-assertion-item';
            el.innerHTML = `
                <div class="review-assertion-step-badge criteria-badge">&#x2714;</div>
                <span class="review-assertion-type">${c.type.replace(/_/g, ' ')}</span>
                <span class="review-assertion-detail">${detail || c.description}</span>
                <button class="review-assertion-delete" data-idx="${idx}" title="Remove">&times;</button>
            `;
            reviewCriteriaList.appendChild(el);
        });
        reviewCriteriaList.querySelectorAll('.review-assertion-delete').forEach(btn => {
            btn.addEventListener('click', () => {
                reviewState.criteria.splice(parseInt(btn.dataset.idx), 1);
                renderReviewCriteria();
            });
        });
    }

    // Save reviewed recording
    document.getElementById('review-save-btn').addEventListener('click', async () => {
        const btn = document.getElementById('review-save-btn');
        btn.disabled = true;
        btn.textContent = 'Saving...';

        const payload = {
            tc_id: reviewState.tcId,
            description: reviewState.description,
            flowId: reviewState.flowId,
            preconditions: document.getElementById('review-preconditions').value.trim(),
            expectedResult: document.getElementById('review-expected').value.trim(),
            steps: reviewState.steps,
            assertions: reviewState.assertions,
            criteria: reviewState.criteria,
        };

        try {
            const res = await window.ats.saveRecordingReview(payload);
            if (res.status === 'success') {
                const varCount = Object.keys(res.data || {}).length;
                const assertCount = res.assertions_count || 0;
                const criteriaCount = res.criteria_count || 0;
                addLog(`Test saved: ${res.tc_id} - ${varCount} variables, ${assertCount} assertions, ${criteriaCount} criteria`, 'pass');
                reviewModal.classList.add('hidden');
                init();
            } else {
                addLog(`Save failed: ${res.message}`, 'fail');
                alert('Failed to save: ' + (res.message || 'Unknown error'));
            }
        } catch (e) {
            addLog(`Save error: ${e.message}`, 'fail');
        }
        btn.disabled = false;
        btn.textContent = 'Save Test';
    });

    // Cancel review
    document.getElementById('review-cancel-btn').addEventListener('click', () => {
        reviewModal.classList.add('hidden');
        addLog('Recording review cancelled. Recording discarded.', 'system');
    });

    // Close button for review modal
    document.querySelector('[data-close="review-modal"]')?.addEventListener('click', () => {
        reviewModal.classList.add('hidden');
    });

    // ── Configure Data Modal ──
    const dataModal = document.getElementById('data-modal');
    const saveDataBtn = document.getElementById('save-data-btn');
    let currentDataFlow = null;
    let currentDataTc = null;

    async function openDataModal(flowId, tcId) {
        currentDataFlow = flowId;
        currentDataTc = tcId;
        document.getElementById('data-tc-label').textContent = tcId;
        const container = document.getElementById('data-fields-container');
        container.innerHTML = '<div class="loading">Loading...</div>';
        dataModal.classList.remove('hidden');

        // Load TC meta (heading + description)
        try {
            const meta = await window.ats.getTcMeta({ flowId, tcId });
            document.getElementById('tc-meta-heading').value = meta.heading || meta.description || '';
            document.getElementById('tc-meta-desc').value = meta.description || '';
        } catch (e) {
            document.getElementById('tc-meta-heading').value = '';
            document.getElementById('tc-meta-desc').value = '';
        }

        // Load test data variables
        const data = await window.ats.getTcData({ flowId, tcId });
        container.innerHTML = '';
        if (Object.keys(data).length === 0) {
            container.innerHTML = '<p style="font-size:12px;color:var(--text-muted);">No data variables found. Record a test with text inputs to auto-generate.</p>';
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
        // Save TC meta (heading + description)
        const heading = document.getElementById('tc-meta-heading').value.trim();
        const desc = document.getElementById('tc-meta-desc').value.trim();
        const metaRes = await window.ats.saveTcMeta({
            flowId: currentDataFlow, tcId: currentDataTc, heading, description: desc
        });

        // Save test data variables
        const inputs = document.querySelectorAll('.data-input-field');
        const data = {};
        inputs.forEach(input => {
            data[input.dataset.key] = input.value;
        });
        const dataRes = await window.ats.saveTcData({ flowId: currentDataFlow, tcId: currentDataTc, data });

        if (metaRes.success && dataRes.success) {
            dataModal.classList.add('hidden');
            addLog(`Updated ${currentDataTc} — heading, description & data saved`, 'system');
            init(); // Refresh flow tree to show updated heading
        } else if (dataRes.success) {
            dataModal.classList.add('hidden');
            addLog(`Data saved for ${currentDataTc}, but heading update failed`, 'warn');
        } else {
            alert("Failed to save: " + (dataRes.error || metaRes.error));
        }
    });

    // Close all modals generic handler
    document.querySelectorAll('.close-modal').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.target.closest('.modal').classList.add('hidden');
        });
    });

    // ── Tab Switching ──
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            tabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
        });
    });

    // ── Test Data / User Stories / Test Steps Panel ──
    async function refreshTestDataPanel() {
        const checkboxes = Array.from(document.querySelectorAll('input[type="checkbox"][data-tc-id]:checked'));
        selectedTcItems = checkboxes.map(cb => ({
            flowId: cb.dataset.module,
            tcId: cb.dataset.tcId
        }));

        if (selectedTcItems.length === 0) {
            bottomPanel.classList.add('hidden');
            currentTdData = {};
            return;
        }

        bottomPanel.classList.remove('hidden');
        tdSelectedInfo.textContent = selectedTcItems.map(t => t.tcId).join(', ');

        // Load test data
        try {
            currentTdData = await window.ats.getBulkTcData(selectedTcItems);
            tdEditor.value = JSON.stringify(currentTdData, null, 2);
            setTdStatus('');
        } catch (e) {
            setTdStatus('Error loading data: ' + e.message, 'error');
        }

        // Load user stories and test steps
        loadUserStoriesAndSteps();
    }

    async function loadUserStoriesAndSteps() {
        // Group selected items by flowId
        const flowIds = [...new Set(selectedTcItems.map(t => t.flowId))];
        let allStories = {};
        for (const fid of flowIds) {
            try {
                const data = await window.ats.getUserStories({ flowId: fid });
                Object.assign(allStories, data);
            } catch (e) { /* ignore */ }
        }
        renderUserStories(allStories);
        renderTestSteps(allStories);
    }

    // ── User Stories Rendering & Editing ──
    let currentStoriesData = {};

    function renderUserStories(stories) {
        currentStoriesData = stories;
        if (!selectedTcItems.length) {
            usContent.innerHTML = '<div class="us-placeholder">Select test cases to view their user stories.</div>';
            return;
        }
        let html = '';
        for (const { tcId, flowId } of selectedTcItems) {
            const us = stories[tcId];
            if (!us || !us.user_story) continue;
            const s = us.user_story;
            html += `<div class="us-card" data-tc-id="${tcId}" data-flow-id="${flowId}">
                <div class="us-card-header">
                    <div class="us-tc-header">${tcId}</div>
                    <button class="us-edit-btn" data-tc-id="${tcId}" data-flow-id="${flowId}" title="Edit user story">Edit</button>
                </div>
                <div class="us-title">${s.summary || ''}</div>
                <div class="us-story">As a <strong>${s.role || 'user'}</strong>, I want <strong>${s.want || ''}</strong>, so that <em>${s.benefit || ''}</em>.</div>
                ${s.acceptance_criteria && s.acceptance_criteria.length ? `
                <div class="us-ac-label">Acceptance Criteria</div>
                <ul class="us-ac-list">
                    ${s.acceptance_criteria.map(ac => `<li>${ac}</li>`).join('')}
                </ul>` : ''}
            </div>`;
        }
        usContent.innerHTML = html || '<div class="us-placeholder">No user stories found for selected test cases.</div>';
        // Attach edit handlers
        usContent.querySelectorAll('.us-edit-btn').forEach(btn => {
            btn.onclick = () => enterUserStoryEditMode(btn.dataset.tcId, btn.dataset.flowId);
        });
    }

    function enterUserStoryEditMode(tcId, flowId) {
        const us = currentStoriesData[tcId];
        if (!us || !us.user_story) return;
        const s = us.user_story;
        const card = usContent.querySelector(`.us-card[data-tc-id="${tcId}"]`);
        if (!card) return;

        card.classList.add('editing');
        card.innerHTML = `
            <div class="us-card-header">
                <div class="us-tc-header">${tcId}</div>
                <div class="us-edit-actions">
                    <button class="us-save-btn" data-tc-id="${tcId}" data-flow-id="${flowId}">Save</button>
                    <button class="us-cancel-btn" data-tc-id="${tcId}" data-flow-id="${flowId}">Cancel</button>
                </div>
            </div>
            <div class="us-field">
                <label>Summary</label>
                <input type="text" class="us-input" data-field="summary" value="${(s.summary || '').replace(/"/g, '&quot;')}">
            </div>
            <div class="us-field">
                <label>Role</label>
                <input type="text" class="us-input" data-field="role" value="${(s.role || '').replace(/"/g, '&quot;')}">
            </div>
            <div class="us-field">
                <label>I Want</label>
                <textarea class="us-textarea" data-field="want" rows="2">${s.want || ''}</textarea>
            </div>
            <div class="us-field">
                <label>So That</label>
                <textarea class="us-textarea" data-field="benefit" rows="2">${s.benefit || ''}</textarea>
            </div>
            <div class="us-field">
                <label>Acceptance Criteria (one per line)</label>
                <textarea class="us-textarea" data-field="acceptance_criteria" rows="4">${(s.acceptance_criteria || []).join('\n')}</textarea>
            </div>
        `;

        card.querySelector('.us-save-btn').onclick = () => saveUserStoryEdit(tcId, flowId);
        card.querySelector('.us-cancel-btn').onclick = () => renderUserStories(currentStoriesData);
    }

    async function saveUserStoryEdit(tcId, flowId) {
        const card = usContent.querySelector(`.us-card[data-tc-id="${tcId}"]`);
        if (!card) return;

        const summary = card.querySelector('[data-field="summary"]').value.trim();
        const role = card.querySelector('[data-field="role"]').value.trim();
        const want = card.querySelector('[data-field="want"]').value.trim();
        const benefit = card.querySelector('[data-field="benefit"]').value.trim();
        const acText = card.querySelector('[data-field="acceptance_criteria"]').value.trim();
        const acceptance_criteria = acText ? acText.split('\n').map(s => s.trim()).filter(Boolean) : [];

        currentStoriesData[tcId].user_story = { summary, role, want, benefit, acceptance_criteria };

        const res = await window.ats.saveUserStories({ flowId, data: currentStoriesData });
        if (res.success) {
            addLog(`User story updated for ${tcId}`, 'system');
            renderUserStories(currentStoriesData);
        } else {
            alert("Failed to save: " + (res.error || 'Unknown error'));
        }
    }

    function renderTestSteps(stories) {
        if (!selectedTcItems.length) {
            tsContent.innerHTML = '<div class="us-placeholder">Select test cases to view their test steps.</div>';
            return;
        }
        let html = '';
        for (const { tcId } of selectedTcItems) {
            const us = stories[tcId];
            if (!us || !us.test_steps || !us.test_steps.length) continue;
            html += `<div class="ts-card">
                <div class="ts-tc-header">${tcId}</div>
                ${us.test_steps.map(step => `
                <div class="ts-step-row">
                    <div class="ts-step-num">${step.step}</div>
                    <div class="ts-step-body">
                        <div class="ts-step-action">${step.action}</div>
                        <div class="ts-step-expected">${step.expected}</div>
                    </div>
                </div>`).join('')}
            </div>`;
        }
        tsContent.innerHTML = html || '<div class="us-placeholder">No test steps found for selected test cases.</div>';
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
        wrapper.innerHTML = `Name: <input id="td-template-name-input" type="text" placeholder="template name" style="background:#FAFAFA;border:2px solid #1A1D23;color:#1A1D23;padding:2px 6px;border-radius:8px;font-size:11px;width:100px;outline:none;"> <button style="background:#16a34a;border:2px solid #1A1D23;color:white;padding:2px 8px;border-radius:8px;font-size:10px;cursor:pointer;">OK</button> <button style="background:#FAFAFA;border:2px solid #1A1D23;color:#4A4D55;padding:2px 8px;border-radius:8px;font-size:10px;cursor:pointer;">Cancel</button>`;
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
