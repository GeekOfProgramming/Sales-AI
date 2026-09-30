import os
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from sales_engine.sending.schemas import (
    StoredDraft,
    ReviewEvent,
    DraftEditRequest,
    ApprovalStatus,
    SendStatus,
    OutreachStatus,
)
from sales_engine.sending.review_store import ReviewStore


def compute_content_fingerprint(draft: StoredDraft | Dict[str, Any]) -> str:
    """
    Computes a deterministic SHA-256 fingerprint over canonical send-relevant content.
    Includes: draft_id, revision, lead_id, contact_id, recipient_email, sender_email, subject, body.
    """
    if isinstance(draft, dict):
        d_id = str(draft.get("draft_id", ""))
        rev = int(draft.get("revision", 1))
        lead_id = str(draft.get("lead_id", ""))
        contact_id = str(draft.get("contact_id", ""))
        recipient_email = str(draft.get("recipient_email", "")).strip().lower()
        sender_email = str(draft.get("sender_email", "") or "").strip().lower()
        subject = str(draft.get("subject", "")).strip()
        body = str(draft.get("body", "")).strip()
    else:
        d_id = str(draft.draft_id)
        rev = int(draft.revision)
        lead_id = str(draft.lead_id)
        contact_id = str(draft.contact_id)
        recipient_email = str(draft.recipient_email).strip().lower()
        sender_email = str(draft.sender_email or "").strip().lower()
        subject = str(draft.subject).strip()
        body = str(draft.body).strip()

    canonical = {
        "draft_id": d_id,
        "revision": rev,
        "lead_id": lead_id,
        "contact_id": contact_id,
        "recipient_email": recipient_email,
        "sender_email": sender_email,
        "subject": subject,
        "body": body,
    }
    encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ApprovalService:
    """
    Manages draft import, human editing, explicit approval, rejection,
    and approval fingerprinting.
    Zero LLM involvement.
    """

    def __init__(self, review_store: Optional[ReviewStore] = None):
        self.store = review_store or ReviewStore()

    def import_drafts(
        self,
        drafts: List[Dict[str, Any]],
        reviewer: str = "importer",
    ) -> List[StoredDraft]:
        imported: List[StoredDraft] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for d in drafts:
            draft_id = d.get("draft_id") or f"draft:{uuid.uuid4().hex[:12]}"
            revision = int(d.get("revision", 1))

            # Safety: Do not overwrite an existing sent revision
            existing = self.store.get_draft(draft_id, revision)
            # Prepare final payload (including opt-out footer if configured) BEFORE review and fingerprinting
            raw_body = d["body"].strip()
            opt_out = d.get("opt_out_text") or os.environ.get("OUTREACH_OPT_OUT_TEXT")
            if opt_out and opt_out.strip() and opt_out.strip() not in raw_body:
                final_body = f"{raw_body}\n\n---\n{opt_out.strip()}"
            else:
                final_body = raw_body

            d_canonical = dict(d)
            d_canonical["body"] = final_body
            content_hash = compute_content_fingerprint(d_canonical)

            stored = StoredDraft(
                draft_id=draft_id,
                revision=revision,
                lead_id=d["lead_id"],
                contact_id=d["contact_id"],
                recipient_name=d["recipient_name"],
                recipient_title=d.get("recipient_title"),
                recipient_email=d["recipient_email"].strip().lower(),
                sender_email=d.get("sender_email"),
                subject=d["subject"].strip(),
                body=final_body,
                service_used=d.get("service_used", ""),
                personalization_notes=d.get("personalization_notes"),
                evidence_refs=d.get("evidence_refs") or [],
                source_job_urls=d.get("source_job_urls") or [],
                language=d.get("language", "en"),
                tone=d.get("tone", "professional_concise"),
                prompt_version=d.get("prompt_version", "outreach_v1"),
                generation_model=d.get("generation_model", "qwen2.5:1.5b"),
                approval_status="pending_review",
                send_status="not_sent",
                outreach_status="draft_ready",
                content_hash=content_hash,
                approved_content_hash=None,
                reviewer=reviewer,
                review_note=d.get("review_note"),
                created_at=d.get("created_at") or now_iso,
                updated_at=now_iso,
            )

            self.store.save_draft(stored)
            self.store.record_event(
                ReviewEvent(
                    event_id=f"rev_{uuid.uuid4().hex[:12]}",
                    draft_id=stored.draft_id,
                    revision=stored.revision,
                    action="imported",
                    previous_status=None,
                    new_status="pending_review",
                    reviewer=reviewer,
                    review_note="Imported into review queue",
                    created_at=now_iso,
                )
            )
            imported.append(stored)

        return imported

    def edit_draft(
        self,
        draft_id: str,
        edit: DraftEditRequest,
    ) -> StoredDraft:
        latest = self.store.get_draft(draft_id)
        if not latest:
            raise ValueError(f"Draft not found: {draft_id}")

        if latest.send_status == "sent":
            raise ValueError("Cannot edit a sent revision in place. A new outreach draft must be created.")

        now_iso = datetime.now(timezone.utc).isoformat()
        new_rev = latest.revision + 1

        # Apply edits
        new_subject = edit.subject.strip() if edit.subject is not None else latest.subject
        new_body = edit.body.strip() if edit.body is not None else latest.body
        new_notes = edit.personalization_notes if edit.personalization_notes is not None else latest.personalization_notes
        new_recip_name = edit.recipient_name if edit.recipient_name is not None else latest.recipient_name
        new_recip_email = edit.recipient_email.strip().lower() if edit.recipient_email is not None else latest.recipient_email

        # Phase 8 validation reuse: check placeholders and basic lengths
        for bad_placeholder in ["{{", "}}", "<NAME>", "[COMPANY]", "<COMPANY>"]:
            if bad_placeholder in new_subject or bad_placeholder in new_body:
                raise ValueError(f"Human edit contains unresolved template placeholder: {bad_placeholder}")

        draft_dict = latest.model_dump()
        draft_dict.update({
            "revision": new_rev,
            "subject": new_subject,
            "body": new_body,
            "personalization_notes": new_notes,
            "recipient_name": new_recip_name,
            "recipient_email": new_recip_email,
        })
        new_content_hash = compute_content_fingerprint(draft_dict)

        new_draft = StoredDraft(
            draft_id=latest.draft_id,
            revision=new_rev,
            lead_id=latest.lead_id,
            contact_id=latest.contact_id,
            recipient_name=new_recip_name,
            recipient_title=latest.recipient_title,
            recipient_email=new_recip_email,
            sender_email=latest.sender_email,
            subject=new_subject,
            body=new_body,
            service_used=latest.service_used,
            personalization_notes=new_notes,
            evidence_refs=latest.evidence_refs,
            source_job_urls=latest.source_job_urls,
            language=latest.language,
            tone=latest.tone,
            prompt_version=latest.prompt_version,
            generation_model=latest.generation_model,
            approval_status="pending_review",
            send_status="not_sent",
            outreach_status="draft_ready",
            content_hash=new_content_hash,
            approved_content_hash=None,
            reviewer=edit.reviewer,
            review_note=edit.note,
            created_at=latest.created_at,
            updated_at=now_iso,
        )

        self.store.save_draft(new_draft)
        self.store.record_event(
            ReviewEvent(
                event_id=f"rev_{uuid.uuid4().hex[:12]}",
                draft_id=new_draft.draft_id,
                revision=new_draft.revision,
                action="edited",
                previous_status=latest.approval_status,
                new_status="pending_review",
                reviewer=edit.reviewer,
                review_note=edit.note or f"Edited from revision {latest.revision}",
                created_at=now_iso,
            )
        )
        return new_draft

    def approve_draft(
        self,
        draft_id: str,
        revision: int,
        reviewer: str,
        note: Optional[str] = None,
    ) -> StoredDraft:
        draft = self.store.get_draft(draft_id, revision)
        if not draft:
            raise ValueError(f"Draft {draft_id} revision {revision} not found")

        if draft.send_status == "sent":
            raise ValueError("Draft has already been sent; cannot re-approve")

        now_iso = datetime.now(timezone.utc).isoformat()
        # Compute exact content fingerprint at moment of approval
        approved_hash = compute_content_fingerprint(draft)

        updated = self.store.update_draft_status(
            draft_id=draft_id,
            revision=revision,
            approval_status="approved",
            outreach_status="approved",
            approved_content_hash=approved_hash,
            approved_at=now_iso,
            reviewer=reviewer,
            review_note=note,
        )
        if not updated:
            raise ValueError("Failed to update approval status")

        self.store.record_event(
            ReviewEvent(
                event_id=f"rev_{uuid.uuid4().hex[:12]}",
                draft_id=draft_id,
                revision=revision,
                action="approved",
                previous_status=draft.approval_status,
                new_status="approved",
                reviewer=reviewer,
                review_note=note or "Approved for outreach",
                created_at=now_iso,
            )
        )
        # CRITICAL INVARIANT: Approval MUST NOT invoke sending or network transmission!
        return updated

    def reject_draft(
        self,
        draft_id: str,
        revision: int,
        reviewer: str,
        note: Optional[str] = None,
    ) -> StoredDraft:
        draft = self.store.get_draft(draft_id, revision)
        if not draft:
            raise ValueError(f"Draft {draft_id} revision {revision} not found")

        now_iso = datetime.now(timezone.utc).isoformat()
        updated = self.store.update_draft_status(
            draft_id=draft_id,
            revision=revision,
            approval_status="rejected",
            send_status="not_sent",
            outreach_status="draft_ready",
            reviewer=reviewer,
            review_note=note,
        )
        if not updated:
            raise ValueError("Failed to update rejection status")

        self.store.record_event(
            ReviewEvent(
                event_id=f"rev_{uuid.uuid4().hex[:12]}",
                draft_id=draft_id,
                revision=revision,
                action="rejected",
                previous_status=draft.approval_status,
                new_status="rejected",
                reviewer=reviewer,
                review_note=note or "Rejected by human reviewer",
                created_at=now_iso,
            )
        )
        return updated

    def request_changes(
        self,
        draft_id: str,
        revision: int,
        reviewer: str,
        note: str,
    ) -> StoredDraft:
        draft = self.store.get_draft(draft_id, revision)
        if not draft:
            raise ValueError(f"Draft {draft_id} revision {revision} not found")

        now_iso = datetime.now(timezone.utc).isoformat()
        updated = self.store.update_draft_status(
            draft_id=draft_id,
            revision=revision,
            approval_status="changes_requested",
            send_status="not_sent",
            outreach_status="draft_ready",
            reviewer=reviewer,
            review_note=note,
        )
        if not updated:
            raise ValueError("Failed to update status")

        self.store.record_event(
            ReviewEvent(
                event_id=f"rev_{uuid.uuid4().hex[:12]}",
                draft_id=draft_id,
                revision=revision,
                action="changes_requested",
                previous_status=draft.approval_status,
                new_status="changes_requested",
                reviewer=reviewer,
                review_note=note,
                created_at=now_iso,
            )
        )
        return updated
