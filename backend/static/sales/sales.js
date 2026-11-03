/**
 * SalesAI Phase 10.5 Operator Console client script.
 * Zero secrets, zero direct writes, modal safety confirmations.
 * Displays exact final immutable preview (From Name, From Email, To, Company, Subject, Full Body, Revision).
 */

document.addEventListener("DOMContentLoaded", function () {
  // Setup Send Confirmation Modal
  const sendModal = document.getElementById("send-confirm-modal");
  const sendConfirmForm = document.getElementById("send-confirm-form");
  const sendDraftIdInput = document.getElementById("modal-send-draft-id");
  const sendRevisionInput = document.getElementById("modal-send-revision");
  const sendDetailsDiv = document.getElementById("modal-send-details");

  window.openSendConfirmation = function (draftId, revision, recipient, subject, company, fromName, fromEmail, fullBody) {
    if (!sendModal) return;
    if (sendDraftIdInput) sendDraftIdInput.value = draftId;
    if (sendRevisionInput) sendRevisionInput.value = revision;
    if (sendConfirmForm) sendConfirmForm.action = `/sales/drafts/${encodeURIComponent(draftId)}/send`;
    const senderDisplay = fromEmail ? escapeHtml(fromEmail) : '<span style="color:#ef4444; font-weight:bold;">Sender not configured</span>';
    const nameDisplay = fromName ? escapeHtml(fromName) : '<span style="color:#94a3b8;">Sender not configured</span>';
    const canSubmit = Boolean(fromEmail && fromEmail.trim());
    if (sendDetailsDiv) {
      sendDetailsDiv.innerHTML = `
        <div style="background:#111827; padding:0.85rem; border-radius:6px; margin:0.75rem 0; font-size:0.875rem;">
          <p><strong>From Name:</strong> ${nameDisplay}</p>
          <p><strong>From Email:</strong> <code>${senderDisplay}</code></p>
          <p><strong>To:</strong> <code>${escapeHtml(recipient)}</code></p>
          <p><strong>Company:</strong> ${escapeHtml(company || 'N/A')}</p>
          <p><strong>Subject:</strong> ${escapeHtml(subject)}</p>
          <p><strong>Revision:</strong> r${escapeHtml(revision)}</p>
          <p style="margin-top:0.5rem;"><strong>Full Body:</strong></p>
          <pre style="background:#0b0f19; padding:0.6rem; border-radius:4px; max-height:160px; overflow-y:auto; white-space:pre-wrap; font-family:monospace; font-size:0.8rem; color:#cbd5e1; border:1px solid #1e293b;">${escapeHtml(fullBody || '')}</pre>
        </div>
        ${!canSubmit ? '<p style="color:#ef4444; font-weight:700; margin-top:0.75rem;">⛔ Cannot send: Sender email is not configured.</p>' : '<p style="color:#ef4444; font-weight:700; margin-top:0.75rem;">⚠️ This will send one real email.</p>'}
      `;
    }
    const submitBtn = sendModal.querySelector('button[type="submit"]');
    if (submitBtn) {
      submitBtn.disabled = !canSubmit;
    }
    sendModal.classList.add("active");
  };

  window.closeSendModal = function () {
    if (sendModal) sendModal.classList.remove("active");
  };

  // Close modals when clicking backdrop
  document.querySelectorAll(".modal-overlay").forEach(function (modal) {
    modal.addEventListener("click", function (e) {
      if (e.target === modal) {
        modal.classList.remove("active");
      }
    });
  });

  // Simple HTML Escaper
  function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.innerText = String(text);
    return div.innerHTML;
  }
});


// Global Operator Console Clipboard & Feedback Helper
window.copyToClipboard = function (text, btnElement) {
  if (!text) return;
  navigator.clipboard.writeText(text).then(function () {
    if (btnElement) {
      const origText = btnElement.innerHTML;
      btnElement.innerHTML = "✓ Copied!";
      btnElement.style.color = "#10b981";
      setTimeout(function () {
        btnElement.innerHTML = origText;
        btnElement.style.color = "";
      }, 1500);
    }
  }).catch(function (err) {
    console.error("Clipboard copy failed: ", err);
  });
};
