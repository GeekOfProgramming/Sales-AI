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
  const slugInput = document.getElementById('ingest-slug');
  const customSlug = slugInput ? slugInput.value.trim() : '';
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
        slug: customSlug || null,
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
        <div style="margin-top: 4px; font-size: 0.8rem; color: #94a3b8;">${data.message || 'Awaiting administrator approval before indexing into ChromaDB.'}</div>
      `;
      urlInput.value = '';
      if (slugInput) slugInput.value = '';
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

let currentArchiveFilter = 'all';
let isArchiveOpen = false;

function toggleArchiveView() {
  const panel = document.getElementById('archive-panel');
  const btn = document.getElementById('btn-toggle-archive');
  if (!panel || !btn) return;
  isArchiveOpen = !isArchiveOpen;
  panel.style.display = isArchiveOpen ? 'block' : 'none';
  const approvedCount = cachedQueueItems.filter(it => it.status === 'approved').length;
  btn.innerHTML = isArchiveOpen 
    ? `📦 Approved &amp; History Archive (${approvedCount}) ▴` 
    : `📦 Approved &amp; History Archive (${approvedCount}) ▾`;
  if (isArchiveOpen) {
    renderArchiveTable();
  }
}

function filterArchive(status) {
  currentArchiveFilter = status;
  ['all', 'approved', 'rejected'].forEach(s => {
    const btn = document.getElementById(`afilter-${s}`);
    if (btn) btn.classList.toggle('active', s === status);
  });
  renderArchiveTable();
}

function renderQueueTable() {
  renderActiveQueueTable();
  renderArchiveTable();
}

function renderActiveQueueTable() {
  const tbody = document.getElementById('queue-table-body');
  const countBadge = document.getElementById('queue-count-badge');
  const archiveBadge = document.getElementById('archive-count-badge');
  if (!tbody) return;

  const activeItems = cachedQueueItems.filter(it => it.status === 'pending' || it.status === 'processing');
  const approvedCount = cachedQueueItems.filter(it => it.status === 'approved').length;
  const rejectedCount = cachedQueueItems.filter(it => it.status === 'rejected').length;
  const historicalTotal = approvedCount + rejectedCount;

  if (countBadge) {
    countBadge.innerText = `${activeItems.length} Pending Action`;
    countBadge.style.color = activeItems.length > 0 ? '#fbbf24' : '#34d399';
  }

  if (archiveBadge) {
    archiveBadge.innerText = approvedCount;
  }

  const acntAll = document.getElementById('acnt-all');
  const acntApp = document.getElementById('acnt-app');
  const acntRej = document.getElementById('acnt-rej');
  if (acntAll) acntAll.innerText = historicalTotal;
  if (acntApp) acntApp.innerText = approvedCount;
  if (acntRej) acntRej.innerText = rejectedCount;

  if (activeItems.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align: center; padding: 28px 20px; color: var(--text-muted);">
          <div style="font-size: 1.6rem; margin-bottom: 6px;">🎉</div>
          <div style="font-weight: 600; color: #34d399; margin-bottom: 4px;">All caught up! No pending submissions.</div>
          <div style="font-size: 0.8rem; color: var(--text-muted);">
            All previous items have been processed and archived. 
            <a href="javascript:void(0)" onclick="toggleArchiveView()" style="color: var(--accent-purple); text-decoration: underline;">Open Approved Archive (${approvedCount} items)</a>
          </div>
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = activeItems.map(item => {
    let badgeClass = item.status === 'processing' ? 'status-processing' : 'status-pending';
    const dateStr = item.submitted_at ? new Date(item.submitted_at).toLocaleString() : '-';
    let actions = '';
    if (item.status === 'pending') {
      actions = `
        <button class="btn-queue-action btn-approve" onclick="approveQueueItem('${item.request_id}')">✅ Approve</button>
        <button class="btn-queue-action btn-reject" onclick="rejectQueueItem('${item.request_id}')">❌ Reject</button>
      `;
    } else if (item.status === 'processing') {
      actions = '<span style="color: var(--accent-cyan); font-size: 0.78rem; font-weight: 600;">Scraping &amp; Indexing...</span>';
    }

    const slugBadge = item.slug ? `<div style="font-size: 0.76rem; color: #a78bfa; margin-top: 4px; font-weight: 500;">🏷️ <code>${item.slug}</code></div>` : '';

    return `
      <tr>
        <td><code style="color: var(--accent-cyan); font-weight: bold;">${item.request_id}</code></td>
        <td style="max-width: 340px; word-break: break-all;">
          <a href="${item.url}" target="_blank" style="color: #cbd5e1; text-decoration: underline;">${item.url}</a>
          ${slugBadge}
        </td>
        <td>${item.submitter || 'Revit Client'}</td>
        <td style="white-space: nowrap; color: var(--text-muted); font-size: 0.8rem;">${dateStr}</td>
        <td><span class="status-badge ${badgeClass}">${item.status}</span></td>
        <td style="white-space: nowrap;">${actions}</td>
      </tr>
    `;
  }).join('');
}

function renderArchiveTable() {
  const tbody = document.getElementById('archive-table-body');
  if (!tbody) return;

  const searchInput = document.getElementById('archive-search-input');
  const query = searchInput ? searchInput.value.trim().toLowerCase() : '';

  let items = cachedQueueItems.filter(it => it.status === 'approved' || it.status === 'rejected');

  if (currentArchiveFilter !== 'all') {
    items = items.filter(it => it.status === currentArchiveFilter);
  }

  if (query) {
    items = items.filter(it => 
      (it.request_id && it.request_id.toLowerCase().includes(query)) ||
      (it.url && it.url.toLowerCase().includes(query)) ||
      (it.slug && it.slug.toLowerCase().includes(query)) ||
      (it.submitter && it.submitter.toLowerCase().includes(query))
    );
  }

  if (items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; padding: 20px; color: var(--text-muted);">No archived items found matching criteria.</td></tr>`;
    return;
  }

  tbody.innerHTML = items.map(item => {
    let badgeClass = item.status === 'approved' ? 'status-approved' : 'status-rejected';
    const dateStr = item.processed_at ? new Date(item.processed_at).toLocaleString() : (item.submitted_at ? new Date(item.submitted_at).toLocaleDateString() : '-');
    const slugBadge = item.slug ? `<div style="font-size: 0.76rem; color: #a78bfa; margin-top: 3px;">🏷️ <code>${item.slug}</code></div>` : '';
    const noteMsg = item.message ? `<div style="font-size: 0.74rem; color: #94a3b8; margin-top: 2px;">${item.message}</div>` : '';

    return `
      <tr>
        <td><code style="color: var(--text-muted);">${item.request_id}</code></td>
        <td style="max-width: 320px; word-break: break-all;">
          <a href="${item.url}" target="_blank" style="color: #94a3b8; text-decoration: underline;">${item.url}</a>
          ${slugBadge}
        </td>
        <td style="color: var(--text-muted); font-size: 0.8rem;">${item.submitter || 'Revit Client'}</td>
        <td style="white-space: nowrap; color: var(--text-muted); font-size: 0.78rem;">${dateStr}</td>
        <td><span class="status-badge ${badgeClass}">${item.status}</span></td>
        <td style="font-size: 0.76rem; max-width: 240px; color: ${item.status === 'approved' ? '#34d399' : '#f87171'};">
          ${noteMsg || (item.status === 'approved' ? 'Indexed in ChromaDB' : 'Rejected')}
        </td>
      </tr>
    `;
  }).join('');
}

async function clearQueueHistory() {
  if (!confirm("Are you sure you want to purge all approved and rejected historical logs?\\n\\nActive pending requests will be preserved.")) return;
  try {
    const res = await fetch('/api/ingest/queue/clear-history', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${currentAdminToken}` }
    });
    if (!res.ok) throw new Error("Failed to clear history");
    const data = await res.json();
    alert(`Archive purged successfully: ${data.removed_count} records removed.`);
    await loadKnowledgeQueue();
  } catch (err) {
    alert("Error clearing history: " + err.message);
  }
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


// --- Autodesk Construction Cloud (ACC / APS) Configuration Handlers ---

function openACCConfigModal() {
  document.getElementById('acc-config-modal').style.display = 'flex';
  const alertEl = document.getElementById('acc-config-alert');
  if (alertEl) alertEl.style.display = 'none';
  loadACCConfigStatus();
}

function closeACCConfigModal() {
  document.getElementById('acc-config-modal').style.display = 'none';
}

function toggleSecretVisibility() {
  const secretInput = document.getElementById('aps-client-secret');
  if (!secretInput) return;
  secretInput.type = secretInput.type === 'password' ? 'text' : 'password';
}

async function loadACCConfigStatus() {
  const badge = document.getElementById('acc-status-badge');
  const title = document.getElementById('acc-modal-mode-title');
  const desc = document.getElementById('acc-modal-mode-desc');
  const maskedId = document.getElementById('acc-modal-masked-id');

  try {
    const res = await fetch('/api/acc/config');
    if (!res.ok) throw new Error('Failed to fetch ACC status');
    const data = await res.json();

    if (data.is_live) {
      if (badge) {
        badge.className = 'status-badge status-approved';
        badge.innerText = '🟢 Live ACC Connected';
      }
      if (title) {
        title.style.color = '#34d399';
        title.innerText = '🟢 Live Autodesk Platform Services Connected';
      }
      if (desc) desc.innerText = data.mode_description;
      if (maskedId) {
        maskedId.style.display = 'block';
        maskedId.innerText = `Active Client ID: ${data.client_id_masked || 'Configured'}`;
      }
    } else {
      if (badge) {
        badge.className = 'status-badge status-pending';
        badge.innerText = '🟡 Simulation Mode';
      }
      if (title) {
        title.style.color = '#f59e0b';
        title.innerText = '🟡 Offline Simulation Mode';
      }
      if (desc) desc.innerText = data.mode_description;
      if (maskedId) {
        maskedId.style.display = 'none';
      }
    }
    return data;
  } catch (err) {
    if (badge) {
      badge.className = 'status-badge status-rejected';
      badge.innerText = '🔴 Cloud API Offline';
    }
  }
}

async function testACCConnection() {
  if (!isAdminLoggedIn()) {
    alert('Administrative privileges required. Please sign in as Admin first.');
    openLoginModal();
    return;
  }

  const clientId = document.getElementById('aps-client-id').value.trim();
  const clientSecret = document.getElementById('aps-client-secret').value.trim();
  const alertEl = document.getElementById('acc-config-alert');
  const spinner = document.getElementById('acc-test-spinner');
  const btnLabel = document.getElementById('btn-test-acc-label');
  const btn = document.getElementById('btn-test-acc');

  if (!clientId || !clientSecret) {
    alertEl.style.display = 'block';
    alertEl.style.background = 'rgba(239, 68, 68, 0.15)';
    alertEl.style.color = '#f87171';
    alertEl.style.border = '1px solid rgba(239, 68, 68, 0.3)';
    alertEl.innerText = '⚠️ Please provide both Client ID and Client Secret to test connection.';
    return;
  }

  btn.disabled = true;
  if (spinner) spinner.style.display = 'inline-block';
  btnLabel.innerText = 'Verifying with Autodesk...';
  alertEl.style.display = 'none';

  try {
    const res = await fetch('/api/acc/config/test', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${currentAdminToken}`
      },
      body: JSON.stringify({
        client_id: clientId,
        client_secret: clientSecret
      })
    });

    const data = await res.json();
    alertEl.style.display = 'block';

    if (res.ok && data.success) {
      alertEl.style.background = 'rgba(16, 185, 129, 0.15)';
      alertEl.style.color = '#34d399';
      alertEl.style.border = '1px solid rgba(16, 185, 129, 0.3)';
      alertEl.innerText = `✅ Success: ${data.message}`;
    } else {
      alertEl.style.background = 'rgba(239, 68, 68, 0.15)';
      alertEl.style.color = '#f87171';
      alertEl.style.border = '1px solid rgba(239, 68, 68, 0.3)';
      alertEl.innerText = `❌ Verification Failed: ${data.message || 'Invalid credentials'}`;
    }
  } catch (err) {
    alertEl.style.display = 'block';
    alertEl.style.background = 'rgba(239, 68, 68, 0.15)';
    alertEl.style.color = '#f87171';
    alertEl.style.border = '1px solid rgba(239, 68, 68, 0.3)';
    alertEl.innerText = `❌ Network Error: ${err.message}`;
  } finally {
    btn.disabled = false;
    if (spinner) spinner.style.display = 'none';
    btnLabel.innerText = '🧪 Test Connection';
  }
}

async function handleACCConfigSubmit(e) {
  e.preventDefault();

  if (!isAdminLoggedIn()) {
    alert('Administrative privileges required. Please sign in as Admin first.');
    openLoginModal();
    return;
  }

  const clientId = document.getElementById('aps-client-id').value.trim();
  const clientSecret = document.getElementById('aps-client-secret').value.trim();
  const alertEl = document.getElementById('acc-config-alert');
  const spinner = document.getElementById('acc-save-spinner');
  const btnLabel = document.getElementById('btn-save-acc-label');
  const btn = document.getElementById('btn-save-acc');

  if (!clientId || !clientSecret) {
    alertEl.style.display = 'block';
    alertEl.style.background = 'rgba(239, 68, 68, 0.15)';
    alertEl.style.color = '#f87171';
    alertEl.style.border = '1px solid rgba(239, 68, 68, 0.3)';
    alertEl.innerText = '⚠️ Both Client ID and Client Secret are required.';
    return;
  }

  btn.disabled = true;
  if (spinner) spinner.style.display = 'inline-block';
  btnLabel.innerText = 'Saving & Activating...';
  alertEl.style.display = 'none';

  try {
    const res = await fetch('/api/acc/config', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${currentAdminToken}`
      },
      body: JSON.stringify({
        client_id: clientId,
        client_secret: clientSecret,
        force_mock: false
      })
    });

    const data = await res.json();
    alertEl.style.display = 'block';

    if (!res.ok) {
      alertEl.style.background = 'rgba(239, 68, 68, 0.15)';
      alertEl.style.color = '#f87171';
      alertEl.style.border = '1px solid rgba(239, 68, 68, 0.3)';
      alertEl.innerText = `❌ ${data.detail || 'Failed to save configuration'}`;
      return;
    }

    alertEl.style.background = 'rgba(16, 185, 129, 0.15)';
    alertEl.style.color = '#34d399';
    alertEl.style.border = '1px solid rgba(16, 185, 129, 0.3)';
    alertEl.innerText = `✅ ${data.message || 'Autodesk Cloud credentials activated and saved to .env!'}`;

    // Update status display
    loadACCConfigStatus();
    // Refresh Hubs dropdown
    loadACCHubs();

    // Clear secret input for safety
    document.getElementById('aps-client-secret').value = '';
  } catch (err) {
    alertEl.style.display = 'block';
    alertEl.style.background = 'rgba(239, 68, 68, 0.15)';
    alertEl.style.color = '#f87171';
    alertEl.style.border = '1px solid rgba(239, 68, 68, 0.3)';
    alertEl.innerText = `❌ Error: ${err.message}`;
  } finally {
    btn.disabled = false;
    if (spinner) spinner.style.display = 'none';
    btnLabel.innerText = '💾 Save & Connect Live';
  }
}

async function revertACCToMock() {
  if (!isAdminLoggedIn()) {
    alert('Administrative privileges required. Please sign in as Admin first.');
    openLoginModal();
    return;
  }

  if (!confirm('Revert to Offline Simulation / Mock Mode? This will clear active credentials from memory and .env.')) {
    return;
  }

  const alertEl = document.getElementById('acc-config-alert');

  try {
    const res = await fetch('/api/acc/config', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${currentAdminToken}`
      },
      body: JSON.stringify({
        force_mock: true
      })
    });

    const data = await res.json();
    document.getElementById('aps-client-id').value = '';
    document.getElementById('aps-client-secret').value = '';

    alertEl.style.display = 'block';
    alertEl.style.background = 'rgba(245, 158, 11, 0.15)';
    alertEl.style.color = '#fbbf24';
    alertEl.style.border = '1px solid rgba(245, 158, 11, 0.3)';
    alertEl.innerText = `ℹ️ ${data.message || 'Switched back to safe Simulation Mode.'}`;

    loadACCConfigStatus();
    loadACCHubs();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}


// --- Initialize ---
checkHealth();
updateAuthUI();
loadKnowledgeQueue();
loadACCHubs();
loadACCConfigStatus();



