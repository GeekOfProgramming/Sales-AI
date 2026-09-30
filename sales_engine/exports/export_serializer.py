import re
import urllib.parse
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple, Set

from backend.schemas import CompanyLead, EnrichedLead, ContactCandidate, StructuredJob
from sales_engine.analysis.company_normalizer import CompanyNormalizer
from sales_engine.exports.schemas import (
    LeadExportRow,
    ContactExportRow,
    JobExportRow,
    ErrorAuditRow,
    ExportSummary,
)

# Forbidden sensitive/privacy keys across any export dictionaries
SENSITIVE_FIELD_NAMES = {
    "personal_email", "personal_phone", "mobile_phone", "cell_phone",
    "home_phone", "phone", "api_key", "secret", "token", "password", "credentials"
}

def clean_scalar_list(items: Optional[List[Any]]) -> str:
    """Format a list of strings/items deterministically joined with ' | '."""
    if not items:
        return ""
    cleaned = []
    for item in items:
        if item is None:
            continue
        s = str(item).strip()
        if s:
            cleaned.append(s)
    return " | ".join(cleaned)

def clean_linkedin_url(url: Optional[str]) -> Optional[str]:
    """Canonicalize LinkedIn URL for stable ID calculation."""
    if not url:
        return None
    url_clean = url.strip().rstrip("/")
    # Remove tracking query parameters
    parsed = urllib.parse.urlparse(url_clean)
    path = parsed.path.rstrip("/")
    if "linkedin.com" in parsed.netloc.lower():
        return f"linkedin.com{path}".lower()
    return url_clean.lower()

def generate_lead_id(lead_obj: Any) -> str:
    """
    Generate deterministic lead_id.
    Priority:
    1. Canonical company domain (e.g. domain:acme.com)
    2. ATS source + source_company_key (e.g. source_key:lever:acme-co)
    3. Normalized company name (e.g. name:acme_corporation)
    4. Deterministic fallback
    """
    # Extract fields from dict or model
    base = lead_obj.base_lead if hasattr(lead_obj, "base_lead") else lead_obj
    
    domain = getattr(base, "company_domain", None)
    if not domain and isinstance(base, dict):
        domain = base.get("company_domain")
        
    if domain:
        canonical_domain = CompanyNormalizer.canonicalize_domain(domain)
        if canonical_domain:
            return f"domain:{canonical_domain}"
            
    source_keys = getattr(base, "source_company_keys", None)
    if not source_keys and isinstance(base, dict):
        source_keys = base.get("source_company_keys", [])
    if not source_keys:
        single_key = getattr(base, "source_company_key", None)
        if not single_key and isinstance(base, dict):
            single_key = base.get("source_company_key")
        if single_key:
            source_keys = [single_key]
            
    if source_keys and len(source_keys) > 0 and source_keys[0]:
        key = str(source_keys[0]).strip().lower()
        return f"source_key:{key}"
        
    name_norm = getattr(base, "company_name_normalized", None)
    if not name_norm and isinstance(base, dict):
        name_norm = base.get("company_name_normalized")
        
    if not name_norm:
        raw_name = getattr(base, "company_name", None)
        if not raw_name and isinstance(base, dict):
            raw_name = base.get("company_name")
        if raw_name:
            name_norm = CompanyNormalizer.normalize(raw_name)
            
    if name_norm:
        slug = re.sub(r"[^a-z0-9]+", "_", name_norm.lower()).strip("_")
        return f"name:{slug}"
        
    return "lead:unknown"

def generate_contact_id(contact_obj: Any, lead_id: str) -> str:
    """
    Generate deterministic contact_id.
    Priority:
    1. Normalized work email (e.g. email:jane@acme.com)
    2. Canonical LinkedIn URL (e.g. linkedin:in/jane-smith)
    3. Provider + provider_person_id (e.g. apollo:person_123)
    4. Deterministic record-local fallback
    """
    email = getattr(contact_obj, "work_email", None)
    if not email and isinstance(contact_obj, dict):
        email = contact_obj.get("work_email")
    if email and str(email).strip():
        norm_email = str(email).strip().lower()
        return f"email:{norm_email}"
        
    linkedin = getattr(contact_obj, "linkedin_url", None)
    if not linkedin and isinstance(contact_obj, dict):
        linkedin = contact_obj.get("linkedin_url")
    if linkedin and str(linkedin).strip():
        canon_li = clean_linkedin_url(str(linkedin))
        if canon_li:
            return f"linkedin:{canon_li}"
            
    provider = getattr(contact_obj, "provider", None)
    if not provider and isinstance(contact_obj, dict):
        provider = contact_obj.get("provider")
    person_id = getattr(contact_obj, "provider_person_id", None)
    if not person_id and isinstance(contact_obj, dict):
        person_id = contact_obj.get("provider_person_id")
    if provider and person_id:
        return f"{str(provider).lower()}:{str(person_id).strip()}"
        
    name = getattr(contact_obj, "full_name", None)
    if not name and isinstance(contact_obj, dict):
        name = contact_obj.get("full_name")
    title = getattr(contact_obj, "job_title", None)
    if not title and isinstance(contact_obj, dict):
        title = contact_obj.get("job_title")
        
    name_slug = re.sub(r"[^a-z0-9]+", "_", str(name or "unknown").lower()).strip("_")
    title_slug = re.sub(r"[^a-z0-9]+", "_", str(title or "unknown").lower()).strip("_")
    return f"fallback:{lead_id}:{name_slug}:{title_slug}"

class ExportSerializer:
    """Canonical serializer producing validated export rows and CRM-ready payloads."""

    def __init__(self, export_run_id: Optional[str] = None):
        self.export_run_id = export_run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self.current_time_iso = datetime.now(timezone.utc).isoformat()

    def serialize_lead(self, enriched: Any, lead_id: str, best_contact_id: Optional[str]) -> LeadExportRow:
        base = getattr(enriched, "base_lead", enriched)
        company_enrich = getattr(enriched, "company_enrichment", None)
        best_cont = getattr(enriched, "best_contact", None)

        def g(obj: Any, attr: str, default: Any = None) -> Any:
            if obj is None:
                return default
            if hasattr(obj, attr):
                val = getattr(obj, attr)
                return val if val is not None else default
            if isinstance(obj, dict):
                val = obj.get(attr)
                return val if val is not None else default
            return default

        # Scoring & breakdown
        lead_score = int(g(base, "lead_score", 0))
        qualified = bool(g(base, "qualified", False))
        fit_score = int(g(base, "fit_score", 0))
        intent_score = int(g(base, "intent_score", 0))
        recency_score = int(g(base, "recency_score", 0))
        evidence_score = int(g(base, "evidence_score", 0))
        total_jobs = int(g(base, "total_job_count", g(base, "job_count", 0)))
        relevant_jobs = int(g(base, "relevant_job_count", 0))

        # Reasons & evidence preservation
        scoring_reasons = g(base, "scoring_reasons", [])
        evidence_list = g(base, "evidence", [])
        lead_reasons_str = clean_scalar_list(scoring_reasons)
        lead_evidence_str = clean_scalar_list(evidence_list)

        # Company enrichment fields
        industry = g(company_enrich, "industry", "")
        country = g(company_enrich, "country", "")
        employee_count = g(company_enrich, "employee_count", None)

        # Status & metadata
        enrichment_status = g(enriched, "enrichment_status", "pending")
        providers_used = clean_scalar_list(g(enriched, "providers_used", []))
        enrichment_errors = clean_scalar_list(g(enriched, "enrichment_errors", []))

        # Best contact flattened
        bc_name = g(best_cont, "full_name", "")
        bc_title = g(best_cont, "job_title", "")
        bc_email = g(best_cont, "work_email", "")
        bc_status = g(best_cont, "email_status", "")
        bc_conf = g(best_cont, "email_confidence", None)
        bc_li = g(best_cont, "linkedin_url", "")
        bc_score = g(best_cont, "contact_score", None)
        bc_buyer_role = g(best_cont, "buyer_role_match", "")

        return LeadExportRow(
            export_run_id=self.export_run_id,
            lead_id=lead_id,
            company_name=g(base, "company_name", ""),
            company_domain=g(base, "company_domain", ""),
            industry=industry,
            country=country,
            employee_count=employee_count,
            lead_score=lead_score,
            qualified=qualified,
            fit_score=fit_score,
            intent_score=intent_score,
            recency_score=recency_score,
            evidence_score=evidence_score,
            qualification_threshold=70,
            total_job_count=total_jobs,
            relevant_job_count=relevant_jobs,
            lead_reasons=lead_reasons_str,
            lead_evidence=lead_evidence_str,
            enrichment_status=enrichment_status,
            providers_used=providers_used,
            enrichment_errors=enrichment_errors,
            best_contact_id=best_contact_id or "",
            best_contact_name=bc_name,
            best_contact_title=bc_title,
            best_contact_email=bc_email,
            best_contact_email_status=bc_status,
            best_contact_email_confidence=bc_conf,
            best_contact_linkedin=bc_li,
            best_contact_score=bc_score,
            buyer_role_match=bc_buyer_role,
            outreach_status="not_started",
            approval_status="pending_review",
            send_status="not_sent",
            draft_subject="",
            draft_body="",
            personalization_notes="",
            last_outreach_at="",
            owner="",
            notes="",
            created_at=self.current_time_iso,
            updated_at=self.current_time_iso
        )

    def serialize_contacts(self, enriched: Any, lead_id: str, best_contact_id: Optional[str]) -> List[ContactExportRow]:
        contacts_raw = getattr(enriched, "contacts", [])
        if not contacts_raw and isinstance(enriched, dict):
            contacts_raw = enriched.get("contacts", [])

        base = getattr(enriched, "base_lead", enriched)
        company_name = getattr(base, "company_name", "") if hasattr(base, "company_name") else (base.get("company_name", "") if isinstance(base, dict) else "")
        company_domain = getattr(base, "company_domain", "") if hasattr(base, "company_domain") else (base.get("company_domain", "") if isinstance(base, dict) else "")

        rows = []
        seen_contact_ids: Set[str] = set()

        for idx, cont in enumerate(contacts_raw):
            c_id = generate_contact_id(cont, lead_id)
            if c_id in seen_contact_ids:
                # Deduplicate within same lead
                continue
            seen_contact_ids.add(c_id)

            def g(attr: str, default: Any = None) -> Any:
                if hasattr(cont, attr):
                    val = getattr(cont, attr)
                    return val if val is not None else default
                if isinstance(cont, dict):
                    val = cont.get(attr)
                    return val if val is not None else default
                return default

            is_best = (c_id == best_contact_id) or bool(g("is_best_contact", False)) or (idx == 0 and best_contact_id == c_id)

            rows.append(ContactExportRow(
                lead_id=lead_id,
                contact_id=c_id,
                company_name=company_name,
                company_domain=company_domain,
                first_name=g("first_name", ""),
                last_name=g("last_name", ""),
                full_name=g("full_name", ""),
                job_title=g("job_title", ""),
                seniority=g("seniority", ""),
                department=g("department", ""),
                work_email=g("work_email", ""),
                email_status=g("email_status", "unknown"),
                email_confidence=int(g("email_confidence", 0) or 0),
                linkedin_url=g("linkedin_url", ""),
                buyer_role_match=g("buyer_role_match", ""),
                contact_score=int(g("contact_score", 0) or 0),
                data_sources=clean_scalar_list(g("data_sources", [])),
                provider_person_id=g("provider_person_id", ""),
                is_best_contact=is_best,
                contact_rank=idx + 1
            ))

        return rows

    def serialize_jobs(self, enriched: Any, lead_id: str, raw_jobs: Optional[List[Any]] = None) -> List[JobExportRow]:
        base = getattr(enriched, "base_lead", enriched)
        company_name = getattr(base, "company_name", "") if hasattr(base, "company_name") else (base.get("company_name", "") if isinstance(base, dict) else "")
        company_domain = getattr(base, "company_domain", "") if hasattr(base, "company_domain") else (base.get("company_domain", "") if isinstance(base, dict) else "")

        jobs_list = raw_jobs or getattr(enriched, "raw_jobs", None) or getattr(base, "raw_jobs", None) or []
        if isinstance(jobs_list, dict):
            jobs_list = [jobs_list]

        rows = []
        if jobs_list:
            for job in jobs_list:
                def gj(attr: str, default: Any = None) -> Any:
                    if hasattr(job, attr):
                        val = getattr(job, attr)
                        return val if val is not None else default
                    if isinstance(job, dict):
                        val = job.get(attr)
                        return val if val is not None else default
                    return default

                techs = gj("technologies", [])
                signals = gj("relevant_signals", [])
                sig_names = []
                sig_evs = []
                for s in signals:
                    if hasattr(s, "signal"):
                        sig_names.append(s.signal)
                        if getattr(s, "evidence", None):
                            sig_evs.append(s.evidence)
                    elif isinstance(s, dict):
                        sig_names.append(s.get("signal", ""))
                        if s.get("evidence"):
                            sig_evs.append(s.get("evidence"))

                rows.append(JobExportRow(
                    lead_id=lead_id,
                    company_name=gj("company_name", company_name),
                    company_domain=gj("company_domain", company_domain),
                    job_title=gj("job_title", ""),
                    job_url=gj("job_url", ""),
                    source=gj("source", "generic"),
                    source_company_key=gj("source_company_key", ""),
                    location=gj("location", ""),
                    employment_type=gj("employment_type", ""),
                    posted_date=gj("posted_date", ""),
                    seniority=gj("seniority", ""),
                    remote_status=gj("remote_status", ""),
                    technologies=clean_scalar_list(techs),
                    relevant_signals=clean_scalar_list(sig_names),
                    signal_evidence=clean_scalar_list(sig_evs),
                    is_relevant=bool(sig_names or gj("is_relevant", True))
                ))
        else:
            # Audit fallback from aggregated CompanyLead when individual job objects were omitted
            job_titles = getattr(base, "job_titles", []) if hasattr(base, "job_titles") else (base.get("job_titles", []) if isinstance(base, dict) else [])
            techs = getattr(base, "technologies", []) if hasattr(base, "technologies") else (base.get("technologies", []) if isinstance(base, dict) else [])
            evidences = getattr(base, "evidence", []) if hasattr(base, "evidence") else (base.get("evidence", []) if isinstance(base, dict) else [])
            signals = getattr(base, "signals", []) if hasattr(base, "signals") else (base.get("signals", []) if isinstance(base, dict) else [])
            sig_strs = [f"{s.get('signal')}:{s.get('evidence_count')}" if isinstance(s, dict) else str(s) for s in signals]

            for title in (job_titles or ["General Job Posting"]):
                rows.append(JobExportRow(
                    lead_id=lead_id,
                    company_name=company_name,
                    company_domain=company_domain,
                    job_title=title,
                    job_url="",
                    source="generic",
                    source_company_key="",
                    location=clean_scalar_list(getattr(base, "locations", []) if hasattr(base, "locations") else []),
                    employment_type="",
                    posted_date=getattr(base, "newest_job_date", "") if hasattr(base, "newest_job_date") else "",
                    seniority="",
                    remote_status="",
                    technologies=clean_scalar_list(techs),
                    relevant_signals=clean_scalar_list(sig_strs),
                    signal_evidence=clean_scalar_list(evidences),
                    is_relevant=True
                ))
        return rows

    def serialize_errors(self, enriched: Any, lead_id: str) -> List[ErrorAuditRow]:
        errors = getattr(enriched, "enrichment_errors", [])
        if not errors and isinstance(enriched, dict):
            errors = enriched.get("enrichment_errors", [])

        base = getattr(enriched, "base_lead", enriched)
        company_name = getattr(base, "company_name", "") if hasattr(base, "company_name") else (base.get("company_name", "") if isinstance(base, dict) else "")

        rows = []
        for err in errors:
            msg = str(err)
            provider = "system"
            err_type = "provider_error"
            if "apollo" in msg.lower():
                provider = "apollo"
            elif "hunter" in msg.lower():
                provider = "hunter"

            rows.append(ErrorAuditRow(
                export_run_id=self.export_run_id,
                lead_id=lead_id,
                company_name=company_name,
                stage="enrichment",
                error_type=err_type,
                provider=provider,
                message=msg,
                source_reference="",
                created_at=self.current_time_iso
            ))
        return rows

    def calculate_summary(
        self,
        lead_rows: List[LeadExportRow],
        contact_rows: List[ContactExportRow],
        error_rows: List[ErrorAuditRow]
    ) -> ExportSummary:
        total = len(lead_rows)
        qualified = sum(1 for r in lead_rows if r.qualified)
        unqualified = total - qualified
        complete = sum(1 for r in lead_rows if r.enrichment_status == "complete")
        partial = sum(1 for r in lead_rows if r.enrichment_status == "partial")
        errors = len(error_rows)

        with_best = sum(1 for r in lead_rows if r.best_contact_email or r.best_contact_name)
        with_verified = sum(1 for r in lead_rows if r.best_contact_email_status == "verified")
        with_likely = sum(1 for r in lead_rows if r.best_contact_email_status == "likely")
        without_work = sum(1 for r in lead_rows if not r.best_contact_email)

        scores = [r.lead_score for r in lead_rows]
        avg_score = round(sum(scores) / total, 2) if total > 0 else 0.0

        return ExportSummary(
            total_leads=total,
            qualified_leads=qualified,
            unqualified_leads=unqualified,
            complete_enrichment=complete,
            partial_enrichment=partial,
            provider_errors=errors,
            leads_with_best_contact=with_best,
            leads_with_verified_email=with_verified,
            leads_with_likely_email=with_likely,
            leads_without_work_email=without_work,
            average_lead_score=avg_score,
            export_timestamp=self.current_time_iso
        )

    def to_crm_payload(
        self,
        lead_row: LeadExportRow,
        contact_rows: List[ContactExportRow],
        job_rows: List[JobExportRow],
        error_rows: List[ErrorAuditRow]
    ) -> Dict[str, Any]:
        """Convert lead and related entities into canonical CRM-ready JSON object."""
        return {
            "company": {
                "name": lead_row.company_name,
                "domain": lead_row.company_domain,
                "industry": lead_row.industry,
                "country": lead_row.country,
                "employee_count": lead_row.employee_count,
            },
            "lead": {
                "export_run_id": lead_row.export_run_id,
                "lead_id": lead_row.lead_id,
                "lead_score": lead_row.lead_score,
                "qualified": lead_row.qualified,
                "fit_score": lead_row.fit_score,
                "intent_score": lead_row.intent_score,
                "recency_score": lead_row.recency_score,
                "evidence_score": lead_row.evidence_score,
                "qualification_threshold": lead_row.qualification_threshold,
                "relevant_job_count": lead_row.relevant_job_count,
                "total_job_count": lead_row.total_job_count,
                "reasons": [r for r in lead_row.lead_reasons.split(" | ") if r],
                "evidence": [e for e in lead_row.lead_evidence.split(" | ") if e],
                "enrichment_status": lead_row.enrichment_status,
                "providers_used": [p for p in lead_row.providers_used.split(" | ") if p],
                "outreach_status": lead_row.outreach_status,
                "approval_status": lead_row.approval_status,
                "send_status": lead_row.send_status,
                "owner": lead_row.owner,
                "notes": lead_row.notes,
                "created_at": lead_row.created_at,
                "updated_at": lead_row.updated_at,
            },
            "best_contact": {
                "contact_id": lead_row.best_contact_id,
                "full_name": lead_row.best_contact_name,
                "job_title": lead_row.best_contact_title,
                "work_email": lead_row.best_contact_email,
                "email_status": lead_row.best_contact_email_status,
                "email_confidence": lead_row.best_contact_email_confidence,
                "linkedin_url": lead_row.best_contact_linkedin,
                "contact_score": lead_row.best_contact_score,
                "buyer_role_match": lead_row.buyer_role_match,
            } if lead_row.best_contact_id else None,
            "contacts": [
                {
                    "contact_id": c.contact_id,
                    "first_name": c.first_name,
                    "last_name": c.last_name,
                    "full_name": c.full_name,
                    "job_title": c.job_title,
                    "seniority": c.seniority,
                    "department": c.department,
                    "work_email": c.work_email,
                    "email_status": c.email_status,
                    "email_confidence": c.email_confidence,
                    "linkedin_url": c.linkedin_url,
                    "buyer_role_match": c.buyer_role_match,
                    "contact_score": c.contact_score,
                    "data_sources": [s for s in c.data_sources.split(" | ") if s],
                    "is_best_contact": c.is_best_contact,
                    "contact_rank": c.contact_rank,
                }
                for c in contact_rows
            ],
            "jobs": [
                {
                    "job_title": j.job_title,
                    "job_url": j.job_url,
                    "source": j.source,
                    "source_company_key": j.source_company_key,
                    "location": j.location,
                    "employment_type": j.employment_type,
                    "posted_date": j.posted_date,
                    "seniority": j.seniority,
                    "remote_status": j.remote_status,
                    "technologies": [t for t in j.technologies.split(" | ") if t],
                    "relevant_signals": [s for s in j.relevant_signals.split(" | ") if s],
                    "signal_evidence": [e for e in j.signal_evidence.split(" | ") if e],
                    "is_relevant": j.is_relevant,
                }
                for j in job_rows
            ],
            "audit": {
                "errors": [
                    {
                        "stage": e.stage,
                        "error_type": e.error_type,
                        "provider": e.provider,
                        "message": e.message,
                        "created_at": e.created_at
                    }
                    for e in error_rows
                ]
            }
        }
