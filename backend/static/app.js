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

// Online Ingestion Handler (Admin Hub)
async function handleIngestSubmit(event) {
  if (event) event.preventDefault();
  const urlInput = document.getElementById('ingest-url');
  const btn = document.getElementById('btn-ingest');
  const btnLabel = document.getElementById('btn-ingest-label');
  const spinner = document.getElementById('ingest-spinner');
  const statusEl = document.getElementById('ingest-status');

  const targetUrl = urlInput.value.trim();
  if (!targetUrl) return;

  // 1. Loading UI State: disable button and display «در حال استخراج...»
  btn.disabled = true;
  if (spinner) spinner.style.display = 'inline-block';
  btnLabel.innerText = 'در حال استخراج...';
  statusEl.style.display = 'block';
  statusEl.style.background = 'rgba(56, 189, 248, 0.1)';
  statusEl.style.color = 'var(--accent-cyan)';
  statusEl.style.border = '1px solid rgba(56, 189, 248, 0.25)';
  statusEl.innerText = '⏳ در حال استخراج و ارسال به صف پردازش پس‌زمینه...';

  try {
    const response = await fetch('/api/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: targetUrl })
    });

    if (response.status === 202 || response.ok) {
      const data = await response.json();
      statusEl.style.background = 'rgba(16, 185, 129, 0.12)';
      statusEl.style.color = '#10b981';
      statusEl.style.border = '1px solid rgba(16, 185, 129, 0.3)';
      statusEl.innerText = `✅ عملیات موفق: استخراج و تزریق مستندات در پس‌زمینه آغاز شد (${data.message || targetUrl})`;
      urlInput.value = '';
      setTimeout(checkHealth, 6000);
    } else {
      const errData = await response.json().catch(() => ({ detail: 'خطای سرور' }));
      throw new Error(errData.detail || 'خطا در ارتباط با سرور');
    }
  } catch (error) {
    statusEl.style.background = 'rgba(239, 68, 68, 0.12)';
    statusEl.style.color = '#ef4444';
    statusEl.style.border = '1px solid rgba(239, 68, 68, 0.3)';
    statusEl.innerText = `❌ خطا: ${error.message}`;
  } finally {
    // Re-enable button and reset label
    btn.disabled = false;
    if (spinner) spinner.style.display = 'none';
    btnLabel.innerText = 'Ingest Knowledge';
  }
}

// Initialize
checkHealth();

