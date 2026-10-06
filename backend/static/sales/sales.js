/**
 * SalesAI Phase 10.5 Operator Console client script.
 * Zero secrets, zero direct writes, modal safety confirmations.
 */

document.addEventListener("DOMContentLoaded", function () {
  // Setup Send Confirmation Modal
  const sendModal = document.getElementById("send-confirm-modal");
  const sendConfirmForm = document.getElementById("send-confirm-form");
  const sendDraftIdInput = document.getElementById("modal-send-draft-id");
  const sendRevisionInput = document.getElementById("modal-send-revision");
  const sendDetailsDiv = document.getElementById("modal-send-details");

  window.openSendConfirmation = function (draftId, revision, recipient, subject, company) {
    if (!sendModal) return;
    if (sendDraftIdInput) sendDraftIdInput.value = draftId;
    if (sendRevisionInput) sendRevisionInput.value = revision;
    if (sendConfirmForm) sendConfirmForm.action = `/sales/drafts/${encodeURIComponent(draftId)}/send`;
    if (sendDetailsDiv) {
      sendDetailsDiv.innerHTML = `
        <div style="background:#111827; padding:0.85rem; border-radius:6px; margin:0.75rem 0; font-size:0.875rem;">
          <p><strong>Company:</strong> ${escapeHtml(company || 'N/A')}</p>
          <p><strong>Recipient:</strong> ${escapeHtml(recipient)}</p>
          <p><strong>Subject:</strong> ${escapeHtml(subject)}</p>
          <p><strong>Revision:</strong> r${escapeHtml(revision)}</p>
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
