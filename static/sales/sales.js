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
    if (sendDetailsDiv) {
      sendDetailsDiv.innerHTML = `
        <div style="background:#111827; padding:0.85rem; border-radius:6px; margin:0.75rem 0; font-size:0.875rem;">
          <p><strong>From Name:</strong> ${escapeHtml(fromName || 'pyBIM Team')}</p>
          <p><strong>From Email:</strong> <code>${escapeHtml(fromEmail || 'sales@pybim.it')}</code></p>
          <p><strong>To:</strong> <code>${escapeHtml(recipient)}</code></p>
          <p><strong>Company:</strong> ${escapeHtml(company || 'N/A')}</p>
          <p><strong>Subject:</strong> ${escapeHtml(subject)}</p>
          <p><strong>Revision:</strong> r${escapeHtml(revision)}</p>
          <p style="margin-top:0.5rem;"><strong>Full Body:</strong></p>
          <pre style="background:#0b0f19; padding:0.6rem; border-radius:4px; max-height:160px; overflow-y:auto; white-space:pre-wrap; font-family:monospace; font-size:0.8rem; color:#cbd5e1; border:1px solid #1e293b;">${escapeHtml(fullBody || '')}</pre>
        </div>
        <p style="color:#ef4444; font-weight:700; margin-top:0.75rem;">
          ⚠️ This will send one real email.
        </p>
      `;
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
