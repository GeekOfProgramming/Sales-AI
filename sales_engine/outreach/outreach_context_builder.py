import re
from typing import List, Dict, Any, Optional, Tuple, Set

from backend.schemas import EnrichedLead, ContactCandidate, StructuredJob
from sales_engine.exports.export_serializer import generate_lead_id, generate_contact_id
from sales_engine.outreach.schemas import (
    SenderProfile,
    OutreachContext,
    OutreachEvidenceItem,
)

# Standard list of non-active service indicators
NON_ACTIVE_STATUSES = {"in_development", "cta", "deprecated", "unknown", "planned", "inactive"}


class OutreachContextBuilder:
    """
    Builds a compact, trusted, deterministic generation context for outreach.
    Filters active services, extracts relevant job and company signals,
    and assigns stable evidence IDs (JOB-001, SIG-001, EVID-001, SERVICE-001, CONTACT-001).
    """

    @classmethod
    def filter_active_services(cls, website_profile: Optional[Dict[str, Any]]) -> List[str]:
        """
        Extract ONLY active services from WebsiteProfile.
        Excludes products/services marked in_development, cta, deprecated, or unknown.
        """
        if not website_profile:
            return []

        active_services: List[str] = []
        services = website_profile.get("services") or []

        for svc in services:
            if isinstance(svc, dict):
                name = svc.get("name") or svc.get("title") or svc.get("service_name")
                status = str(svc.get("status", "active")).strip().lower()
                if status not in NON_ACTIVE_STATUSES and name:
                    active_services.append(name.strip())
            elif isinstance(svc, str) and svc.strip():
                # Plain string service name is considered active unless explicitly excluded
                active_services.append(svc.strip())

        # Also check offerings if present
        offerings = website_profile.get("offerings") or []
        for off in offerings:
            if isinstance(off, dict):
                name = off.get("name") or off.get("title")
                status = str(off.get("status", "active")).strip().lower()
                if status not in NON_ACTIVE_STATUSES and name:
                    clean_name = name.strip()
                    if clean_name not in active_services:
                        active_services.append(clean_name)

        return active_services

    @classmethod
    def build_context(
        cls,
        lead: EnrichedLead,
        sender: SenderProfile,
        website_profile: Optional[Dict[str, Any]] = None,
        jobs: Optional[List[StructuredJob]] = None,
        target_contact: Optional[ContactCandidate] = None,
        language: str = "en",
        tone: str = "professional_concise",
    ) -> Tuple[Optional[OutreachContext], Optional[str]]:
        """
        Build deterministic OutreachContext for a single lead and contact.
        Returns (context, error_code).
        """
        base_lead = lead.base_lead
        lead_id = generate_lead_id(lead)

        contact = target_contact or lead.best_contact
        if not contact:
            return None, "no_best_contact"

        contact_id = generate_contact_id(contact, lead_id)

        # Contact identity fields
        recipient_name = (
            contact.full_name
            or (f"{contact.first_name or ''} {contact.last_name or ''}".strip())
            or "Hiring Leader"
        )
        recipient_email = contact.work_email or ""
        recipient_title = contact.job_title

        # Active service filtering
        active_services = cls.filter_active_services(website_profile)
        if not active_services:
            return None, "no_active_service"

        company_name = base_lead.company_name or "the company"
        company_domain = base_lead.company_domain

        evidence_items: List[OutreachEvidenceItem] = []
        source_job_urls: List[str] = []

        # 1. Contact evidence
        contact_summary = f"{recipient_name}, {recipient_title or 'Leadership'} at {company_name}"
        evidence_items.append(
            OutreachEvidenceItem(
                id="CONTACT-001",
                category="contact",
                title="Target Recipient",
                content=contact_summary,
            )
        )

        # 2. Company / Base Lead Evidence
        if company_domain:
            evidence_items.append(
                OutreachEvidenceItem(
                    id="EVID-001",
                    category="company",
                    title="Company Domain & Identity",
                    content=f"Company: {company_name}, Domain: {company_domain}",
                )
            )

        # 3. Active Services evidence
        for idx, svc_name in enumerate(active_services, start=1):
            svc_id = f"SERVICE-{idx:03d}"
            evidence_items.append(
                OutreachEvidenceItem(
                    id=svc_id,
                    category="service",
                    title=f"Active Offering: {svc_name}",
                    content=f"We provide {svc_name} as an active production capability.",
                )
            )

        # 4. Relevant Jobs & Job Signals
        lead_job_titles = set(base_lead.job_titles or [])
        lead_technologies = set(base_lead.technologies or [])

        job_idx = 1
        if jobs:
            for job in jobs:
                # Associate job if matches company name, domain, or key
                matched = False
                if base_lead.company_name and job.company_name:
                    if base_lead.company_name.lower() in job.company_name.lower():
                        matched = True
                if not matched and job.job_title in lead_job_titles:
                    matched = True

                if matched:
                    j_id = f"JOB-{job_idx:03d}"
                    job_url = getattr(job, "job_url", None) or getattr(job, "url", None)
                    if not job_url and hasattr(job, "source_metadata") and isinstance(job.source_metadata, dict):
                        job_url = job.source_metadata.get("url")
                    if job_url:
                        source_job_urls.append(str(job_url))

                    # Create compact grounded snippet
                    tech_str = ", ".join(job.technologies[:5]) if job.technologies else ""
                    summary = f"Job Opening: {job.job_title} at {job.location or 'Remote'}."
                    if tech_str:
                        summary += f" Tech requirements: {tech_str}."
                    dept = getattr(job, "department", None)
                    if dept:
                        summary += f" Department: {dept}."

                    evidence_items.append(
                        OutreachEvidenceItem(
                            id=j_id,
                            category="job",
                            title=f"Hiring Signal: {job.job_title}",
                            content=summary,
                            url=job_url,
                        )
                    )
                    job_idx += 1
                    if job_idx > 5:
                        break

        # Fallback if no raw StructuredJobs matched but base_lead has job titles
        if job_idx == 1 and base_lead.job_titles:
            for title in base_lead.job_titles[:3]:
                j_id = f"JOB-{job_idx:03d}"
                evidence_items.append(
                    OutreachEvidenceItem(
                        id=j_id,
                        category="job",
                        title=f"Job Role: {title}",
                        content=f"Open role: {title}. Technologies: {', '.join(base_lead.technologies[:4]) if base_lead.technologies else 'BIM/Revit'}.",
                    )
                )
                job_idx += 1

        # 5. Lead Signals
        sig_idx = 1
        for sig in (base_lead.signals or [])[:5]:
            sig_name = sig.get("name") or sig.get("signal_name") or sig.get("type") or "Signal"
            sig_val = sig.get("value") or sig.get("evidence") or str(sig)
            evidence_items.append(
                OutreachEvidenceItem(
                    id=f"SIG-{sig_idx:03d}",
                    category="signal",
                    title=f"Signal: {sig_name}",
                    content=str(sig_val),
                )
            )
            sig_idx += 1

        # Check for sufficient grounding: we need at least 1 job or signal
        has_job_or_sig = any(item.category in ("job", "signal") for item in evidence_items)
        if not has_job_or_sig:
            return None, "insufficient_grounding"

        context = OutreachContext(
            lead_id=lead_id,
            contact_id=contact_id,
            company_name=company_name,
            company_domain=company_domain,
            recipient_name=recipient_name,
            recipient_title=recipient_title,
            recipient_email=recipient_email,
            active_services=active_services,
            evidence_items=evidence_items,
            sender=sender,
            source_job_urls=list(set(source_job_urls)),
            language=language,
            tone=tone,
        )
        return context, None
