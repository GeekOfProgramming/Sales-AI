import logging
import copy
from typing import List, Dict, Any, Optional

from backend.schemas import EnrichedLead, ContactCandidate, StructuredJob
from sales_engine.exports.export_serializer import generate_lead_id, generate_contact_id
from sales_engine.outreach.schemas import (
    SenderProfile,
    EmailDraft,
    GenerateDraftsRequest,
    GenerateDraftsResponse,
    SkippedLeadRecord,
    GenerationErrorRecord,
)
from sales_engine.outreach.outreach_context_builder import OutreachContextBuilder
from sales_engine.outreach.email_generator import EmailGenerator

logger = logging.getLogger(__name__)

USABLE_EMAIL_STATUSES = {"verified", "likely"}
VALID_TONES = {"professional_concise", "technical_consultative", "executive_brief"}
VALID_LANGUAGES = {"en", "it", "de"}
MAX_DRAFTS_LIMIT = 50


class OutreachOrchestrator:
    """
    Coordinates cold email draft generation for qualified leads.
    Enforces eligibility, contact preservation, active service gating,
    and batch failure isolation.
    """

    def __init__(self, email_generator: Optional[EmailGenerator] = None):
        self.generator = email_generator or EmailGenerator()

    def generate_drafts(
        self,
        request: GenerateDraftsRequest,
        jobs: Optional[List[StructuredJob]] = None,
    ) -> GenerateDraftsResponse:
        """
        Batch generate email drafts according to Phase 8 specification.
        """
        response = GenerateDraftsResponse()
        active_jobs = jobs if jobs is not None else getattr(request, "jobs", [])

        # Tone and Language validation
        language = request.language if request.language in VALID_LANGUAGES else "en"
        tone = request.tone if request.tone in VALID_TONES else "professional_concise"

        leads_to_process = request.leads[: min(request.max_drafts, MAX_DRAFTS_LIMIT)]

        for lead in leads_to_process:
            base_lead = lead.base_lead
            lead_id = generate_lead_id(lead)
            comp_name = base_lead.company_name

            # 1. Eligibility Check: Qualified
            if not getattr(base_lead, "qualified", False):
                response.skipped.append(
                    SkippedLeadRecord(
                        lead_id=lead_id,
                        company_name=comp_name,
                        reason="not_qualified",
                        details="Lead is not marked as qualified (threshold=60).",
                    )
                )
                continue

            # 2. Eligibility Check: Best Contact
            best_contact = lead.best_contact
            if not best_contact:
                response.skipped.append(
                    SkippedLeadRecord(
                        lead_id=lead_id,
                        company_name=comp_name,
                        reason="no_best_contact",
                        details="No best contact identified for qualified lead.",
                    )
                )
                continue

            contact_id = generate_contact_id(best_contact, lead_id)

            # 3. Eligibility Check: Work Email & Usability
            work_email = getattr(best_contact, "work_email", None)
            if not work_email or not str(work_email).strip():
                response.skipped.append(
                    SkippedLeadRecord(
                        lead_id=lead_id,
                        company_name=comp_name,
                        reason="no_work_email",
                        details=f"Best contact {best_contact.full_name or ''} has no work email.",
                    )
                )
                continue

            email_status = (getattr(best_contact, "email_status", None) or "unknown").lower()
            if request.require_usable_email and email_status not in USABLE_EMAIL_STATUSES:
                response.skipped.append(
                    SkippedLeadRecord(
                        lead_id=lead_id,
                        company_name=comp_name,
                        reason="email_not_usable",
                        details=f"Email status '{email_status}' is not verified or likely.",
                    )
                )
                continue

            # 4. Deterministic Context Building
            context, build_err = OutreachContextBuilder.build_context(
                lead=lead,
                sender=request.sender_profile,
                website_profile=request.website_profile,
                jobs=active_jobs,
                target_contact=best_contact,
                language=language,
                tone=tone,
            )

            if not context:
                if build_err in ("no_active_service", "insufficient_grounding", "no_best_contact"):
                    response.skipped.append(
                        SkippedLeadRecord(
                            lead_id=lead_id,
                            company_name=comp_name,
                            reason=build_err,
                            details=f"Context build skipped lead: {build_err}",
                        )
                    )
                else:
                    response.errors.append(
                        GenerationErrorRecord(
                            lead_id=lead_id,
                            company_name=comp_name,
                            contact_id=contact_id,
                            error_type="context_build_failed",
                            message=build_err or "Unknown context build error",
                        )
                    )
                continue

            # 5. LLM Draft Generation
            try:
                draft, err_code, err_msg = self.generator.generate_draft(context=context, revision=1)
                if draft:
                    response.drafts.append(draft)
                else:
                    response.errors.append(
                        GenerationErrorRecord(
                            lead_id=lead_id,
                            company_name=comp_name,
                            contact_id=contact_id,
                            error_type=err_code or "generation_failed",
                            message=err_msg or "Failed to generate valid email draft",
                        )
                    )
            except Exception as e:
                logger.error(f"Unexpected error generating draft for lead {lead_id}: {e}")
                response.errors.append(
                    GenerationErrorRecord(
                        lead_id=lead_id,
                        company_name=comp_name,
                        contact_id=contact_id,
                        error_type="generation_failed",
                        message=str(e),
                    )
                )

        response.generated_count = len(response.drafts)
        response.skipped_count = len(response.skipped)
        response.failed_count = len(response.errors)

        return response

    @staticmethod
    def project_draft_to_lead_row(lead_row: Any, draft: EmailDraft) -> Any:
        """
        Deterministic Phase 7 handoff projection helper:
        Projects generated draft content and workflow fields into a LeadExportRow.
        Invariants:
        - approval_status = 'pending_review'
        - outreach_status = 'draft_ready'
        - send_status = 'not_sent'
        - lead_id, scoring, and best_contact remain untouched
        """
        updated = lead_row.model_copy(deep=True) if hasattr(lead_row, "model_copy") else copy.deepcopy(lead_row)
        if hasattr(updated, "draft_subject"):
            updated.draft_subject = draft.subject
            updated.draft_body = draft.body
            updated.personalization_notes = draft.personalization_notes or ""
            updated.approval_status = "pending_review"
            updated.outreach_status = "draft_ready"
            updated.send_status = "not_sent"
        elif isinstance(updated, dict):
            updated["draft_subject"] = draft.subject
            updated["draft_body"] = draft.body
            updated["personalization_notes"] = draft.personalization_notes or ""
            updated["approval_status"] = "pending_review"
            updated["outreach_status"] = "draft_ready"
            updated["send_status"] = "not_sent"
        return updated
