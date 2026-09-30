import re
from typing import List, Dict, Any, Optional, Tuple, Set

from backend.schemas import EnrichedLead, ContactCandidate, StructuredJob
from sales_engine.exports.export_serializer import generate_lead_id, generate_contact_id
from sales_engine.outreach.schemas import (
    SenderProfile,
    OutreachContext,
    OutreachEvidenceItem,
)

from sales_engine.analysis.company_normalizer import CompanyNormalizer

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
        For offerings dicts, requires status explicitly == "active".
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
                # Plain string service name is considered active per Phase 2 contract
                active_services.append(svc.strip())

        # Also check offerings if present (requires status explicitly == "active")
        offerings = website_profile.get("offerings") or []
        for off in offerings:
            if isinstance(off, dict):
                name = off.get("name") or off.get("title")
                status = str(off.get("status", "")).strip().lower()
                if status == "active" and name:
                    clean_name = name.strip()
                    if clean_name not in active_services:
                        active_services.append(clean_name)

        return active_services

    @classmethod
    def _matches_company_identity(cls, base_lead: Any, job: StructuredJob) -> bool:
        """
        Strictly verify that job belongs to the target company using company identity priority:
        1. Canonical company_domain
        2. Namespaced source identity (source:source_company_key) against base_lead.source_company_identities
        3. Exact normalized company name
        NEVER match on job_title alone.
        """
        # 1. Canonical domain match
        lead_domain = CompanyNormalizer.canonicalize_domain(base_lead.company_domain or "")
        job_domain = CompanyNormalizer.canonicalize_domain(job.company_domain or "")
        if lead_domain and job_domain:
            return lead_domain == job_domain

        # 2. Namespaced source identity match
        lead_identities = set(getattr(base_lead, "source_company_identities", []) or [])
        job_src = getattr(job, "source", None)
        job_key = getattr(job, "source_company_key", None)
        if job_src and job_key:
            job_ident = f"{job_src}:{job_key}".lower()
            if any(str(i).lower() == job_ident for i in lead_identities):
                return True
            # If lead has explicit namespaced identities and job has a source identity that does NOT match,
            # do not fall back to company name matching
            if lead_identities:
                return False

        # 3. Exact normalized company name fallback (only if neither has conflicting source identity)
        if not lead_identities:
            lead_name = getattr(base_lead, "company_name_normalized", None) or CompanyNormalizer.normalize(base_lead.company_name or "")
            job_name = getattr(job, "company_name_normalized", None) or CompanyNormalizer.normalize(job.company_name or "")
            if lead_name and job_name:
                return lead_name == job_name

        return False

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

        # P8-ID-005: If explicit target_contact is provided, verify it belongs to this lead
        if target_contact:
            lead_contact_emails = {c.work_email for c in (lead.contacts or []) if c.work_email}
            lead_contact_names = {c.full_name for c in (lead.contacts or []) if c.full_name}
            best_email = lead.best_contact.work_email if lead.best_contact else None
            best_name = lead.best_contact.full_name if lead.best_contact else None
            if best_email:
                lead_contact_emails.add(best_email)
            if best_name:
                lead_contact_names.add(best_name)

            matches_email = target_contact.work_email and target_contact.work_email in lead_contact_emails
            matches_name = target_contact.full_name and target_contact.full_name in lead_contact_names
            if not (matches_email or matches_name):
                return None, "invalid_contact_for_lead"

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
        source_job_urls_set: Set[str] = set()

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

        # 2. Company Identity Evidence
        if company_domain:
            evidence_items.append(
                OutreachEvidenceItem(
                    id="EVID-001",
                    category="company",
                    title="Company Domain & Identity",
                    content=f"Company: {company_name}, Domain: {company_domain}",
                )
            )

        # 3. Upstream Phase 5 Grounded Evidence (base_lead.evidence)
        evid_idx = 2
        for ev_str in (base_lead.evidence or []):
            if ev_str and str(ev_str).strip():
                clean_ev = str(ev_str).strip()
                evidence_items.append(
                    OutreachEvidenceItem(
                        id=f"EVID-{evid_idx:03d}",
                        category="evidence",
                        title="Verified Upstream Evidence",
                        content=clean_ev,
                    )
                )
                evid_idx += 1

        # 4. Active Services evidence
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

        # 5. Relevant Jobs & Job Signals
        matched_jobs: List[StructuredJob] = []
        lead_job_titles = {t.lower().strip() for t in (base_lead.job_titles or []) if t}
        if jobs:
            for job in jobs:
                # Strictly match company identity FIRST
                if cls._matches_company_identity(base_lead, job):
                    # Check relevance: if lead has specific job titles or signals, verify relevance
                    is_rel = getattr(job, "is_relevant", None)
                    if is_rel is False:
                        continue
                    
                    # If job title exists and lead specifies titles, check if title or technologies align
                    title = (job.job_title or "").lower().strip()
                    if lead_job_titles and title:
                        # Exclude clearly non-technical/unrelated roles like receptionist/accountant if lead is BIM
                        if not any(lt in title or title in lt for lt in lead_job_titles):
                            if not (job.relevant_signals or (is_rel is True)):
                                continue

                    matched_jobs.append(job)

        # Deterministically sort matched jobs by URL/title/location
        matched_jobs.sort(
            key=lambda j: (
                str(getattr(j, "job_url", "") or ""),
                str(getattr(j, "job_title", "") or ""),
                str(getattr(j, "location", "") or ""),
            )
        )

        job_idx = 1
        for job in matched_jobs[:5]:
            j_id = f"JOB-{job_idx:03d}"
            job_url = getattr(job, "job_url", None) or getattr(job, "url", None)
            if not job_url and hasattr(job, "source_metadata") and isinstance(job.source_metadata, dict):
                job_url = job.source_metadata.get("url")
            if job_url:
                source_job_urls_set.add(str(job_url))

            # Create compact grounded snippet without fabrication
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
                    url=str(job_url) if job_url else None,
                )
            )

            # Also include structured job signals if present
            for rel_sig in (getattr(job, "relevant_signals", []) or []):
                sig_val = None
                evid_val = None
                if isinstance(rel_sig, dict):
                    sig_val = rel_sig.get("signal")
                    evid_val = rel_sig.get("evidence")
                else:
                    sig_val = getattr(rel_sig, "signal", None)
                    evid_val = getattr(rel_sig, "evidence", None)

                if sig_val:
                    evidence_items.append(
                        OutreachEvidenceItem(
                            id=f"SIG-JOB-{job_idx:03d}",
                            category="signal",
                            title=f"Job Signal: {job.job_title}",
                            content=str(sig_val),
                            url=str(job_url) if job_url else None,
                        )
                    )
                if evid_val:
                    evidence_items.append(
                        OutreachEvidenceItem(
                            id=f"EVID-JOB-{job_idx:03d}",
                            category="job_evidence",
                            title=f"Job Evidence: {job.job_title}",
                            content=str(evid_val),
                            url=str(job_url) if job_url else None,
                        )
                    )

            job_idx += 1

        # Fallback if no raw StructuredJobs matched but base_lead has job titles
        # (NO FABRICATED "Technologies: BIM/Revit")
        if job_idx == 1 and base_lead.job_titles:
            for title in base_lead.job_titles[:3]:
                sig_id = f"SIG-ROLE-{job_idx:03d}"
                content = f"Aggregated relevant hiring role: {title}."
                if base_lead.technologies:
                    content += f" Associated technologies: {', '.join(base_lead.technologies[:4])}."
                evidence_items.append(
                    OutreachEvidenceItem(
                        id=sig_id,
                        category="signal",
                        title=f"Hiring Role Signal: {title}",
                        content=content,
                    )
                )
                job_idx += 1

        # 6. Lead Signals (Support Phase 5 signal schema)
        sig_idx = 1
        for sig in (base_lead.signals or [])[:5]:
            if isinstance(sig, dict):
                sig_name = (
                    sig.get("signal")
                    or sig.get("name")
                    or sig.get("signal_name")
                    or sig.get("type")
                    or "Signal"
                )
                sig_val = (
                    sig.get("value")
                    or sig.get("evidence")
                    or f"Detected signal: {sig_name}"
                )
            else:
                sig_name = "Signal"
                sig_val = str(sig)

            evidence_items.append(
                OutreachEvidenceItem(
                    id=f"SIG-{sig_idx:03d}",
                    category="signal",
                    title=f"Signal: {sig_name}",
                    content=str(sig_val),
                )
            )
            sig_idx += 1

        # Check for sufficient grounding: need at least 1 job, signal, or verified evidence
        has_grounding = any(item.category in ("job", "signal", "evidence") for item in evidence_items)
        if not has_grounding:
            return None, "insufficient_grounding"

        # Deterministic sorting of source job urls
        sorted_job_urls = sorted(source_job_urls_set)

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
            source_job_urls=sorted_job_urls,
            language=language,
            tone=tone,
        )
        return context, None
