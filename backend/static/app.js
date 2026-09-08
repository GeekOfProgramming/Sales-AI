let currentEnvironment = 'pyrevit';
let currentLanguage = 'python';

// Check Health on Page Load
async function checkHealth() {
  try {
    const res = await fetch('/health');
    if (!res.ok) throw new Error('Unreachable');
    const data = await res.json();
    
    document.getElementById('health-dot').style.background = data.status === 'healthy' ? '#10b981' : '#f59e0b';
    document.getElementById('health-text').innerText = data.status === 'healthy' ? 'Ollama & Vector DB Healthy' : 'Degraded Services';
    document.getElementById('rules-pill').innerText = `📚 ${data.indexed_rules_count} BIM Rules Active`;
    if (data.available_models.length > 0) {
      document.getElementById('model-pill').innerText = `🧠 ${data.available_models[0]}`;
    }
  } catch (err) {
    document.getElementById('health-dot').style.background = '#ef4444';
    document.getElementById('health-text').innerText = 'Core Services Offline';
  }
}

function setEnvironment(env) {
  currentEnvironment = env;
  if (env === 'csharp') {
    currentLanguage = 'csharp';
  } else {
    currentLanguage = 'python';
  }
  document.getElementById('btn-py').classList.toggle('active', env === 'pyrevit');
  document.getElementById('btn-cs').classList.toggle('active', env === 'csharp');
  const btnDyn = document.getElementById('btn-dyn');
  if (btnDyn) btnDyn.classList.toggle('active', env === 'dynamo');

  const labelMap = {
    'pyrevit': 'Python (pyRevit)',
    'csharp': 'C# (.NET Command)',
    'dynamo': 'Dynamo Python Script'
  };
  document.getElementById('code-lang-label').innerText = labelMap[env] || env;
}

function setLanguage(lang) {
  setEnvironment(lang === 'csharp' ? 'csharp' : 'pyrevit');
}


function setPreset(num) {
  const promptEl = document.getElementById('user-prompt');
  if (num === 1) {
    promptEl.value = "Set FireRating parameter of all selected walls to '2 Hours' inside an active Revit Transaction.";
    setLanguage('python');
    addSampleElements();
  } else if (num === 2) {
    promptEl.value = "Validate and rename information container following ISO 19650 standard for Project 'HQB01', Whole Building 'ZZ', Level '00', 3D Architectural Model number 0001.";
    setLanguage('python');
  } else if (num === 3) {
    promptEl.value = "Write an IExternalCommand in C# to collect and count all Door instances in the active view.";
    setLanguage('csharp');
  } else if (num === 4) {
    promptEl.value = "Calculate total length and volume of selected walls and output element IDs.";
    setLanguage('python');
    addSampleElements();
  }
}

function addSampleElements() {
  const sample = [
    {"element_id": 482910, "category": "OST_Walls", "name": "Basic Wall - Generic 200mm", "parameters": {"FireRating": "None", "Length": 5.4}},
    {"element_id": 482911, "category": "OST_Walls", "name": "Basic Wall - Generic 200mm", "parameters": {"FireRating": "None", "Length": 3.8}}
  ];
  document.getElementById('elem-context').value = JSON.stringify(sample, null, 2);
}

async function generateCode() {
  const prompt = document.getElementById('user-prompt').value.trim();
  if (!prompt) {
    alert('Please enter a natural language instruction.');
    return;
  }

  let selectedElements = [];
  const elemText = document.getElementById('elem-context').value.trim();
  if (elemText) {
    try {
      selectedElements = JSON.parse(elemText);
    } catch (e) {
      alert('Invalid JSON in Selected Elements field.');
      return;
    }
  }

  const btn = document.getElementById('btn-generate');
  const spinner = document.getElementById('spinner');
  const btnLabel = document.getElementById('btn-label');
  const timer = document.getElementById('exec-timer');

  btn.disabled = true;
  spinner.style.display = 'inline-block';
  btnLabel.innerText = 'Generating with AI...';
  const startTime = performance.now();
  timer.innerText = 'Inference in progress...';

  try {
    const response = await fetch('/generate-script', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_prompt: prompt,
        environment: currentEnvironment,
        language: currentLanguage,
        selected_elements: selectedElements,
        include_rag_rules: true,
        temperature: 0.1
      })
    });

    if (!response.ok) {
      const errData = await response.json();
      throw new Error(errData.detail || 'Server error');
    }

    const data = await response.json();
    const duration = ((performance.now() - startTime) / 1000).toFixed(2);
    timer.innerText = `Completed in ${duration}s (Model: ${data.model_used})`;

    document.getElementById('output-code').innerText = data.code;

    // Render RAG badges
    const ragPanel = document.getElementById('rag-panel');
    if (data.retrieved_sources && data.retrieved_sources.length > 0) {
      ragPanel.innerHTML = '<strong>Applied Knowledge Documents:</strong><br>' +
        data.retrieved_sources.map(src => `<span class="rag-badge">📄 ${src}</span>`).join(' ') +
        `<div style="margin-top: 8px; color: var(--text-secondary); font-size: 0.8rem;">${data.validation_notes || ''}</div>`;
    } else {
      ragPanel.innerHTML = '<span style="color: var(--text-muted);">No specific external rules required for this command.</span>';
    }

  } catch (error) {
    timer.innerText = 'Error occurred';
    alert('Generation Failed: ' + error.message);
  } finally {
    btn.disabled = false;
    spinner.style.display = 'none';
    btnLabel.innerText = '✨ Generate Revit Code';
  }
}

function copyCode() {
  const code = document.getElementById('output-code').innerText;
  navigator.clipboard.writeText(code).then(() => {
    alert('Code copied to clipboard!');
  });
}

// --- Authentication & JWT Layer ---
let currentAdminToken = localStorage.getItem('pybim_admin_token') || null;
let currentAdminUser = localStorage.getItem('pybim_admin_user') || null;
let cachedQueueItems = [];
let currentQueueFilter = 'all';

function isAdminLoggedIn() {
  return Boolean(currentAdminToken);
}

function updateAuthUI() {
  const container = document.getElementById('auth-header-container');
  if (!container) return;

  if (isAdminLoggedIn()) {
    container.innerHTML = `
      <div class="admin-active-badge">
        <span>🛡️ Admin: ${currentAdminUser || 'admin'}</span>
        <button onclick="logoutAdmin()" style="background:none; border:none; color:#f87171; cursor:pointer; font-size:0.75rem; font-weight:700; text-decoration:underline;">Logout</button>
      </div>
    `;
  } else {
    container.innerHTML = `
      <button class="btn-auth" onclick="openLoginModal()">🔐 Admin Login</button>
    `;
  }
}

function openLoginModal() {
  document.getElementById('login-modal').style.display = 'flex';
  document.getElementById('login-error').style.display = 'none';
  document.getElementById('login-password').value = '';
}

function closeLoginModal() {
  document.getElementById('login-modal').style.display = 'none';
}

function handleAuthButtonClick() {
  if (isAdminLoggedIn()) {
    logoutAdmin();
  } else {
    openLoginModal();
  }
}

async function handleLoginSubmit(event) {
  event.preventDefault();
  const username = document.getElementById('login-username').value.trim();
  const password = document.getElementById('login-password').value.trim();
  const errorEl = document.getElementById('login-error');
  const btn = document.getElementById('btn-login-submit');

  btn.disabled = true;
  errorEl.style.display = 'none';

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Authentication failed' }));
      throw new Error(err.detail || 'Invalid username or password');
    }

    const data = await res.json();
    currentAdminToken = data.access_token;
    currentAdminUser = data.username;
    localStorage.setItem('pybim_admin_token', data.access_token);
    localStorage.setItem('pybim_admin_user', data.username);

    closeLoginModal();
    updateAuthUI();
    loadKnowledgeQueue();
  } catch (err) {
    errorEl.innerText = `❌ ${err.message}`;
    errorEl.style.display = 'block';
  } finally {
    btn.disabled = false;
  }
}

function logoutAdmin() {
  currentAdminToken = null;
  currentAdminUser = null;
  localStorage.removeItem('pybim_admin_token');
  localStorage.removeItem('pybim_admin_user');
  updateAuthUI();
  loadKnowledgeQueue();
}


// --- Submission Handler (Stage 1: User Submission into Queue) ---
async function handleIngestSubmit(event) {
  if (event) event.preventDefault();
  const urlInput = document.getElementById('ingest-url');
  const submitterInput = document.getElementById('ingest-submitter');
  const btn = document.getElementById('btn-ingest');
  const btnLabel = document.getElementById('btn-ingest-label');
  const spinner = document.getElementById('ingest-spinner');
  const statusEl = document.getElementById('ingest-status');

  const targetUrl = urlInput.value.trim();
  const submitter = submitterInput ? submitterInput.value.trim() : 'Revit User';
  if (!targetUrl) return;

  btn.disabled = true;
  if (spinner) spinner.style.display = 'inline-block';
  btnLabel.innerText = 'Submitting to Queue...';
  statusEl.style.display = 'block';
  statusEl.style.background = 'rgba(56, 189, 248, 0.1)';
  statusEl.style.color = 'var(--accent-cyan)';
  statusEl.style.border = '1px solid rgba(56, 189, 248, 0.25)';
  statusEl.innerText = '⏳ Submitting documentation into Admin Review Queue...';

  try {
    const response = await fetch('/api/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: targetUrl,
        submitter: submitter || 'Revit User'
      })
    });

    if (response.status === 202 || response.ok) {
      const data = await response.json();
      statusEl.style.background = 'rgba(16, 185, 129, 0.12)';
      statusEl.style.color = '#10b981';
      statusEl.style.border = '1px solid rgba(16, 185, 129, 0.3)';
      statusEl.innerHTML = `
        <div><strong>✅ Request Submitted to Admin Queue!</strong></div>
        <div style="margin-top: 4px;">Tracking ID: <strong style="color: #fff; background: rgba(255,255,255,0.1); padding: 2px 6px; border-radius: 4px;">${data.request_id}</strong></div>
        <div style="margin-top: 4px; font-size: 0.8rem; color: #94a3b8;">Awaiting administrator approval before indexing into ChromaDB.</div>
      `;
      urlInput.value = '';
      const trackInput = document.getElementById('track-id-input');
      if (trackInput) trackInput.value = data.request_id;
      loadKnowledgeQueue();
    } else {
      const errData = await response.json().catch(() => ({ detail: 'Server Error' }));
      throw new Error(errData.detail || 'Failed to submit documentation');
    }
  } catch (error) {
    statusEl.style.background = 'rgba(239, 68, 68, 0.12)';
    statusEl.style.color = '#ef4444';
    statusEl.style.border = '1px solid rgba(239, 68, 68, 0.3)';
    statusEl.innerText = `❌ Error: ${error.message}`;
  } finally {
    btn.disabled = false;
    if (spinner) spinner.style.display = 'none';
    btnLabel.innerText = '📥 Submit for Admin Review';
  }
}


// --- User Tracking Widget ---
async function trackSubmissionStatus() {
  const input = document.getElementById('track-id-input');
  const resultEl = document.getElementById('track-result');
  const trackingId = input.value.trim();

  if (!trackingId) {
    alert('Please enter a valid Tracking ID (e.g. req_a1b2c3d4)');
    return;
  }

  resultEl.style.display = 'block';
  resultEl.style.background = 'rgba(255, 255, 255, 0.05)';
  resultEl.style.color = '#cbd5e1';
  resultEl.innerText = 'Checking status...';

  try {
    const res = await fetch(`/api/ingest/status/${encodeURIComponent(trackingId)}`);
    if (!res.ok) {
      if (res.status === 404) throw new Error(`Tracking ID '${trackingId}' not found.`);
      throw new Error('Failed to fetch status.');
    }

    const item = await res.json();
    let badgeClass = 'status-pending';
    let statusText = '🟡 Pending Review';
    if (item.status === 'approved') {
      badgeClass = 'status-approved';
      statusText = '🟢 Approved & Indexed into ChromaDB';
    } else if (item.status === 'rejected') {
      badgeClass = 'status-rejected';
      statusText = '🔴 Rejected by Administrator';
    } else if (item.status === 'processing') {
      badgeClass = 'status-processing';
      statusText = '⚙️ Scraping & Vectorizing in Progress';
    }

    resultEl.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
        <span class="status-badge ${badgeClass}">${statusText}</span>
        <span style="font-size: 0.75rem; color: var(--text-muted);">${new Date(item.submitted_at).toLocaleTimeString()}</span>
      </div>
      <div style="word-break: break-all; color: #94a3b8; font-size: 0.78rem;"><strong>URL:</strong> ${item.url}</div>
      <div style="margin-top: 4px; font-size: 0.78rem; color: #cbd5e1;"><strong>Note:</strong> ${item.message || 'No additional note'}</div>
    `;
  } catch (err) {
    resultEl.style.background = 'rgba(239, 68, 68, 0.1)';
    resultEl.style.color = '#f87171';
    resultEl.innerText = `❌ ${err.message}`;
  }
}


// --- Knowledge Queue Table & Admin Actions ---
async function loadKnowledgeQueue() {
  const tbody = document.getElementById('queue-table-body');
  const countBadge = document.getElementById('queue-count-badge');
  if (!tbody) return;

  if (!isAdminLoggedIn()) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align: center; padding: 24px; color: var(--text-muted);">
          🔒 Administrative privileges required to view and manage Knowledge Queue.<br>
          <button class="btn-auth" onclick="openLoginModal()" style="margin: 10px auto 0 auto;">🔐 Admin Login</button>
        </td>
      </tr>
    `;
    if (countBadge) countBadge.innerText = 'Admin Login Required';
    return;
  }

  try {
    const res = await fetch('/api/ingest/queue', {
      headers: { 'Authorization': `Bearer ${currentAdminToken}` }
    });

    if (res.status === 401 || res.status === 403) {
      logoutAdmin();
      return;
    }

    if (!res.ok) throw new Error('Failed to load queue');

    cachedQueueItems = await res.json();
    renderQueueTable();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: #f87171; padding: 16px;">Error loading queue: ${err.message}</td></tr>`;
  }
}

function filterQueue(status) {
  currentQueueFilter = status;
  ['all', 'pending', 'approved', 'rejected'].forEach(s => {
    const btn = document.getElementById(`qfilter-${s}`);
    if (btn) btn.classList.toggle('active', s === status);
  });
  renderQueueTable();
}

function renderQueueTable() {
  const tbody = document.getElementById('queue-table-body');
  const countBadge = document.getElementById('queue-count-badge');
  if (!tbody) return;

  let items = cachedQueueItems;
  if (currentQueueFilter !== 'all') {
    items = items.filter(it => it.status === currentQueueFilter);
  }

  if (countBadge) {
    const pendingCount = cachedQueueItems.filter(it => it.status === 'pending').length;
    countBadge.innerText = `${pendingCount} Pending Approval`;
  }

  if (items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; padding: 20px; color: var(--text-muted);">No submissions found in this category.</td></tr>`;
    return;
  }

  tbody.innerHTML = items.map(item => {
    let badgeClass = 'status-pending';
    if (item.status === 'approved') badgeClass = 'status-approved';
    else if (item.status === 'rejected') badgeClass = 'status-rejected';
    else if (item.status === 'processing') badgeClass = 'status-processing';

    const dateStr = item.submitted_at ? new Date(item.submitted_at).toLocaleDateString() : '-';

    let actions = '<span style="color: var(--text-muted); font-size: 0.75rem;">Archived</span>';
    if (item.status === 'pending') {
      actions = `
        <button class="btn-queue-action btn-approve" onclick="approveQueueItem('${item.request_id}')">✅ Approve</button>
        <button class="btn-queue-action btn-reject" onclick="rejectQueueItem('${item.request_id}')">❌ Reject</button>
      `;
    } else if (item.status === 'processing') {
      actions = '<span style="color: var(--accent-cyan); font-size: 0.75rem;">Processing...</span>';
    } else if (item.status === 'approved') {
      actions = '<span style="color: #34d399; font-size: 0.75rem;">Indexed ✓</span>';
    }

    return `
      <tr>
        <td><code style="color: var(--accent-cyan);">${item.request_id}</code></td>
        <td style="max-width: 280px; word-break: break-all;">
          <a href="${item.url}" target="_blank" style="color: #cbd5e1; text-decoration: underline;">${item.url}</a>
        </td>
        <td>${item.submitter || 'Revit Client'}</td>
        <td style="white-space: nowrap; color: var(--text-muted); font-size: 0.8rem;">${dateStr}</td>
        <td><span class="status-badge ${badgeClass}">${item.status}</span></td>
        <td style="white-space: nowrap;">${actions}</td>
      </tr>
    `;
  }).join('');
}

async function approveQueueItem(requestId) {
  if (!confirm(`Approve request ${requestId} and trigger ChromaDB scraping?`)) return;

  try {
    const res = await fetch('/api/ingest/approve', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${currentAdminToken}`
      },
      body: JSON.stringify({ request_id: requestId })
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to approve' }));
      throw new Error(err.detail || 'Approval failed');
    }

    loadKnowledgeQueue();
    setTimeout(checkHealth, 5000);
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

async function rejectQueueItem(requestId) {
  const reason = prompt('Reason for rejection (optional):', 'Failed compliance check');
  if (reason === null) return; // User cancelled prompt

  try {
    const res = await fetch('/api/ingest/reject', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${currentAdminToken}`
      },
      body: JSON.stringify({ request_id: requestId, reason: reason })
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to reject' }));
      throw new Error(err.detail || 'Rejection failed');
    }

    loadKnowledgeQueue();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}


// --- Cloud BIM (Autodesk Construction Cloud - ACC) Handlers ---
let currentAuditData = null;

async function loadACCHubs() {
  const hubSelect = document.getElementById('acc-hub-select');
  if (!hubSelect) return;

  try {
    const res = await fetch('/api/acc/hubs');
    if (!res.ok) throw new Error('Failed to load hubs');
    const hubs = await res.json();

    hubSelect.innerHTML = '<option value="">-- Choose Corporate Hub --</option>' +
      hubs.map(h => `<option value="${h.hub_id}">${h.name} (${h.region})</option>`).join('');
    
    // Auto-select first hub for convenience
    if (hubs.length > 0) {
      hubSelect.value = hubs[0].hub_id;
      handleHubChange();
    }
  } catch (err) {
    hubSelect.innerHTML = `<option value="">Error: ${err.message}</option>`;
  }
}

async function handleHubChange() {
  const hubSelect = document.getElementById('acc-hub-select');
  const projSelect = document.getElementById('acc-project-select');
  const modSelect = document.getElementById('acc-model-select');
  const hubId = hubSelect.value;

  projSelect.innerHTML = '<option value="">Loading Projects...</option>';
  modSelect.innerHTML = '<option value="">Select a Project first</option>';

  if (!hubId) return;

  try {
    const res = await fetch(`/api/acc/projects/${encodeURIComponent(hubId)}`);
    if (!res.ok) throw new Error('Failed to load projects');
    const projects = await res.json();

    projSelect.innerHTML = '<option value="">-- Choose Project --</option>' +
      projects.map(p => `<option value="${p.project_id}">${p.name} [${p.project_type}]</option>`).join('');

    if (projects.length > 0) {
      projSelect.value = projects[0].project_id;
      handleProjectChange();
    }
  } catch (err) {
    projSelect.innerHTML = `<option value="">Error: ${err.message}</option>`;
  }
}

async function handleProjectChange() {
  const projSelect = document.getElementById('acc-project-select');
  const modSelect = document.getElementById('acc-model-select');
  const projectId = projSelect.value;

  modSelect.innerHTML = '<option value="">Loading Models...</option>';
  if (!projectId) return;

  try {
    const res = await fetch(`/api/acc/models/${encodeURIComponent(projectId)}`);
    if (!res.ok) throw new Error('Failed to load models');
    const models = await res.json();

    modSelect.innerHTML = '<option value="">-- Choose Cloud Model (.rvt) --</option>' +
      models.map(m => `<option value="${m.urn}" data-name="${m.name}">${m.name} (v${m.version} • ${m.file_size_mb} MB)</option>`).join('');

    if (models.length > 0) {
      modSelect.value = models[0].urn;
    }
  } catch (err) {
    modSelect.innerHTML = `<option value="">Error: ${err.message}</option>`;
  }
}

async function runCloudModelAudit() {
  const modSelect = document.getElementById('acc-model-select');
  const urn = modSelect.value;
  const btn = document.getElementById('btn-run-audit');
  const btnLabel = document.getElementById('audit-btn-label');
  const spinner = document.getElementById('audit-spinner');
  const resultsCard = document.getElementById('cloud-audit-results');

  if (!urn) {
    alert('Please select a Cloud Model (.rvt) first.');
    return;
  }

  btn.disabled = true;
  if (spinner) spinner.style.display = 'inline-block';
  btnLabel.innerText = 'Extracting Metadata & Auditing...';

  try {
    const res = await fetch('/api/acc/audit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urn: urn })
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Audit failed' }));
      throw new Error(err.detail || 'Audit execution error');
    }

    currentAuditData = await res.json();
    renderCloudAuditResults(currentAuditData);
  } catch (err) {
    alert(`Audit Error: ${err.message}`);
  } finally {
    btn.disabled = false;
    if (spinner) spinner.style.display = 'none';
    btnLabel.innerText = '🔍 Run Read-Only Cloud Audit';
  }
}

function renderCloudAuditResults(data) {
  const resultsCard = document.getElementById('cloud-audit-results');
  const titleEl = document.getElementById('audit-model-title');
  const metaEl = document.getElementById('audit-model-meta');
  const scoreBadge = document.getElementById('audit-score-badge');
  const tbody = document.getElementById('cloud-issues-table-body');

  resultsCard.style.display = 'block';
  titleEl.innerText = `📄 ${data.model_name}`;
  metaEl.innerText = `Elements Audited: ${data.total_elements_audited} | Checks: ${data.total_checks_evaluated} | Time: ${new Date(data.audited_at).toLocaleTimeString()}`;

  let scoreClass = 'status-approved';
  if (data.compliance_score < 70) scoreClass = 'status-rejected';
  else if (data.compliance_score < 90) scoreClass = 'status-pending';

  scoreBadge.className = `status-badge ${scoreClass}`;
  scoreBadge.innerText = `Compliance Score: ${data.compliance_score}% (${data.status})`;

  if (data.issues.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: #10b981; padding: 20px;">✅ All parameter checks passed with 100% compliance! No issues detected.</td></tr>';
    return;
  }

  tbody.innerHTML = data.issues.map((iss, idx) => {
    let sevColor = '#94a3b8';
    if (iss.severity === 'HIGH') sevColor = '#f87171';
    else if (iss.severity === 'MEDIUM') sevColor = '#fbbf24';

    const elemIdStr = iss.element_id ? `#${iss.element_id}` : 'Container';

    return `
      <tr>
        <td>
          <input type="checkbox" class="cloud-issue-chk" data-elem-id="${iss.element_id || 0}" checked style="cursor: pointer;" />
        </td>
        <td><code style="color: var(--accent-cyan); font-size: 0.8rem;">${elemIdStr}</code></td>
        <td>${iss.category}</td>
        <td><strong>${iss.parameter}</strong></td>
        <td><span style="color: #f87171; text-decoration: line-through;">${iss.current_value}</span></td>
        <td><span style="color: #34d399; font-weight: 600;">${iss.proposed_value}</span></td>
        <td><span style="color: ${sevColor}; font-weight: 600; font-size: 0.75rem;">${iss.severity}</span></td>
      </tr>
    `;
  }).join('');
}

async function applyCloudCorrections() {
  if (!currentAuditData) return;

  if (!isAdminLoggedIn()) {
    alert('Administrative privileges required. Please sign in as Admin.');
    openLoginModal();
    return;
  }

  const checkboxes = document.querySelectorAll('.cloud-issue-chk:checked');
  const approvedElementIds = Array.from(checkboxes).map(c => parseInt(c.getAttribute('data-elem-id'))).filter(id => id > 0);

  if (approvedElementIds.length === 0) {
    alert('Please select at least one element correction to authorize.');
    return;
  }

  if (!confirm(`Authorize ${approvedElementIds.length} element corrections for synchronization to Autodesk Construction Cloud?`)) {
    return;
  }

  try {
    const res = await fetch('/api/acc/apply-changes', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${currentAdminToken}`
      },
      body: JSON.stringify({
        urn: currentAuditData.urn,
        approved_elements: approvedElementIds,
        reviewer_notes: 'Human-in-the-Loop review confirmed by Administrator.'
      })
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to authorize' }));
      throw new Error(err.detail || 'Authorization failed');
    }

    const data = await res.json();
    alert(`✅ ${data.message}`);
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}


// --- Initialize ---
checkHealth();
updateAuthUI();
loadKnowledgeQueue();
loadACCHubs();



