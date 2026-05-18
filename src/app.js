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
    const browserSelect = document.getElementById('browser-select'); // may be null if removed from topbar
    const zoomSelect = document.getElementById('viewport-select');
    const userSelect = document.getElementById('user-select');
    const adminUserSelect = document.getElementById('admin-user-select');
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
    // Variant management
    const tdVariantBar = document.getElementById('td-variant-bar');
    const tdVariantSelect = document.getElementById('td-variant-select');
    const tdVariantAdd = document.getElementById('td-variant-add');
    const tdVariantDup = document.getElementById('td-variant-dup');
    const tdVariantRen = document.getElementById('td-variant-ren');
    const tdVariantDel = document.getElementById('td-variant-del');
    let activeVariantFlow = null;
    let activeVariantTcId = null;
    let activeVariants = null; // { name: { data }, ... }
    let activeVariantName = null;

    // ── State ──
    let allFlows = [];
    let currentTdData = {};  // { tcId: { ...data }, ... }
    let selectedTcItems = []; // [{flowId, tcId}, ...]
    let config = {};
    let isRunning = false;
    let totalTests = 0;
    let completedTests = 0;

    // ── Jira Modal State ──
    let jiraModalState = { type: '', tcId: '', flowId: '', runFolder: null, errors: [] };

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
                                    <span class="tc-name">${tc.description}</span>
                                    ${tc.preconditions ? `<span class="tc-precond">Pre: ${tc.preconditions}</span>` : ''}
                                </div>
                            </label>
                            <div class="tc-actions">
                                <button class="icon-btn tc-menu-btn" title="Actions">⋯</button>
                                <div class="tc-dropdown hidden">
                                    <div class="tc-dropdown-item config-data-btn" data-tc-id="${tc.tc_id}" data-module="${flow.id}">Test Data</div>
                                    <div class="tc-dropdown-item edit-tc-btn" data-tc-id="${tc.tc_id}" data-module="${flow.id}" data-description="${tc.description || ''}">Edit Steps</div>
                                    <div class="tc-dropdown-item re-record-btn" data-tc-id="${tc.tc_id}" data-module="${flow.id}" data-description="${tc.description || ''}">Re-record</div>
                                    <div class="tc-dropdown-item analyze-coverage-btn" data-tc-id="${tc.tc_id}" data-module="${flow.id}" data-description="${tc.description || ''}">Analyze Coverage</div>
                                    <div class="tc-dropdown-item jira-story-btn" data-tc-id="${tc.tc_id}" data-module="${flow.id}">Jira Story</div>
                                    <div class="tc-dropdown-item delete-tc-btn" data-tc-id="${tc.tc_id}" data-module="${flow.id}">Delete</div>
                                </div>
                            </div>
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

            // Dropdown menu toggle — close others, toggle current
            moduleEl.querySelectorAll('.tc-menu-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const dropdown = btn.nextElementSibling;
                    const isOpen = !dropdown.classList.contains('hidden');
                    // Close all dropdowns first
                    document.querySelectorAll('.tc-dropdown:not(.hidden)').forEach(d => d.classList.add('hidden'));
                    if (!isOpen) dropdown.classList.remove('hidden');
                });
            });
            // Configure Data button listeners
            moduleEl.querySelectorAll('.config-data-btn').forEach(btn => {
                btn.addEventListener('click', () => openDataModal(btn.dataset.module, btn.dataset.tcId));
            });

            // Re-record button listeners — opens recorder pre-filled with TC info
            moduleEl.querySelectorAll('.re-record-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const flowId = btn.dataset.module;
                    const tcId = btn.dataset.tcId;
                    const desc = btn.dataset.description;
                    
                    const flowSelect = document.getElementById('record-flow');
                    flowSelect.innerHTML = allFlows.map(f => `<option value="${f.id}">${f.name}</option>`).join('');
                    flowSelect.value = flowId;
                    
                    document.getElementById('record-tc-id').value = tcId;
                    document.getElementById('record-desc').value = desc;
                    document.getElementById('record-modal').classList.remove('hidden');
                });
            });
            // Analyze Coverage button listeners — replay TC, snapshot DOM, suggest more tests
            moduleEl.querySelectorAll('.analyze-coverage-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    openCoverageModal(btn.dataset.module, btn.dataset.tcId, btn.dataset.description || '');
                });
            });

            // Edit Test button listeners — open review modal with existing assertions/criteria
            moduleEl.querySelectorAll('.edit-tc-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const tcId = btn.dataset.tcId;
                    const flowId = btn.dataset.module;
                    const desc = btn.dataset.description;
                    // Load existing assertions/criteria from test_cases.json
                    const flow = allFlows.find(f => f.id === flowId);
                    const tc = flow?.test_cases?.find(t => t.tc_id === tcId);
                    // Build steps from checkpoints
                    const steps = (tc?.checkpoints || []).map((cp, i) => ({
                        id: i + 1,
                        rawLine: '',
                        type: 'other',
                        target: '',
                        targetDescription: cp,
                        value: '',
                        varName: '',
                    }));
                    // Load existing assertions and criteria
                    const assertions = (tc?.assertions || []).map((a, i) => ({ ...a, idx: i }));
                    const criteria = (tc?.criteria || []).map((c, i) => ({ ...c, idx: i }));
                    reviewState = { steps, assertions, criteria, tcId, description: desc, flowId, editMode: true };
                    document.getElementById('review-modal-title').textContent = `Edit Test - ${tcId}`;
                    document.getElementById('review-preconditions').value = tc?.preconditions || '';
                    document.getElementById('review-expected').value = tc?.expected_result || '';
                    reviewAbBuilder.classList.add('hidden');
                    reviewCbBuilder.classList.add('hidden');
                    renderReviewSteps();
                    renderReviewAssertions();
                    renderReviewCriteria();
                    reviewModal.classList.remove('hidden');
                });
            });

            // Jira Story button listeners — open modal for editing before submit
            moduleEl.querySelectorAll('.jira-story-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const tcId = btn.dataset.tcId;
                    const flowId = btn.dataset.module;
                    // Load user story data for defaults
                    let userStory = {};
                    try {
                        const allStories = await window.ats.getUserStories({ flowId });
                        userStory = allStories[tcId] || {};
                    } catch (e) { /* ok */ }
                    const s = (userStory.user_story || {});
                    const defaultSummary = s.summary || `${tcId} — ${flowId} test story`;
                    const defaultDesc = (userStory.user_story ? `As a ${s.role || 'user'}, I want ${s.want || ''}, so that ${s.benefit || ''}.\n\n` +
                        (s.acceptance_criteria && s.acceptance_criteria.length
                            ? 'Acceptance Criteria:\n' + s.acceptance_criteria.map((ac, i) => `${i + 1}. ${ac}`).join('\n')
                            : '') : '');
                    openJiraModal({
                        type: 'Story',
                        tcId, flowId,
                        summary: defaultSummary,
                        labels: 'ats-generated, ' + flowId,
                        description: defaultDesc.trim(),
                        runFolder: null,
                        errors: [],
                    });
                });
            });
            // Delete Test button listeners
            moduleEl.querySelectorAll('.delete-tc-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const tcId = btn.dataset.tcId;
                    const flowId = btn.dataset.module;
                    if (!confirm(`Delete test case "${tcId}"?\n\nThis will remove it from test_cases.json, test_data.json, and the Python test file.`)) return;
                    try {
                        const result = await window.ats.deleteTest({ flowId, tcId });
                        if (result?.success) {
                            // Remove the TC item from the UI
                            const tcItem = btn.closest('.tc-item');
                            if (tcItem) tcItem.remove();
                            // Remove from allFlows cache
                            const flow = allFlows.find(f => f.id === flowId);
                            if (flow?.test_cases) {
                                flow.test_cases = flow.test_cases.filter(t => t.tc_id !== tcId);
                            }
                            addLog(`Deleted test case ${tcId}`, 'success');
                        } else {
                            alert('Failed to delete: ' + (result?.error || 'Unknown error'));
                        }
                    } catch (e) {
                        alert('Delete error: ' + e.message);
                    }
                });
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
                    // Checkpoint results (from run folder)
                    try {
                        const cpData = await window.ats.getRunCheckpoints(runPath);
                        const cpTcIds = Object.keys(cpData);
                        if (cpTcIds.length > 0) {
                            html += `<div class="hd-section-title">Checkpoints</div>`;
                            for (const cpTcId of cpTcIds) {
                                const cps = cpData[cpTcId];
                                const cpPassed = cps.filter(c => c.status === 'PASS').length;
                                const cpFailed = cps.filter(c => c.status === 'FAIL').length;
                                const cpSkipped = cps.filter(c => c.status === 'SKIP').length;
                                const overallStatus = cpFailed > 0 ? '❌' : '✅';
                                html += `<div class="hd-cp-block">
                                    <div class="hd-tc-row">
                                        <span class="hd-tc-id">${overallStatus} ${cpTcId}</span>
                                        <span style="font-size:10px;color:var(--text-muted);">${cpPassed}/${cps.length} passed</span>
                                        <button class="jira-bug-btn" data-tc-id="${cpTcId}" data-run-folder="${runPath || ''}" data-description="${(allFlows.flatMap(f => f.test_cases).find(t => t.tc_id === cpTcId) || {}).description || ''}">🐛 Bug</button>
                                    </div>
                                    <div class="hd-cp-list">
                                        ${cps.map(cp => {
                                            const icon = cp.status === 'PASS' ? '✓' : cp.status === 'FAIL' ? '✗' : '◌';
                                            const cls = cp.status === 'PASS' ? 'cp-pass' : cp.status === 'FAIL' ? 'cp-fail' : 'cp-skip';
                                            return `<div class="hd-cp-item ${cls}"><span class="hd-cp-icon">${icon}</span> ${cp.name}${cp.error ? ` <span class="hd-cp-err">— ${cp.error.substring(0, 120)}</span>` : ''}</div>`;
                                        }).join('')}
                                    </div>
                                </div>`;
                            }
                        }
                    } catch (e) { /* ignore checkpoint errors */ }

                    if (items.length > 0) {
                        try {
                            const bulkData = await window.ats.getBulkTcData(items);
                            html += `<div class="hd-section-title">Test Data</div>`;
                            for (const [tcId, data] of Object.entries(bulkData)) {
                                html += `<div class="hd-tc-block">
                                    <div class="hd-tc-row">
                                        <span class="hd-tc-id">${tcId}</span>
                                        <button class="jira-bug-btn" data-tc-id="${tcId}" data-run-folder="${runPath || ''}" data-description="${(allFlows.flatMap(f => f.test_cases).find(t => t.tc_id === tcId) || {}).description || ''}">🐛 Create Bug</button>
                                    </div>
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
                    // Attach click handlers for Jira bug buttons — open modal for editing
                    detailEl.querySelectorAll('.jira-bug-btn').forEach(btn => {
                        btn.onclick = async (ev) => {
                            ev.stopPropagation();
                            const tcId = btn.dataset.tcId;
                            const description = btn.dataset.description || '';
                            const runFolder = btn.dataset.runFolder || null;
                            openJiraModal({
                                type: 'Bug',
                                tcId,
                                summary: `BUG: ${tcId} — ${description || 'Automated test failure'}`,
                                labels: 'automated-test, ats',
                                description: `Test Case: ${tcId}\nStatus: FAILED\nEnvironment: Dev\nDetected by: Agrim ATS (automated)\n\n${description ? 'Description: ' + description + '\n\n' : ''}--- Steps to Reproduce ---\n1. Open Agrim ATS\n2. Select test case ${tcId}\n3. Click Run\n4. Observe failure`,
                                runFolder,
                                errors: [],
                            });
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
            if (browserSelect) browserSelect.value = config.execution.browser || 'chromium';
            document.getElementById('mode-status').textContent = `Mode: ${config.execution.default_mode || 'headless'}`;
            document.getElementById('parallel-status').textContent = `Parallel: ${config.execution.parallel_workers || 1} workers`;
        }
        // Populate user selector
        const sellerUsers = config.platforms?.seller?.users || config.users || [];
        if (sellerUsers.length > 0) {
            const prev = userSelect.value;
            userSelect.innerHTML = sellerUsers.map((u, i) =>
                `<option value="${i}">${u.label || 'User ' + (i+1)} (${u.email})</option>`
            ).join('');
            if (prev && parseInt(prev) < sellerUsers.length) {
                userSelect.value = prev;
            }
        }
        // Populate admin user selector
        const adminUsers = config.platforms?.admin?.users || config.admin_users || [];
        if (adminUsers.length > 0) {
            const prevAdmin = adminUserSelect.value;
            adminUserSelect.innerHTML = adminUsers.map((u, i) =>
                `<option value="${i}">${u.label || 'Admin ' + (i+1)} (${u.email})</option>`
            ).join('');
            if (prevAdmin && parseInt(prevAdmin) < adminUsers.length) {
                adminUserSelect.value = prevAdmin;
            }
        } else {
            adminUserSelect.innerHTML = '<option value="0">No admin users</option>';
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
        const selectedTCs = Array.from(flowTree.querySelectorAll('input[type="checkbox"]:checked'))
            .map(cb => cb.dataset.tcId)
            .filter(Boolean);

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
        const sellerLabel = config.platforms?.seller?.users?.[userSelect.value]?.label || config.users?.[userSelect.value]?.label || 'Default';
        const adminLabel = config.platforms?.admin?.users?.[adminUserSelect.value]?.label || 'None';
        addLog(`  Env: ${envSelect.value} | Mode: ${execModeSelect.value} | Seller: ${sellerLabel} | Admin: ${adminLabel}`, 'system');

        const options = {
            tc_ids: selectedTCs,
            env: envSelect.value,
            mode: config.execution?.default_mode || 'headless',
            execMode: execModeSelect.value,
            parallel: execModeSelect.value === 'parallel' ? (config.execution?.parallel_workers || 1) : 1,
            zoom: zoomSelect.value,
            userIndex: parseInt(userSelect.value) || 0,
            sellerUserIndex: parseInt(userSelect.value) || 0,
            adminUserIndex: parseInt(adminUserSelect.value) || 0,
            variant: selectedTCs.length === 1 ? activeVariantName : null,
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
                // Show checkpoint breakdown
                if (data.checkpoints && data.checkpoints.length > 0) {
                    const cpPassed = data.checkpoints.filter(c => c.status === 'PASS').length;
                    const cpFailed = data.checkpoints.filter(c => c.status === 'FAIL').length;
                    const cpSkipped = data.checkpoints.filter(c => c.status === 'SKIP').length;
                    addLog(`   └ Checkpoints: ${cpPassed} passed, ${cpFailed} failed, ${cpSkipped} skipped`, 'system');
                    for (const cp of data.checkpoints) {
                        const icon = cp.status === 'PASS' ? '  ✓' : cp.status === 'FAIL' ? '  ✗' : '  ◌';
                        const type = cp.status === 'PASS' ? 'pass' : cp.status === 'FAIL' ? 'fail' : 'warn';
                        addLog(`     ${icon} ${cp.name}${cp.error ? ' — ' + cp.error.substring(0, 100) : ''}`, type);
                    }
                }
                // Store checkpoints for history
                if (!window._lastRunCheckpoints) window._lastRunCheckpoints = {};
                window._lastRunCheckpoints[data.tc_id] = data.checkpoints || [];
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
        usersList.innerHTML = (config.platforms?.seller?.users || config.users || []).map((user, i) => `
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
        // Populate admin user fields
        const adminUsersList = document.getElementById('admin-users-config-list');
        adminUsersList.innerHTML = (config.platforms?.admin?.users || config.admin_users || []).map((user, i) => `
            <div style="margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid #333;">
                <h4 style="color: #94a3b8; margin-bottom: 8px;">${user.label || 'Admin ' + (i + 1)}</h4>
                <div class="setting-item">
                    <label>Email</label>
                    <input type="text" class="admin-user-email" data-index="${i}" value="${user.email || ''}">
                </div>
                <div class="setting-item">
                    <label>Password</label>
                    <input type="password" class="admin-user-pass" data-index="${i}" value="${user.password || ''}">
                </div>
            </div>
        `).join('');

        // Populate Jira fields
        const jira = config.jira || {};
        document.getElementById('setting-jira-url').value = jira.url || '';
        document.getElementById('setting-jira-email').value = jira.email || '';
        document.getElementById('setting-jira-token').value = jira.apiToken || '';
        document.getElementById('setting-jira-project').value = jira.projectKey || 'PM';
        document.getElementById('jira-test-status').textContent = '';

        settingsModal.classList.remove('hidden');
    };

    closeModalBtn.onclick = () => settingsModal.classList.add('hidden');

    // Jira test connection
    document.getElementById('test-jira-btn').addEventListener('click', async () => {
        const statusEl = document.getElementById('jira-test-status');
        statusEl.textContent = 'Testing...';
        statusEl.style.color = 'var(--text-muted)';
        // Save current fields to config first
        config.jira = {
            url: document.getElementById('setting-jira-url').value.trim(),
            email: document.getElementById('setting-jira-email').value.trim(),
            apiToken: document.getElementById('setting-jira-token').value.trim(),
            projectKey: document.getElementById('setting-jira-project').value.trim() || 'PM',
        };
        await window.ats.saveConfig(config);
        const res = await window.ats.testJiraConnection();
        if (res.success) {
            statusEl.textContent = `Connected as ${res.user}`;
            statusEl.style.color = 'var(--accent-green)';
        } else {
            statusEl.textContent = `Failed: ${res.error}`;
            statusEl.style.color = 'var(--accent-red)';
        }
    });

    saveSettingsBtn.onclick = async () => {
        config.execution.default_mode = document.getElementById('setting-mode').value;
        config.execution.parallel_workers = parseInt(document.getElementById('setting-workers').value) || 1;

        document.querySelectorAll('.user-email').forEach((input) => {
            const idx = parseInt(input.dataset.index);
            const users = config.platforms?.seller?.users || config.users;
            if (users && users[idx]) {
                users[idx].email = input.value;
            }
        });
        document.querySelectorAll('.user-pass').forEach((input) => {
            const idx = parseInt(input.dataset.index);
            const users = config.platforms?.seller?.users || config.users;
            if (users && users[idx]) {
                users[idx].password = input.value;
            }
        });
        document.querySelectorAll('.admin-user-email').forEach((input) => {
            const idx = parseInt(input.dataset.index);
            const adminUsers = config.platforms?.admin?.users || (() => {
                if (!config.admin_users) config.admin_users = [];
                return config.admin_users;
            })();
            if (!adminUsers[idx]) adminUsers[idx] = {};
            adminUsers[idx].email = input.value;
        });
        document.querySelectorAll('.admin-user-pass').forEach((input) => {
            const idx = parseInt(input.dataset.index);
            const adminUsers = config.platforms?.admin?.users || (() => {
                if (!config.admin_users) config.admin_users = [];
                return config.admin_users;
            })();
            if (!adminUsers[idx]) adminUsers[idx] = {};
            adminUsers[idx].password = input.value;
        });

        // Save Jira config
        config.jira = {
            url: document.getElementById('setting-jira-url').value.trim(),
            email: document.getElementById('setting-jira-email').value.trim(),
            apiToken: document.getElementById('setting-jira-token').value.trim(),
            projectKey: document.getElementById('setting-jira-project').value.trim() || 'PM',
        };

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
        const platform = document.getElementById('record-platform').value;
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
                flowId, tcId, description: desc, env: envSelect.value, platform
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
        wait: '&#x23F3;', viewport: '&#x1F4FA;', scroll: '&#x2B07;', other: '&#x25B6;'
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
                <button class="review-step-scroll-btn" data-step-id="${step.id}" title="Insert scroll down after this step">⬇ Scroll</button>
                <button class="review-step-assert-btn" data-step-id="${step.id}" title="Add assertion after this step">+ Assert</button>
                <button class="review-step-delete-btn" data-step-id="${step.id}" title="Delete this step">✕</button>
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
        // Scroll button handlers — insert a scroll step after this step
        reviewStepsList.querySelectorAll('.review-step-scroll-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const afterStep = parseInt(btn.dataset.stepId);
                const scrollStep = {
                    id: reviewState.steps.length + 1,
                    rawLine: 'page.evaluate("window.scrollBy(0, 500)")',
                    type: 'scroll',
                    target: '',
                    targetDescription: 'Scroll down 500px',
                    value: '500',
                    varName: '',
                };
                // Insert after the clicked step
                let insertIdx = reviewState.steps.findIndex(s => s.id === afterStep);
                if (insertIdx === -1) insertIdx = reviewState.steps.length;
                reviewState.steps.splice(insertIdx + 1, 0, scrollStep);
                // Re-number step IDs
                reviewState.steps.forEach((s, i) => s.id = i + 1);
                renderReviewSteps();
                addLog('Scroll step inserted', 'system');
            });
        });
        // Delete step button handlers
        reviewStepsList.querySelectorAll('.review-step-delete-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const stepId = parseInt(btn.dataset.stepId);
                reviewState.steps = reviewState.steps.filter(s => s.id !== stepId);
                reviewState.steps.forEach((s, i) => s.id = i + 1);
                renderReviewSteps();
                addLog('Step deleted', 'system');
            });
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
            editMode: reviewState.editMode || false,
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

    // ── Jira Submit Modal ──
    const jiraModal = document.getElementById('jira-modal');
    const jiraSubmitBtn = document.getElementById('jira-submit-btn');
    const jiraCancelBtn = document.getElementById('jira-cancel-btn');

    function openJiraModal({ type, tcId, flowId, summary, labels, description, runFolder, errors }) {
        jiraModalState = { type, tcId, flowId: flowId || '', runFolder: runFolder || null, errors: errors || [] };
        document.getElementById('jira-modal-title').textContent = `Create Jira ${type}`;
        document.getElementById('jira-issue-type').textContent = type === 'Bug' ? '🐛 Bug' : '🎫 Story';
        document.getElementById('jira-summary').value = summary || '';
        document.getElementById('jira-labels').value = labels || '';
        document.getElementById('jira-description').value = description || '';
        // Show attachment info if run folder exists
        const infoEl = document.getElementById('jira-attachments-info');
        if (runFolder) {
            infoEl.textContent = `Attachments will be auto-included from the latest run for ${tcId}.`;
        } else {
            infoEl.textContent = `No run artifacts found for ${tcId}. Issue will be created without attachments.`;
        }
        jiraSubmitBtn.disabled = false;
        jiraSubmitBtn.textContent = 'Create Issue';
        jiraModal.classList.remove('hidden');
    }

    jiraSubmitBtn.addEventListener('click', async () => {
        const summary = document.getElementById('jira-summary').value.trim();
        const labelsRaw = document.getElementById('jira-labels').value.trim();
        const description = document.getElementById('jira-description').value.trim();
        if (!summary) { alert('Summary is required.'); return; }
        const labels = labelsRaw ? labelsRaw.split(',').map(l => l.trim()).filter(Boolean) : [];

        jiraSubmitBtn.disabled = true;
        jiraSubmitBtn.textContent = 'Creating...';

        let res;
        if (jiraModalState.type === 'Bug') {
            res = await window.ats.createJiraBug({
                tcId: jiraModalState.tcId,
                runFolder: jiraModalState.runFolder,
                errors: jiraModalState.errors,
                summary,
                labels,
                description,
            });
        } else {
            res = await window.ats.createJiraStory({
                tcId: jiraModalState.tcId,
                flowId: jiraModalState.flowId,
                userStory: {},
                summary,
                labels,
                description,
            });
        }

        jiraModal.classList.add('hidden');

        if (res.success) {
            addLog(`Jira ${jiraModalState.type.toLowerCase()} created: ${res.key} — ${res.url}`, 'pass');
        } else {
            addLog(`Jira ${jiraModalState.type.toLowerCase()} failed: ${res.error}`, 'fail');
        }
    });

    jiraCancelBtn.addEventListener('click', () => {
        jiraModal.classList.add('hidden');
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
            hideVariantBar();
            return;
        }

        bottomPanel.classList.remove('hidden');

        // Single TC selected — show variant bar always
        if (selectedTcItems.length === 1) {
            const { flowId, tcId } = selectedTcItems[0];
            tdSelectedInfo.textContent = tcId;
            activeVariantFlow = flowId;
            activeVariantTcId = tcId;
            try {
                const result = await window.ats.getTcData({ flowId, tcId });
                if (result.isVariant) {
                    activeVariants = result.variants;
                    showVariantBar(Object.keys(activeVariants));
                } else {
                    // Flat data — show as single "default" variant
                    activeVariants = { default: result.data || {} };
                    showVariantBar(['default']);
                }
                loadUserStoriesAndSteps();
            } catch (e) {
                hideVariantBar();
                setTdStatus('Error loading data: ' + e.message, 'error');
            }
        } else {
            // Multiple TCs — bulk mode, no variant bar
            hideVariantBar();
            tdSelectedInfo.textContent = selectedTcItems.map(t => t.tcId).join(', ');
            try {
                currentTdData = await window.ats.getBulkTcData(selectedTcItems);
                tdEditor.value = JSON.stringify(currentTdData, null, 2);
                setTdStatus('');
            } catch (e) {
                setTdStatus('Error loading data: ' + e.message, 'error');
            }
        }

        // Load user stories and test steps
        loadUserStoriesAndSteps();
    }

    function showVariantBar(variantNames) {
        activeVariantName = variantNames[0] || 'default';
        tdVariantSelect.innerHTML = variantNames.map(n =>
            `<option value="${n}" ${n === activeVariantName ? 'selected' : ''}>${n}</option>`
        ).join('');
        tdVariantBar.classList.remove('hidden');
        loadVariantData(activeVariantName);
    }

    function hideVariantBar() {
        tdVariantBar.classList.add('hidden');
        activeVariantFlow = null;
        activeVariantTcId = null;
        activeVariants = null;
        activeVariantName = null;
    }

    function loadVariantData(variantName) {
        if (!activeVariants || !variantName) return;
        activeVariantName = variantName;
        const data = activeVariants[variantName] || {};
        currentTdData = data;
        tdEditor.value = JSON.stringify(data, null, 2);
        tdSelectedInfo.textContent = `${activeVariantTcId} [${variantName}]`;
        setTdStatus('');
    }

    // Variant selector change
    tdVariantSelect.addEventListener('change', (e) => {
        loadVariantData(e.target.value);
    });

    // Helper to prompt for variant name inline
    function promptVariantName(placeholder, callback) {
        tdStatus.innerHTML = '';
        const wrapper = document.createElement('span');
        wrapper.innerHTML = `Name: <input id="td-variant-name-input" type="text" placeholder="${placeholder}" style="background:#FAFAFA;border:2px solid #1A1D23;color:#1A1D23;padding:2px 6px;border-radius:8px;font-size:11px;width:100px;outline:none;"> <button style="background:#16a34a;border:2px solid #1A1D23;color:white;padding:2px 8px;border-radius:8px;font-size:10px;cursor:pointer;">OK</button> <button style="background:#FAFAFA;border:2px solid #1A1D23;color:#4A4D55;padding:2px 8px;border-radius:8px;font-size:10px;cursor:pointer;">Cancel</button>`;
        tdStatus.appendChild(wrapper);
        const nameInput = document.getElementById('td-variant-name-input');
        nameInput.focus();

        const okBtn = wrapper.querySelector('button:first-of-type');
        const cancelBtn = wrapper.querySelector('button:last-of-type');

        const submit = () => {
            const name = nameInput.value.trim();
            if (name) {
                tdStatus.innerHTML = '';
                callback(name);
            }
        };

        okBtn.onclick = submit;
        nameInput.onkeydown = (e) => { if (e.key === 'Enter') submit(); };
        cancelBtn.onclick = () => { tdStatus.innerHTML = ''; };
    }

    // New variant
    tdVariantAdd.addEventListener('click', async () => {
        if (!activeVariantFlow || !activeVariantTcId) return;
        promptVariantName('new variant', async (name) => {
            const res = await window.ats.addTcVariant({
                flowId: activeVariantFlow, tcId: activeVariantTcId,
                variantName: name, copyFrom: activeVariantName
            });
            if (res.success) {
                activeVariants = res.variants;
                showVariantBar(Object.keys(activeVariants));
                tdVariantSelect.value = name;
                loadVariantData(name);
                addLog(`Variant "${name}" created for ${activeVariantTcId}`, 'system');
            } else {
                setTdStatus('Failed: ' + (res.error || 'Unknown'), 'error');
            }
        });
    });

    // Duplicate variant
    tdVariantDup.addEventListener('click', async () => {
        if (!activeVariantFlow || !activeVariantTcId || !activeVariantName) return;
        promptVariantName(activeVariantName + '-copy', async (name) => {
            const res = await window.ats.addTcVariant({
                flowId: activeVariantFlow, tcId: activeVariantTcId,
                variantName: name, copyFrom: activeVariantName
            });
            if (res.success) {
                activeVariants = res.variants;
                showVariantBar(Object.keys(activeVariants));
                tdVariantSelect.value = name;
                loadVariantData(name);
                addLog(`Variant "${name}" duplicated from "${activeVariantName}"`, 'system');
            } else {
                setTdStatus('Failed: ' + (res.error || 'Unknown'), 'error');
            }
        });
    });

    // Rename variant
    tdVariantRen.addEventListener('click', async () => {
        if (!activeVariantFlow || !activeVariantTcId || !activeVariantName) return;
        promptVariantName(activeVariantName, async (newName) => {
            if (newName === activeVariantName) return;
            // Add new variant copied from old, then delete old
            const addRes = await window.ats.addTcVariant({
                flowId: activeVariantFlow, tcId: activeVariantTcId,
                variantName: newName, copyFrom: activeVariantName
            });
            if (addRes.success) {
                await window.ats.deleteTcVariant({
                    flowId: activeVariantFlow, tcId: activeVariantTcId, variantName: activeVariantName
                });
                activeVariants = addRes.variants;
                delete activeVariants[activeVariantName];
                showVariantBar(Object.keys(activeVariants));
                tdVariantSelect.value = newName;
                loadVariantData(newName);
                addLog(`Variant renamed to "${newName}"`, 'system');
            } else {
                setTdStatus('Failed: ' + (addRes.error || 'Unknown'), 'error');
            }
        });
    });

    // Delete variant
    tdVariantDel.addEventListener('click', async () => {
        if (!activeVariantFlow || !activeVariantTcId || !activeVariantName) return;
        if (Object.keys(activeVariants).length <= 1) {
            alert('Cannot delete the last variant.');
            return;
        }
        if (!confirm(`Delete variant "${activeVariantName}"?`)) return;
        const res = await window.ats.deleteTcVariant({
            flowId: activeVariantFlow, tcId: activeVariantTcId, variantName: activeVariantName
        });
        if (res.success) {
            activeVariants = res.variants;
            const remaining = Object.keys(activeVariants);
            if (remaining.length > 0) {
                showVariantBar(remaining);
            } else {
                hideVariantBar();
                tdEditor.value = '{}';
            }
            addLog(`Variant "${activeVariantName}" deleted`, 'system');
        }
    });

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

    // Save button (variant-aware)
    tdSaveBtn.addEventListener('click', async () => {
        const parsed = parseEditorData();
        if (!parsed) return;

        // Variant mode — save single variant
        if (activeVariantFlow && activeVariantTcId && activeVariantName) {
            const res = await window.ats.saveTcData({
                flowId: activeVariantFlow, tcId: activeVariantTcId,
                data: parsed, variant: activeVariantName
            });
            if (res.success) {
                activeVariants[activeVariantName] = parsed;
                currentTdData = parsed;
                setTdStatus('Saved', 'saved');
                addLog(`Data saved for ${activeVariantTcId} [${activeVariantName}]`, 'system');
            } else {
                setTdStatus('Save failed: ' + res.error, 'error');
            }
            return;
        }

        // Bulk mode — save all selected TCs
        const items = selectedTcItems.map(({ flowId, tcId }) => ({
            flowId,
            tcId,
            data: parsed[tcId] || {}
        }));

        const res = await window.ats.saveBulkTcData(items);
        if (res.success) {
            currentTdData = parsed;
            setTdStatus('Saved', 'saved');
            addLog('Test data saved for ' + items.length + ' test case(s)', 'system');
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

    // Close dropdown menus when clicking outside
    document.addEventListener('click', () => {
        document.querySelectorAll('.tc-dropdown:not(.hidden)').forEach(d => d.classList.add('hidden'));
    });

    // ── Coverage Analysis Modal ──
    const coverageModal = document.getElementById('coverage-modal');
    const coverageStatusText = document.getElementById('coverage-status-text');
    const coverageStatusIcon = document.getElementById('coverage-status-icon');
    const coverageLog = document.getElementById('coverage-log');
    const coverageSummary = document.getElementById('coverage-summary');
    const coverageList = document.getElementById('coverage-suggestions-list');
    const coverageAnalyzeBtn = document.getElementById('coverage-analyze-btn');
    const coverageHeaded = document.getElementById('coverage-headed');
    const coverageStopTerminal = document.getElementById('coverage-stop-terminal');
    const coverageVariantWrap = document.getElementById('coverage-variant-wrap');
    const coverageVariantSelect = document.getElementById('coverage-variant-select');
    const coverageNavmap = document.getElementById('coverage-navmap');
    const coverageLogWrap = document.getElementById('coverage-log-wrap');
    const coverageArtifactBox = document.getElementById('coverage-artifact');
    const coverageArtifactPath = document.getElementById('coverage-artifact-path');

    let coverageContext = { flowId: '', tcId: '', desc: '' };
    let coverageProgressUnsub = null;

    const PRIORITY_COLORS = {
        high: { bg: '#fde8e8', border: '#e53e3e', label: 'HIGH' },
        medium: { bg: '#fef5e7', border: '#dd6b20', label: 'MED' },
        low: { bg: '#edf2f7', border: '#718096', label: 'LOW' },
    };

    const SUGGESTION_ICONS = {
        tab_coverage: '&#x1F516;',
        negative_flow: '&#x1F6AB;',
        destructive_flow: '&#x26A0;&#xFE0F;',
        alternate_action: '&#x1F501;',
        validation: '&#x2728;',
        input_coverage: '&#x270F;&#xFE0F;',
        checkbox_coverage: '&#x2611;&#xFE0F;',
        dropdown_variants: '&#x1F4CB;',
    };

    async function openCoverageModal(flowId, tcId, desc) {
        coverageContext = { flowId, tcId, desc };
        document.getElementById('coverage-modal-title').textContent = `Coverage Analysis — ${tcId}`;
        coverageStatusIcon.innerHTML = '&#x1F50D;';
        coverageStatusText.textContent = 'Ready. Click Analyze to run the test and capture the navigation map.';
        coverageLog.innerHTML = '';
        coverageLogWrap.removeAttribute('open');
        coverageSummary.style.display = 'none';
        coverageSummary.innerHTML = '';
        coverageArtifactBox.style.display = 'none';
        coverageArtifactPath.textContent = '';
        coverageNavmap.innerHTML = `<div style="color:var(--text-3); font-size:13px; text-align:center; padding:30px 0;">
            Click <strong>Analyze</strong> to run the test and capture the navigation map.</div>`;
        if (coverageList) {
            coverageList.style.display = 'none';
            coverageList.innerHTML = '';
        }
        coverageAnalyzeBtn.disabled = false;
        coverageAnalyzeBtn.textContent = 'Analyze';
        coverageModal.classList.remove('hidden');

        // Populate variant dropdown (hidden if TC has no variants)
        coverageVariantWrap.style.display = 'none';
        coverageVariantSelect.innerHTML = '';
        try {
            const td = await window.ats.getTcData({ flowId, tcId });
            if (td && td.isVariant && td.variants && Object.keys(td.variants).length > 0) {
                const names = Object.keys(td.variants);
                coverageVariantSelect.innerHTML = names
                    .map(n => `<option value="${n}">${n}</option>`)
                    .join('');
                coverageVariantWrap.style.display = 'inline-flex';
            }
        } catch (e) { /* TC has no test data — fine */ }
    }

    function appendCoverageLog(msg) {
        const line = document.createElement('div');
        line.textContent = msg;
        coverageLog.appendChild(line);
        coverageLog.scrollTop = coverageLog.scrollHeight;
    }

    function _escHtml(s) {
        return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
    }

    function _shortenUrl(u) {
        if (!u) return '(unknown)';
        try {
            const url = new URL(u);
            return url.hostname.replace(/^www\./, '') + (url.pathname === '/' ? '' : url.pathname);
        } catch (e) {
            return u.length > 60 ? u.slice(0, 60) + '…' : u;
        }
    }

    function renderNavigationMap(navMap, gapsByPage) {
        if (!navMap || !navMap.pages || navMap.pages.length === 0) {
            coverageNavmap.innerHTML = `<div style="color:var(--text-3); font-size:13px; text-align:center; padding:24px 0;">No pages were captured. Did the test fail to start?</div>`;
            return;
        }
        const pageCards = navMap.pages.map((p, idx) => {
            const elCount = (p.elements || []).length;
            const actCount = (p.actions_on_page || []).length;
            const pageGaps = (gapsByPage || {})[p.url] || {};
            const gapCount = Object.values(pageGaps).reduce((sum, arr) => sum + (Array.isArray(arr) ? arr.length : 0), 0);

            // Build a compact element inventory grouped by tag/role
            const byCat = { tabs: [], buttons: [], links: [], inputs: [], other: [] };
            for (const el of (p.elements || [])) {
                const label = (el.aria_label || el.text || el.placeholder || el.name || el.id || '').trim();
                if (!label) continue;
                if (el.role === 'tab') byCat.tabs.push(label);
                else if (el.tag === 'button' || el.role === 'button') byCat.buttons.push(label);
                else if (el.tag === 'a') byCat.links.push(label);
                else if (el.tag === 'input' || el.tag === 'textarea') byCat.inputs.push(label);
                else byCat.other.push(label);
            }
            const _chips = (arr, color) => arr.slice(0, 14).map(t => `<span style="display:inline-block; padding:2px 7px; margin:2px; border:1px solid var(--border); border-radius:10px; background:${color}; font-size:10.5px; color:var(--text-2);">${_escHtml(t)}</span>`).join('') + (arr.length > 14 ? `<span style="font-size:10.5px; color:var(--text-3);"> +${arr.length - 14} more</span>` : '');

            // Transitions out
            const tOut = (p.transitions_out || []).slice(0, 5).map(t => `<div style="font-size:11px; color:var(--text-3); margin-top:2px;">→ <code style="font-size:11px;">${_escHtml(_shortenUrl(t.to_url))}</code> via ${_escHtml(t.via_action.slice(0, 60))}</div>`).join('');

            return `<details ${idx === 0 ? 'open' : ''} class="navmap-page" style="border:1px solid var(--border); border-radius:var(--radius); background:var(--surface-1); padding:0; overflow:hidden;">
                <summary style="padding:10px 12px; cursor:pointer; display:flex; gap:10px; align-items:center; background:var(--surface-2);">
                    <span style="font-weight:700; color:var(--text); font-size:13px;">${idx + 1}. ${_escHtml(p.title || _shortenUrl(p.url))}</span>
                    <code style="font-size:11px; color:var(--text-3);">${_escHtml(_shortenUrl(p.url))}</code>
                    <span style="margin-left:auto; display:flex; gap:6px;">
                        <span style="font-size:10px; color:var(--text-3);">${actCount} action${actCount !== 1 ? 's' : ''}</span>
                        <span style="font-size:10px; color:var(--text-3);">·</span>
                        <span style="font-size:10px; color:var(--text-3);">${elCount} elements</span>
                        ${gapCount ? `<span style="font-size:10px; padding:1px 6px; background:#fef5e7; color:#dd6b20; border-radius:8px; font-weight:600;">${gapCount} gap${gapCount !== 1 ? 's' : ''}</span>` : ''}
                    </span>
                </summary>
                <div style="padding:10px 12px; border-top:1px solid var(--border);">
                    ${byCat.tabs.length ? `<div style="margin-bottom:8px;"><span style="font-size:10px; color:var(--text-3); text-transform:uppercase; letter-spacing:0.5px; font-weight:700; display:block; margin-bottom:3px;">Tabs (${byCat.tabs.length})</span>${_chips(byCat.tabs, 'var(--surface-2)')}</div>` : ''}
                    ${byCat.buttons.length ? `<div style="margin-bottom:8px;"><span style="font-size:10px; color:var(--text-3); text-transform:uppercase; letter-spacing:0.5px; font-weight:700; display:block; margin-bottom:3px;">Buttons (${byCat.buttons.length})</span>${_chips(byCat.buttons, 'var(--surface-2)')}</div>` : ''}
                    ${byCat.inputs.length ? `<div style="margin-bottom:8px;"><span style="font-size:10px; color:var(--text-3); text-transform:uppercase; letter-spacing:0.5px; font-weight:700; display:block; margin-bottom:3px;">Inputs (${byCat.inputs.length})</span>${_chips(byCat.inputs, 'var(--surface-2)')}</div>` : ''}
                    ${byCat.links.length ? `<div style="margin-bottom:8px;"><span style="font-size:10px; color:var(--text-3); text-transform:uppercase; letter-spacing:0.5px; font-weight:700; display:block; margin-bottom:3px;">Links (${byCat.links.length})</span>${_chips(byCat.links, 'var(--surface-2)')}</div>` : ''}
                    ${tOut ? `<div style="margin-top:8px; padding-top:8px; border-top:1px dashed var(--border);"><span style="font-size:10px; color:var(--text-3); text-transform:uppercase; letter-spacing:0.5px; font-weight:700; display:block; margin-bottom:3px;">Transitions out</span>${tOut}</div>` : ''}
                </div>
            </details>`;
        }).join('');

        coverageNavmap.innerHTML = pageCards;
    }

    function renderSuggestionsList(suggestions) {
        if (!suggestions || suggestions.length === 0) {
            coverageList.innerHTML = `<div style="color:var(--text-3); font-size:13px; text-align:center; padding:30px 0;">&#x2705; No coverage gaps detected.</div>`;
            return;
        }
        const PRI = {
            high: { bg: '#fde8e8', fg: '#e53e3e', label: 'HIGH' },
            medium: { bg: '#fef5e7', fg: '#dd6b20', label: 'MED' },
            low: { bg: '#edf2f7', fg: '#718096', label: 'LOW' },
        };
        coverageList.innerHTML = suggestions.map(s => {
            const p = PRI[s.priority] || PRI.low;
            return `<div style="border:1px solid var(--border); border-left:3px solid ${p.fg}; border-radius:var(--radius); background:var(--surface-1); padding:10px 12px;">
                <div style="display:flex; align-items:center; gap:8px; margin-bottom:3px;">
                    <span style="font-size:9px; font-weight:700; padding:2px 6px; border-radius:3px; background:${p.bg}; color:${p.fg};">${p.label}</span>
                    <span style="font-size:10px; color:var(--text-3); text-transform:uppercase; letter-spacing:0.5px;">${_escHtml(s.type.replace(/_/g, ' '))}</span>
                    ${s.page_url ? `<code style="font-size:10px; color:var(--text-3); margin-left:auto;">${_escHtml(_shortenUrl(s.page_url))}</code>` : ''}
                </div>
                <div style="font-size:13px; font-weight:600; color:var(--text); margin-bottom:2px;">${_escHtml(s.title)}</div>
                <div style="font-size:12px; color:var(--text-2);">${_escHtml(s.description)}</div>
            </div>`;
        }).join('');
    }

    function switchCoverageTab(tab) {
        document.querySelectorAll('.coverage-tab-btn').forEach(btn => {
            const active = btn.dataset.tab === tab;
            btn.style.borderBottom = active ? '2px solid var(--accent,#0066cc)' : '2px solid transparent';
            btn.style.color = active ? 'var(--text)' : 'var(--text-2)';
            btn.classList.toggle('active', active);
        });
        coverageNavmap.style.display = (tab === 'navmap') ? 'flex' : 'none';
        coverageList.style.display = (tab === 'suggestions') ? 'flex' : 'none';
    }

    function renderCoverageSummary(gapsSummary) {
        coverageSummary.style.display = 'flex';
        const labels = {
            unused_tabs: 'Unused tabs',
            negative_action_buttons: 'Cancel/back buttons',
            destructive_buttons: 'Destructive actions',
            alternate_actions: 'Alt actions',
            unfilled_required_inputs: 'Required empty inputs',
            unfilled_inputs: 'Optional empty inputs',
            unused_dropdown_options: 'Dropdowns w/ options',
            checkboxes_skipped: 'Untoggled checkboxes',
        };
        const chips = [];
        for (const [key, count] of Object.entries(gapsSummary || {})) {
            if (!count) continue;
            const label = labels[key] || key;
            chips.push(`<div style="padding:5px 10px; border:1px solid var(--border); border-radius:14px; background:var(--surface-2); font-size:11px; color:var(--text-2);">
                <strong style="color:var(--text);">${count}</strong> ${label}
            </div>`);
        }
        coverageSummary.innerHTML = chips.length
            ? chips.join('')
            : `<div style="font-size:12px; color:var(--text-3);">No coverage gaps detected.</div>`;
    }

    function renderCoverageSuggestions(suggestions) {
        if (!suggestions || suggestions.length === 0) {
            coverageList.innerHTML = `<div style="color:var(--text-3); font-size:13px; text-align:center; padding:30px 0;">
                &#x2705; No coverage gaps found. This recording exercises everything visible on the page.</div>`;
            return;
        }
        coverageList.innerHTML = suggestions.map((s, i) => {
            const pri = PRIORITY_COLORS[s.priority] || PRIORITY_COLORS.low;
            const icon = SUGGESTION_ICONS[s.type] || '&#x2728;';
            let extra = '';
            if (s.type === 'dropdown_variants' && Array.isArray(s.options)) {
                const opts = s.options.map(o => `<span style="display:inline-block; padding:2px 8px; margin:2px; border:1px solid var(--border); border-radius:10px; background:var(--surface-1); font-size:11px;">${o.label}</span>`).join('');
                extra = `<div style="margin-top:6px;">${opts}</div>`;
            }
            return `<div class="coverage-suggestion" data-idx="${i}" style="display:flex; gap:12px; padding:10px 12px; border-left:3px solid ${pri.border}; border:1px solid var(--border); border-left:3px solid ${pri.border}; border-radius:var(--radius); background:var(--surface-1);">
                <div style="font-size:20px; line-height:1;">${icon}</div>
                <div style="flex:1;">
                    <div style="display:flex; gap:8px; align-items:center; margin-bottom:4px;">
                        <span style="font-size:9px; font-weight:700; padding:2px 6px; border-radius:3px; background:${pri.bg}; color:${pri.border};">${pri.label}</span>
                        <span style="font-size:10px; color:var(--text-3); text-transform:uppercase; letter-spacing:0.5px;">${s.type.replace(/_/g, ' ')}</span>
                        <span style="font-size:10px; color:var(--text-3);">step ${s.step_id}</span>
                    </div>
                    <div style="font-size:13px; font-weight:600; color:var(--text); margin-bottom:3px;">${s.title}</div>
                    <div style="font-size:12px; color:var(--text-2); line-height:1.4;">${s.description}</div>
                    ${extra}
                </div>
            </div>`;
        }).join('');
    }

    async function runCoverageAnalysis() {
        if (!coverageContext.tcId) return;

        coverageAnalyzeBtn.disabled = true;
        coverageAnalyzeBtn.textContent = 'Analyzing…';
        coverageStatusIcon.innerHTML = '&#x23F3;';
        const variantLabel = (coverageVariantWrap.style.display !== 'none')
            ? ` [variant: ${coverageVariantSelect.value}]` : '';
        coverageStatusText.textContent = `Replaying ${coverageContext.tcId}${variantLabel} — this can take 1–3 minutes…`;
        coverageLog.style.display = 'block';
        coverageLog.innerHTML = '';
        coverageSummary.style.display = 'none';
        coverageNavmap.innerHTML = '';

        // Subscribe to streaming progress
        if (coverageProgressUnsub) coverageProgressUnsub();
        coverageProgressUnsub = window.ats.onAnalyzeProgress((data) => {
            if (data && data.message) appendCoverageLog(data.message);
        });

        const selectedVariant = (coverageVariantWrap.style.display !== 'none')
            ? coverageVariantSelect.value
            : null;

        try {
            const res = await window.ats.analyzeCoverage({
                flowId: coverageContext.flowId,
                tcId: coverageContext.tcId,
                headed: coverageHeaded.checked,
                stopTerminal: coverageStopTerminal.checked,
                variant: selectedVariant,
            });

            if (res.status !== 'success') {
                coverageStatusIcon.innerHTML = '&#x274C;';
                coverageStatusText.textContent = `Analysis failed: ${res.message || 'Unknown error'}`;
                addLog(`Coverage analysis failed: ${res.message || 'Unknown error'}`, 'fail');
            } else {
                const navMap = res.navigation_map || { pages: [], transitions: [] };
                const pageCount = (navMap.pages || []).length;
                const transitionCount = (navMap.transitions || []).length;
                const driver = res.driver || 'replay';
                const artifactFile = res.artifact_file || res.snapshot_file || '';

                coverageStatusIcon.innerHTML = pageCount ? '&#x2728;' : '&#x2705;';
                coverageStatusText.textContent = `Captured ${pageCount} unique page(s), ${transitionCount} transition(s) across ${res.snapshots_count || 0} actions. [driver: ${driver}]`;

                coverageSummary.style.display = 'flex';
                coverageSummary.innerHTML = [
                    `<div style="padding:5px 10px; border:1px solid var(--border); border-radius:14px; background:var(--surface-2); font-size:11px;"><strong>${pageCount}</strong> pages</div>`,
                    `<div style="padding:5px 10px; border:1px solid var(--border); border-radius:14px; background:var(--surface-2); font-size:11px;"><strong>${transitionCount}</strong> transitions</div>`,
                    `<div style="padding:5px 10px; border:1px solid var(--border); border-radius:14px; background:var(--surface-2); font-size:11px;"><strong>${res.snapshots_count || 0}</strong> actions</div>`,
                ].join('');

                if (artifactFile) {
                    coverageArtifactBox.style.display = 'block';
                    coverageArtifactPath.textContent = artifactFile;
                    coverageArtifactBox.dataset.path = artifactFile;
                }

                renderNavigationMap(navMap, {});
                addLog(`Coverage: ${coverageContext.tcId} → ${pageCount} pages captured. Data at ${artifactFile}`, 'system');
            }
        } catch (e) {
            coverageStatusIcon.innerHTML = '&#x274C;';
            coverageStatusText.textContent = 'Analysis crashed: ' + e.message;
            addLog(`Coverage crash: ${e.message}`, 'fail');
        } finally {
            if (coverageProgressUnsub) { coverageProgressUnsub(); coverageProgressUnsub = null; }
            coverageAnalyzeBtn.disabled = false;
            coverageAnalyzeBtn.textContent = 'Analyze Again';
        }
    }

    coverageAnalyzeBtn.addEventListener('click', runCoverageAnalysis);
    document.getElementById('coverage-close-btn').addEventListener('click', () => coverageModal.classList.add('hidden'));
    document.querySelector('[data-close="coverage-modal"]')?.addEventListener('click', () => coverageModal.classList.add('hidden'));

    document.getElementById('coverage-artifact-copy')?.addEventListener('click', () => {
        const p = coverageArtifactBox.dataset.path || '';
        if (p) {
            navigator.clipboard.writeText(p).then(() => addLog(`Copied: ${p}`, 'system'));
        }
    });
    document.getElementById('coverage-artifact-open')?.addEventListener('click', () => {
        const p = coverageArtifactBox.dataset.path || '';
        if (p) {
            const folder = p.replace(/[\\\/][^\\\/]+$/, '');
            window.ats.openFolder(folder);
        }
    });

    // Kick off
    init();
});
