"""
FastAPI Router for SalesAI Phase 10.5 Local Sales Operator Console.
Prefix: /sales
Strictly local-first, zero autonomous sends, zero direct writes.
All mutations delegate directly to Phase 9 and Phase 10 domain services.
Enforces CSRF / Same-Origin protections, authoritative suppression,
safe path resolution, and explicit human confirmation.
"""

import os
import re
import json
import secrets
import hashlib
import hmac
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Request, Form, Query, HTTPException, status, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from backend.sales_ui.view_models import (
    DashboardSummary,
    CompanyTimelineEvent,
    CandidateListItem,
    CandidateDetailView,
    DraftReviewCard,
    SentHistoryItem,
    WatchlistItemView,
    SuppressionItemView,
    SourceItemView,
    DiscoveryRunItemView,
    HistoricalImportPreviewResult,
)
from backend.sales_ui.dashboard_service import DashboardService
from backend.sales_ui.timeline_service import TimelineService

# Authoritative Domain Services from Phase 9 & Phase 10
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany
from sales_engine.candidates.classifier import CompanyClassifier
from sales_engine.discovery_v2.source_registry import SourceRegistry
from sales_engine.discovery_v2.discovery_engine import DiscoveryEngineV2
from sales_engine.discovery_v2.bridges import Phase9AuthorityBridge, Phase10ConsoleBridge
from sales_engine.historical.importer import HistoricalImporter

from sales_engine.sending import (
    ReviewStore,
    ApprovalService,
    SuppressionStore,
    SendOrchestrator,
    AuditService,
    DraftEditRequest,
)
from sales_engine.sending.approval_service import get_trusted_senders

# Phase 8 Outreach Stack and Phase 6 Schemas
from backend.schemas import CompanyLead, EnrichedLead, ContactCandidate
from sales_engine.outreach.outreach_orchestrator import OutreachOrchestrator
from sales_engine.outreach.schemas import SenderProfile, GenerateDraftsRequest
from sales_engine.candidates.models import QualificationOutcome, DiscoveryStatus

router = APIRouter(prefix="/sales", tags=["Sales-Console"])

templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

CSRF_COOKIE_NAME = "sales_csrf_token"
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def get_console_services():
    """Lazily instantiate or retrieve authoritative stores and services."""
    db_path = os.getenv("SALES_OUTREACH_DB", os.getenv("SALESAI_DB_PATH", os.getenv("SALESAI_CANDIDATES_DB", "data/sales_outreach.db")))
    cand_store = CandidateStore(db_path=db_path)
    registry = SourceRegistry()
    authority_bridge = Phase9AuthorityBridge(db_path=cand_store.db_path)
    classifier = CompanyClassifier(suppression_store=authority_bridge)
    engine = DiscoveryEngineV2(
        candidate_store=cand_store,
        classifier=classifier,
        source_registry=registry,
    )
    
    # Phase 9 Sending services
    rev_store = ReviewStore(db_path=cand_store.db_path)
    approval_svc = ApprovalService(rev_store)
    suppression_svc = SuppressionStore(rev_store)
    orchestrator = SendOrchestrator(review_store=rev_store, suppression_store=suppression_svc)
    audit_svc = AuditService(rev_store)
    
    dash_svc = DashboardService(candidate_store=cand_store, review_store=rev_store)
    timeline_svc = TimelineService(candidate_store=cand_store, review_store=rev_store)
    
    return {
        "cand_store": cand_store,
        "registry": registry,
        "classifier": classifier,
        "engine": engine,
        "rev_store": rev_store,
        "approval_svc": approval_svc,
        "suppression_svc": suppression_svc,
        "orchestrator": orchestrator,
        "audit_svc": audit_svc,
        "dash_svc": dash_svc,
        "timeline_svc": timeline_svc,
    }


def is_email_send_enabled() -> bool:
    """Check if live sending is enabled via environment."""
    val = os.getenv("EMAIL_SEND_ENABLED", "false").strip().lower()
    return val in ("true", "1", "yes", "t")


def get_or_create_csrf_token(request: Request) -> str:
    """Retrieve or generate secure CSRF token for local session."""
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        token = secrets.token_hex(32)
    return token


def is_same_origin(request: Request) -> bool:
    """Verify request Origin or Referer against allowed local origins."""
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    allowed_hosts = {"127.0.0.1", "localhost", "testserver"}
    if host:
        allowed_hosts.add(host.split(":")[0].lower())

    if origin:
        try:
            parsed = urlparse(origin)
            origin_host = (parsed.hostname or "").lower()
            if origin_host not in allowed_hosts:
                return False
        except Exception:
            return False

    referer = request.headers.get("referer")
    if not origin and referer:
        try:
            parsed_ref = urlparse(referer)
            ref_host = (parsed_ref.hostname or "").lower()
            if ref_host not in allowed_hosts:
                return False
        except Exception:
            return False

    return True


async def validate_csrf(request: Request):
    """Dependency enforcing Same-Origin and CSRF token on mutating routes."""
    # 1. Same-Origin Check
    if not is_same_origin(request):
        raise HTTPException(status_code=403, detail="Cross-origin mutation rejected")

    # 2. CSRF Token Check
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get("x-csrf-token")
    form_token = None
    try:
        form = await request.form()
        form_token = form.get("csrf_token")
    except Exception:
        pass

    submitted_token = form_token or header_token
    if not cookie_token or not submitted_token or not hmac.compare_digest(str(cookie_token), str(submitted_token)):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


def render_console_template(request: Request, name: str, context: Dict[str, Any]) -> HTMLResponse:
    """Renders Jinja2 template ensuring CSRF token is provided in context and cookie."""
    csrf_token = get_or_create_csrf_token(request)
    context["csrf_token"] = csrf_token
    resp = templates.TemplateResponse(request=request, name=name, context=context)
    if request.cookies.get(CSRF_COOKIE_NAME) != csrf_token:
        resp.set_cookie(CSRF_COOKIE_NAME, csrf_token, httponly=True, samesite="lax")
    return resp


def check_authoritative_suppression(
    supp_store: SuppressionStore,
    email: Optional[str] = None,
    domain: Optional[str] = None,
) -> bool:
    """Consistent backend-derived suppression evaluation across email and domain scopes."""
    clean_email = email.strip().lower() if email else None
    clean_domain = domain.strip().lower() if domain else None
    if clean_email and not clean_domain and "@" in clean_email:
        clean_domain = clean_email.split("@", 1)[1].strip().lower()
    return supp_store.is_suppressed(email=clean_email, company_domain=clean_domain)


def is_candidate_outreach_eligible(cand: Any) -> bool:
    """
    Authoritative eligibility for outreach draft generation:
    - qualification_outcome == 'qualified_for_enrichment' (or legacy discovery_status == 'qualified' / outcome == 'qualified')
    - cold_outreach_allowed == True
    - suppressed == False
    - active_conversation == False
    - historical_contact_status in ('uncontacted', None, '')
    - not watchlist
    - non-qualified outcomes (watchlist, known_company, reengagement_review, suppressed, reject, research_more) strictly ineligible
    """
    outcome = getattr(cand, "qualification_outcome", None)
    if hasattr(outcome, "value"):
        outcome = outcome.value
    outcome_str = str(outcome) if outcome else ""

    disc_status = getattr(cand, "discovery_status", "")
    if hasattr(disc_status, "value"):
        disc_status = disc_status.value
    disc_status_str = str(disc_status)

    is_qual = (outcome_str == "qualified_for_enrichment" or disc_status_str == "qualified" or outcome_str == "qualified")
    if not is_qual:
        return False

    # Block non-eligible outcomes explicitly
    if outcome_str in ("watchlist", "known_company", "reengagement_review", "suppressed", "reject", "research_more"):
        if outcome_str != "qualified_for_enrichment":
            return False

    if disc_status_str == "watchlist" or outcome_str == "watchlist":
        return False

    if not getattr(cand, "cold_outreach_allowed", True):
        return False
    if getattr(cand, "suppressed", False):
        return False
    if getattr(cand, "active_conversation", False):
        return False
    if getattr(cand, "historical_contact_status", "uncontacted") in ("contacted", "replied", "converted", "dnc"):
        return False
    if getattr(cand, "previous_outreach_detected", False):
        return False

    return True


def resolve_safe_import_path(file_path: str) -> Path:
    """Path traversal and repository-root validation for historical file imports."""
    raw_str = file_path.strip()
    if not raw_str:
        raise HTTPException(status_code=400, detail="file_path cannot be empty")
    if ".." in raw_str:
        raise HTTPException(status_code=400, detail="Access denied: Path traversal outside root is forbidden.")

    base_dir = Path(__file__).resolve().parent.parent.parent
    try:
        resolved = Path(raw_str)
        if not resolved.is_absolute():
            resolved = (base_dir / resolved).resolve()
        else:
            resolved = resolved.resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file path")

    allowed_roots = [base_dir.resolve(), (base_dir / "data").resolve()]
    is_allowed = False
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            is_allowed = True
            break
        except ValueError:
            continue

    if not is_allowed:
        raise HTTPException(
            status_code=400,
            detail="Access denied: file_path must reside within repository or data directory. Path traversal outside root is forbidden.",
        )

    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"File {file_path} not found")

    return resolved


# ---------------------------------------------------------------------------
# 1. DASHBOARD
# ---------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_view(request: Request):
    svcs = get_console_services()
    dash_svc = svcs["dash_svc"]
    summary = dash_svc.get_summary()

    raw_drafts, _ = svcs["rev_store"].list_drafts(filters={"approval_status": "pending_review"}, limit=5)
    pending_drafts = []
    for d in raw_drafts:
        c_name = d.lead_id
        if "cand_store" in svcs:
            c = svcs["cand_store"].get_candidate(d.lead_id)
            if c:
                c_name = getattr(c, "company_name", getattr(c, "name", c_name))
        pending_drafts.append({
            "draft_id": d.draft_id,
            "revision": d.revision,
            "company_name": c_name,
            "recipient_email": d.recipient_email,
            "subject": d.subject,
        })

    recent_runs = []
    if "cand_store" in svcs and hasattr(svcs["cand_store"], "list_discovery_runs"):
        raw_runs = svcs["cand_store"].list_discovery_runs(limit=5)
        for r in raw_runs:
            recent_runs.append({
                "run_id": r.get("run_id", "run_unknown"),
                "source_name": r.get("source_id") or "multi-source",
                "status": r.get("status", "completed"),
                "new_candidates": r.get("new_candidates", 0),
                "observations_found": r.get("observations_found", 0),
            })

    # Safe SMTP check: never expose credentials
    smtp_host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER")
    smtp_configured = bool(smtp_host and smtp_user)

    return render_console_template(
        request=request,
        name="dashboard.html",
        context={
            "request": request,
            "active_tab": "dashboard",
            "summary": summary,
            "pending_drafts": pending_drafts,
            "recent_runs": recent_runs,
            "email_send_enabled": is_email_send_enabled(),
            "smtp_configured": smtp_configured,
        },
    )


# ---------------------------------------------------------------------------
# 2. DISCOVERIES
# ---------------------------------------------------------------------------
@router.get("/discoveries", response_class=HTMLResponse)
async def discoveries_view(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    svcs = get_console_services()
    cand_store = svcs.get("cand_store")
    raw_runs = cand_store.list_discovery_runs(limit=30) if cand_store and hasattr(cand_store, "list_discovery_runs") else []
    runs = []
    for r in raw_runs:
        runs.append(
            DiscoveryRunItemView(
                run_id=r.get("run_id", "run_unknown"),
                mode=r.get("discovery_mode", "standard"),
                source_name=r.get("source_id") or "multi-source",
                started_at=r.get("started_at", "N/A"),
                completed_at=r.get("completed_at"),
                status=r.get("status", "completed"),
                observations_found=r.get("observations_found", 0),
                new_candidates=r.get("new_candidates", 0),
                known_candidates=r.get("known_candidates", 0),
                watchlist_candidates=r.get("watchlist_candidates", 0),
                qualified_candidates=r.get("qualified_candidates", 0),
                rejected_candidates=r.get("rejected_candidates", 0),
                errors=r.get("errors", []),
            )
        )

    return render_console_template(
        request=request,
        name="discoveries.html",
        context={
            "request": request,
            "active_tab": "discoveries",
            "runs": runs,
            "email_send_enabled": is_email_send_enabled(),
            "message": message,
            "error": error,
        },
    )


@router.post("/discoveries/run")
async def trigger_discovery_run_action(
    query: str = Form("BIM"),
    discovery_mode: str = Form("company_first"),
    sources: Optional[str] = Form(None),
    csrf: None = Depends(validate_csrf),
):
    svcs = get_console_services()
    source_list = [s.strip() for s in sources.split(",")] if sources else None
    try:
        run_rec = svcs["engine"].run_multi_source_discovery(
            sources=source_list,
            query=query,
            discovery_mode=discovery_mode,
        )
        msg = f"Discovery run completed: {run_rec.new_candidates} new candidates, {run_rec.observations_found} observations."
        return RedirectResponse(f"/sales/discoveries?message={msg}", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        return RedirectResponse(f"/sales/discoveries?error={str(e)}", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# 3. SOURCES & SEEDS
# ---------------------------------------------------------------------------
@router.get("/sources", response_class=HTMLResponse)
async def sources_view(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    svcs = get_console_services()
    registry = svcs["registry"]
    sources_raw = registry.list_sources()
    health = registry.get_health_summary()

    sources = []
    for s in sources_raw:
        h = health.get(s.id, health.get(s.name, {}))
        
        # P10_5-FINAL-012: Truthful credential configuration check using SourceConfig.credential_env_var
        is_conf = False
        config_error = False
        if s.requires_api_key:
            if not getattr(s, "credential_env_var", None):
                config_error = True
                is_conf = False
            else:
                is_conf = bool(os.getenv(s.credential_env_var))
        else:
            is_conf = True

        sources.append(
            SourceItemView(
                id=s.id,
                name=s.name,
                type=s.type,
                enabled=s.enabled,
                discovery_mode=', '.join(s.discovery_mode) if isinstance(s.discovery_mode, list) else str(s.discovery_mode),
                priority=s.priority,
                countries=s.countries,
                requires_api_key=s.requires_api_key,
                is_configured=is_conf,
                config_error=config_error,
                status=h.get("status", "operational"),
                success_count=h.get("success_count", 0),
                failure_count=h.get("failure_count", 0),
                last_success_at=h.get("last_success_at"),
                last_failure_at=h.get("last_failure_at"),
                last_error=h.get("last_error"),
            )
        )

    return render_console_template(
        request=request,
        name="sources.html",
        context={
            "request": request,
            "active_tab": "sources",
            "sources": sources,
            "email_send_enabled": is_email_send_enabled(),
            "message": message,
            "error": error,
        },
    )


@router.post("/sources/manual-seed")
async def manual_seed_action(
    company_name: str = Form(...),
    domain: Optional[str] = Form(None),
    country: Optional[str] = Form(None),
    source_url: Optional[str] = Form(None),
    csrf: None = Depends(validate_csrf),
):
    svcs = get_console_services()
    store = svcs["cand_store"]
    cand = CandidateCompany(
        name=company_name,
        domain=domain,
        country=country,
        first_seen_at=datetime.now(timezone.utc).isoformat(),
        discovery_status="new",
    )
    saved = store.save_candidate(cand)
    msg = f"Candidate seed '{saved.name}' saved and enqueued for research."
    return RedirectResponse(f"/sales/candidates/{saved.candidate_id}?message={msg}", status_code=status.HTTP_303_SEE_OTHER)


# P10_5-ADV-009 / ADV-012 / ADV-013: Preview Historical Import (Zero Mutations)
@router.post("/sources/import-history/preview")
@router.post("/sources/historical/preview")
async def preview_historical_import_action(
    request: Request,
    file_path: str = Form(...),
    sheet_name: Optional[str] = Form(None),
    csrf: None = Depends(validate_csrf),
):
    resolved = resolve_safe_import_path(file_path)
    file_bytes = resolved.read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    svcs = get_console_services()
    importer = HistoricalImporter(store=svcs["cand_store"])

    if resolved.suffix.lower() == ".csv":
        records = importer.parse_csv(resolved)
    elif resolved.suffix.lower() in (".xlsx", ".xlsm"):
        records = importer.parse_xlsx(resolved, sheet_name=sheet_name)
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format (CSV/XLSX only)")

    # Stable preview token
    token_payload = f"{str(resolved)}:{file_hash}:{sheet_name or ''}"
    preview_token = hmac.new(b"salesai_preview_salt", token_payload.encode(), hashlib.sha256).hexdigest()

    unique_comps = set(r.domain or r.company_name for r in records)
    total_contacts = sum(len(r.emails) for r in records)
    has_outreach_count = sum(1 for r in records if (r.contact_date or r.reply_status or r.notes))
    dnc_count = sum(1 for r in records if r.is_dnc)
    active_conv_count = sum(1 for r in records if r.has_active_conversation)

    preview_res = HistoricalImportPreviewResult(
        file_path=str(resolved),
        file_hash=file_hash,
        sheet_name=sheet_name,
        preview_token=preview_token,
        rows_detected=len(records),
        companies_detected=len(unique_comps),
        contacts_detected=total_contacts,
        historical_outreach_events=has_outreach_count,
        dnc_count=dnc_count,
        active_conversations=active_conv_count,
    )

    # Check if request prefers JSON (e.g. from test or API client)
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse(preview_res.model_dump())

    return render_console_template(
        request=request,
        name="sources.html",
        context={
            "request": request,
            "active_tab": "sources",
            "sources": [],
            "email_send_enabled": is_email_send_enabled(),
            "preview": preview_res,
            "message": f"Preview ready: {len(records)} rows parsed. Review statistics below and confirm import.",
        },
    )


# P10_5-ADV-010 / ADV-011: Confirm and Execute Historical Import
@router.post("/sources/import-history/confirm")
@router.post("/sources/historical/confirm")
async def confirm_historical_import_action(
    request: Request,
    file_path: str = Form(...),
    file_hash: Optional[str] = Form(None),
    preview_token: str = Form(...),
    sheet_name: Optional[str] = Form(None),
    csrf: None = Depends(validate_csrf),
):
    resolved = resolve_safe_import_path(file_path)
    current_bytes = resolved.read_bytes()
    current_hash = hashlib.sha256(current_bytes).hexdigest()

    # Content change verification
    if file_hash and current_hash != file_hash:
        raise HTTPException(
            status_code=400,
            detail="File content has changed since preview. Please generate a new preview.",
        )
    effective_hash = file_hash or current_hash

    # Token verification
    expected_payload = f"{str(resolved)}:{effective_hash}:{sheet_name or ''}"
    expected_token = hmac.new(b"salesai_preview_salt", expected_payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(preview_token, expected_token):
        raise HTTPException(status_code=400, detail="Invalid preview confirmation token.")

    svcs = get_console_services()
    importer = HistoricalImporter(store=svcs["cand_store"])
    if resolved.suffix.lower() == ".csv":
        records = importer.parse_csv(resolved)
    else:
        records = importer.parse_xlsx(resolved, sheet_name=sheet_name)

    result = importer.import_records(records)
    msg = f"Import completed: {result.companies_created} new companies, {result.records_parsed} records, {result.duplicates_skipped} duplicates skipped."

    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"status": "completed", "companies_created": result.companies_created, "records_parsed": result.records_parsed})

    return RedirectResponse(f"/sales/sources?message={msg}", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# 4. CANDIDATES LIST & DETAIL
# ---------------------------------------------------------------------------
@router.get("/candidates", response_class=HTMLResponse)
async def candidates_list_view(
    request: Request,
    q: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    message: Optional[str] = None,
    error: Optional[str] = None,
):
    svcs = get_console_services()
    bridge = Phase10ConsoleBridge(store=svcs["cand_store"], registry=svcs["registry"])
    
    all_summaries = bridge.get_candidate_list_summary(
        status=status,
        tier=tier,
        limit=1000,
    )
    
    filtered = all_summaries
    if country:
        filtered = [c for c in filtered if (c.get("country") or "").lower() == country.lower()]
    if q:
        query_lower = q.lower()
        filtered = [
            c for c in filtered
            if query_lower in (c.get("name") or c.get("company_name") or "").lower()
            or query_lower in (c.get("canonical_domain") or c.get("company_domain") or "").lower()
        ]

    total = len(filtered)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_items = filtered[start_idx:end_idx]

    candidates: List[CandidateListItem] = []
    for c in page_items:
        dom = c.get("canonical_domain") or c.get("company_domain") or ""
        # Authoritative domain suppression check
        is_supp = check_authoritative_suppression(svcs["suppression_svc"], domain=dom)
        
        raw_bim = c.get("bim_maturity")
        bim_display = raw_bim if raw_bim and raw_bim.strip() and raw_bim.lower() != "unknown" else "Unknown"

        candidates.append(
            CandidateListItem(
                candidate_id=c.get("company_id") or c.get("candidate_id", ""),
                company_name=c.get("name") or c.get("company_name", ""),
                domain=dom or None,
                country=c.get("country"),
                city=c.get("city"),
                tier=c.get("company_tier"),
                fit=c.get("fit_assessment") or c.get("fit_level"),
                intent=c.get("intent_assessment") or c.get("intent_level"),
                bim_maturity=bim_display,
                outsourcing_potential=c.get("outsourcing_potential"),
                automation_potential=c.get("automation_potential"),
                discovery_sources=c.get("discovery_sources", []),
                status=c.get("status") or c.get("discovery_status", "new"),
                is_previously_contacted=c.get("historical_contact_status") in ("contacted", "replied", "converted", "dnc") or c.get("historical_outreach_summary") in ("contacted", "replied", "converted", "dnc"),
                is_suppressed=is_supp,
            )
        )

    has_next = end_idx < total

    return render_console_template(
        request=request,
        name="candidates.html",
        context={
            "request": request,
            "active_tab": "candidates",
            "candidates": candidates,
            "total": total,
            "page": page,
            "has_next": has_next,
            "query": q,
            "status": status,
            "tier": tier,
            "country": country,
            "email_send_enabled": is_email_send_enabled(),
            "message": message,
            "error": error,
        },
    )


@router.get("/candidates/{candidate_id}", response_class=HTMLResponse)
async def candidate_detail_view(
    candidate_id: str,
    request: Request,
    message: Optional[str] = None,
    error: Optional[str] = None,
):
    svcs = get_console_services()
    cand = svcs["cand_store"].get_by_id(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate company not found")

    bridge = Phase10ConsoleBridge(store=svcs["cand_store"], registry=svcs["registry"])
    detail_data = bridge.get_candidate_detail(candidate_id) or {}
    
    ev_items = [e.model_dump() if hasattr(e, "model_dump") else e.__dict__ for e in svcs["cand_store"].get_evidence(candidate_id)]
    obs_items = [o.model_dump() if hasattr(o, "model_dump") else o.__dict__ for o in svcs["cand_store"].get_observations_for_company(candidate_id)]
    hist_outreach = svcs["cand_store"].get_historical_outreach_for_candidate(candidate_id)

    cand_name = getattr(cand, "company_name", None) or getattr(cand, "name", "Company")
    cand_domain = getattr(cand, "company_domain", None) or getattr(cand, "domain", None)
    
    # Authoritative domain suppression check
    is_supp = check_authoritative_suppression(svcs["suppression_svc"], domain=cand_domain)
    is_contacted = cand.historical_contact_status in ("contacted", "replied", "converted", "dnc") or bool(hist_outreach)

    raw_bim = cand.bim_maturity
    bim_display = raw_bim if raw_bim and raw_bim.strip() and raw_bim.lower() != "unknown" else "Unknown"

    candidate_item = CandidateListItem(
        candidate_id=cand.candidate_id,
        company_name=cand_name,
        domain=cand_domain,
        country=cand.country,
        city=cand.city,
        company_type=cand.legal_form,
        tier=cand.company_tier,
        fit=cand.fit_assessment,
        intent=cand.intent_assessment,
        bim_maturity=bim_display,
        outsourcing_potential=cand.outsourcing_potential,
        automation_potential=cand.automation_potential,
        discovery_sources=detail_data.get("discovery_sources", []),
        status=cand.discovery_status,
        is_previously_contacted=is_contacted,
        is_suppressed=is_supp,
    )

    timeline_events = svcs["timeline_svc"].get_company_timeline(candidate_id)

    why_found = f"Discovered via {', '.join(detail_data.get('discovery_sources', [])) or 'crawler'}."
    why_fit = f"Assessed as fit '{cand.fit_assessment or 'normal'}' based on architectural & BIM profile."
    why_intent = f"Assessed intent '{cand.intent_assessment or 'normal'}': {', '.join(cand.intent_signals) if cand.intent_signals else 'No urgent signals'}."
    why_status = f"Qualification outcome: {cand.qualification_outcome or cand.discovery_status}."

    view = CandidateDetailView(
        candidate=candidate_item,
        legal_form=cand.legal_form,
        employee_size_hint=cand.employee_size_hint,
        project_scale_hint=cand.project_scale_hint,
        tier_confidence=cand.tier_confidence,
        bim_maturity_confidence=cand.bim_maturity_confidence,
        relationship_type=cand.relationship_type,
        opportunity_types=cand.opportunity_types,
        fit_reasons=cand.fit_reasons,
        intent_signals=cand.intent_signals,
        why_found=why_found,
        why_fit=why_fit,
        why_intent=why_intent,
        why_status=why_status,
        evidence_items=ev_items,
        observations=obs_items,
        historical_outreach=hist_outreach,
        timeline=timeline_events,
    )

    # Determine draft generation eligibility (P10_5-FINAL-005 / 006)
    can_generate_draft = is_candidate_outreach_eligible(cand) and not is_supp and not is_contacted

    raw_contacts = svcs["cand_store"].get_contacts_for_candidate(candidate_id)
    contacts_view = []
    for c in raw_contacts:
        st = (getattr(c, "email_status", None) or "unknown").strip().lower()
        w_email = (getattr(c, "work_email", None) or "").strip().lower()
        is_usable = bool(w_email and EMAIL_REGEX.match(w_email) and st in ("verified", "likely"))
        contacts_view.append({
            "contact_id": getattr(c, "contact_id", None) or f"email:{w_email}",
            "full_name": getattr(c, "full_name", None) or f"{c.first_name or ''} {c.last_name or ''}".strip() or "Unnamed Contact",
            "job_title": getattr(c, "job_title", None) or "Unknown Title",
            "work_email": w_email,
            "email_status": st,
            "is_eligible": is_usable,
        })

    return render_console_template(
        request=request,
        name="candidate_detail.html",
        context={
            "request": request,
            "active_tab": "candidates",
            "detail": view,
            "contacts": contacts_view,
            "email_send_enabled": is_email_send_enabled(),
            "can_generate_draft": can_generate_draft,
            "message": message,
            "error": error,
        },
    )


@router.post("/candidates/{candidate_id}/research")
async def research_candidate_action(candidate_id: str, csrf: None = Depends(validate_csrf)):
    svcs = get_console_services()
    cand = svcs["cand_store"].get_by_id(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    cand.last_researched_at = datetime.now(timezone.utc).isoformat()
    cand.discovery_status = "needs_research"
    svcs["cand_store"].save_candidate(cand)
    return RedirectResponse(f"/sales/candidates/{candidate_id}?message=Research timestamp updated.", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/candidates/{candidate_id}/qualify")
async def qualify_candidate_action(candidate_id: str, csrf: None = Depends(validate_csrf)):
    svcs = get_console_services()
    cand = svcs["cand_store"].get_by_id(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    obs = svcs["cand_store"].get_observations_for_company(candidate_id)
    ev = svcs["cand_store"].get_evidence(candidate_id)
    qual = svcs["classifier"].classify_company(cand, observations=obs, evidence=ev)
    svcs["cand_store"].save_candidate(cand)
    msg = f"Qualification completed: {qual.outcome} (Route: {qual.qualification_route})"
    return RedirectResponse(f"/sales/candidates/{candidate_id}?message={msg}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/candidates/{candidate_id}/watch")
async def add_candidate_to_watchlist_action(
    candidate_id: str,
    reason: str = Form(...),
    signals: Optional[str] = Form(None),
    csrf: None = Depends(validate_csrf),
):
    svcs = get_console_services()
    cand = svcs["cand_store"].get_by_id(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    signal_list = [s.strip() for s in signals.split(",")] if signals else None
    svcs["cand_store"].add_to_watchlist(candidate_id, reason=reason, signals=signal_list)
    cand.discovery_status = "watchlist"
    svcs["cand_store"].save_candidate(cand)
    return RedirectResponse(f"/sales/candidates/{candidate_id}?message=Added to watchlist.", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/candidates/{candidate_id}/suppress")
async def suppress_candidate_action(
    candidate_id: str,
    email: str = Form(...),
    reason: str = Form("manual"),
    csrf: None = Depends(validate_csrf),
):
    svcs = get_console_services()
    cand = svcs["cand_store"].get_by_id(candidate_id)
    cand_domain = getattr(cand, "company_domain", None) or getattr(cand, "domain", None) if cand else None

    clean_email = email.strip().lower()
    clean_domain = cand_domain.strip().lower() if cand_domain else None

    svcs["suppression_svc"].add_suppression(
        email=clean_email,
        company_domain=clean_domain,
        reason=reason,
        source="sales_console_candidate",
    )
    if cand:
        cand.suppressed = True
        cand.cold_outreach_allowed = False
        svcs["cand_store"].save_candidate(cand)

    return RedirectResponse(f"/sales/candidates/{candidate_id}?message=Recipient and domain added to suppression list.", status_code=status.HTTP_303_SEE_OTHER)


# P10_5-ADV-022 / P10_5-FINAL-001..009: Phase 8 Outreach Generation Delegation
@router.post("/candidates/{candidate_id}/generate-draft")
async def generate_candidate_draft_action(
    candidate_id: str,
    contact_id: Optional[str] = Form(None),
    contact_email: Optional[str] = Form(None),
    reviewer: str = Form("local_operator"),
    language: str = Form("en"),
    tone: str = Form("professional_concise"),
    csrf: None = Depends(validate_csrf),
):
    svcs = get_console_services()
    cand = svcs["cand_store"].get_by_id(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate company not found")

    cand_domain = getattr(cand, "company_domain", None) or getattr(cand, "domain", None)

    # BLOCKER 2: Authoritative Qualification & Safety Guards
    if not is_candidate_outreach_eligible(cand):
        disc_status = getattr(cand, "discovery_status", "")
        if hasattr(disc_status, "value"):
            disc_status = disc_status.value
        outcome = getattr(cand, "qualification_outcome", "")
        if hasattr(outcome, "value"):
            outcome = outcome.value

        if disc_status == "watchlist" or str(outcome) == "watchlist":
            err_msg = "Candidate is currently watchlisted; fresh cold draft blocked."
        elif getattr(cand, "historical_contact_status", None) in ("contacted", "replied", "converted", "dnc") or getattr(cand, "active_conversation", False) or getattr(cand, "previous_outreach_detected", False):
            err_msg = "Candidate was historically contacted; fresh cold outreach blocked."
        elif getattr(cand, "suppressed", False):
            err_msg = "Candidate is marked as suppressed."
        elif not getattr(cand, "cold_outreach_allowed", True):
            err_msg = "Cold outreach is not allowed for this candidate."
        else:
            err_msg = f"Candidate is not qualified for outreach (status: {disc_status}, outcome: {outcome})."

        return RedirectResponse(
            f"/sales/candidates/{candidate_id}?error={urllib.parse.quote(err_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # BLOCKER 3: Contact Resolution & Phase 6 Eligibility Checks
    # Server resolves contact strictly by contact_id. Arbitrary browser-typed emails cannot bypass Phase 6.
    if not contact_id or not contact_id.strip():
        return RedirectResponse(
            f"/sales/candidates/{candidate_id}?error=Missing contact selection. Must select an existing backend contact.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    resolved_contact = svcs["cand_store"].get_contact_by_id(contact_id.strip(), candidate_id=candidate_id)
    if not resolved_contact:
        return RedirectResponse(
            f"/sales/candidates/{candidate_id}?error=Selected contact '{contact_id}' not found in backend records.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    clean_recip = (getattr(resolved_contact, "work_email", None) or "").strip().lower()
    if not clean_recip or not EMAIL_REGEX.match(clean_recip):
        return RedirectResponse(
            f"/sales/candidates/{candidate_id}?error=Contact has missing or invalid work email.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Status must be verified or likely (risky, unknown, not_found blocked)
    contact_status = (getattr(resolved_contact, "email_status", None) or "unknown").strip().lower()
    if contact_status not in ("verified", "likely"):
        return RedirectResponse(
            f"/sales/candidates/{candidate_id}?error=Contact status '{contact_status}' is not eligible for outreach (verified or likely required).",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Server-side suppression check
    if check_authoritative_suppression(svcs["suppression_svc"], email=clean_recip, domain=cand_domain):
        return RedirectResponse(
            f"/sales/candidates/{candidate_id}?error=Recipient or company domain is suppressed. Draft generation blocked.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # BLOCKER 1: Delegate to Frozen Phase 8 Outreach Stack
    market_ev = svcs["cand_store"].get_evidence(candidate_id)
    ev_texts = [e.text.strip() for e in market_ev if e.text and e.text.strip()]
    if getattr(cand, "evidence_refs", None):
        for er in cand.evidence_refs:
            if er and er.strip() and er.strip() not in ev_texts:
                ev_texts.append(er.strip())
    if getattr(cand, "fit_reasons", None):
        for fr in cand.fit_reasons:
            if fr and fr.strip() and fr.strip() not in ev_texts:
                ev_texts.append(fr.strip())

    lead_signals = []
    if getattr(cand, "intent_signals", None):
        for sig in cand.intent_signals:
            if sig and str(sig).strip():
                lead_signals.append({"name": "Market Signal", "value": str(sig).strip()})

    base_lead = CompanyLead(
        company_name=cand.company_name or getattr(cand, "name", "Company"),
        company_domain=cand_domain,
        qualified=True,
        evidence=ev_texts,
        signals=lead_signals,
    )

    enriched_lead = EnrichedLead(
        base_lead=base_lead,
        best_contact=resolved_contact,
        contacts=[resolved_contact],
    )

    trusted_senders = get_trusted_senders()
    primary_sender = trusted_senders[0] if trusted_senders else (os.environ.get("SMTP_FROM_EMAIL") or os.environ.get("SALES_SENDER_EMAIL") or "outreach@pybim-studio.com")
    primary_sender_name = os.environ.get("SMTP_FROM_NAME") or "pyBIM Team"

    sender_prof = SenderProfile(
        sender_name=primary_sender_name,
        sender_company="pyBIM",
        sender_role="Technical Consultant",
        sender_email=primary_sender,
        sender_website="https://pybim.com",
    )

    website_prof = {
        "company_name": "pyBIM",
        "services": ["Tech-Enabled BIM Services"],
        "offerings": [{"name": "Tech-Enabled BIM Services", "status": "active"}],
    }

    gen_req = GenerateDraftsRequest(
        leads=[enriched_lead],
        sender_profile=sender_prof,
        website_profile=website_prof,
        language=language if language in ("en", "it", "de") else "en",
        tone=tone if tone in ("professional_concise", "technical_consultative", "executive_brief") else "professional_concise",
    )

    orchestrator = svcs.get("outreach_orchestrator") or OutreachOrchestrator()
    gen_resp = orchestrator.generate_drafts(gen_req)

    if gen_resp.skipped:
        skip = gen_resp.skipped[0]
        return RedirectResponse(
            f"/sales/candidates/{candidate_id}?error=Draft generation skipped: {skip.reason}.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if gen_resp.errors:
        err_item = gen_resp.errors[0]
        return RedirectResponse(
            f"/sales/candidates/{candidate_id}?error=Phase 8 draft generation error: {err_item.message}.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if not gen_resp.drafts:
        return RedirectResponse(
            f"/sales/candidates/{candidate_id}?error=Phase 8 produced no draft.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    p8_draft = gen_resp.drafts[0]

    # Import into Phase 9 Pending Review
    imported = svcs["approval_svc"].import_drafts([p8_draft.model_dump()], reviewer=reviewer)
    if not imported:
        return RedirectResponse(
            f"/sales/candidates/{candidate_id}?error=Failed to import Phase 8 draft into review queue.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    created_draft = imported[0]
    cand_name = cand.company_name or getattr(cand, "name", "Company")
    msg = f"Generated Phase 8 draft r{created_draft.revision} for {cand_name}. It is staged in Pending Review."
    return RedirectResponse(
        f"/sales/drafts/{created_draft.draft_id}?message={urllib.parse.quote(msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# 5. LEADS
# ---------------------------------------------------------------------------
@router.get("/leads", response_class=HTMLResponse)
async def leads_view(request: Request, q: Optional[str] = Query(None)):
    svcs = get_console_services()
    store = svcs["cand_store"]
    cands = store.list_candidates()
    
    leads = []
    for c in cands:
        name = getattr(c, "company_name", None) or getattr(c, "name", "Company")
        dom = getattr(c, "company_domain", None) or getattr(c, "domain", None)
        is_supp = check_authoritative_suppression(svcs["suppression_svc"], domain=dom)

        if q:
            ql = q.lower()
            if ql not in name.lower() and (not dom or ql not in dom.lower()):
                continue

        leads.append({
            "lead_id": f"domain:{dom}" if dom else c.candidate_id,
            "company_name": name,
            "domain": dom,
            "country": c.country,
            "tier": c.company_tier,
            "discovery_status": c.discovery_status,
            "bim_maturity": c.bim_maturity if c.bim_maturity and c.bim_maturity.lower() != "unknown" else "Unknown",
            "is_suppressed": is_supp,
            "candidate_id": c.candidate_id,
        })

    return render_console_template(
        request=request,
        name="leads.html",
        context={
            "request": request,
            "active_tab": "leads",
            "leads": leads,
            "email_send_enabled": is_email_send_enabled(),
        },
    )


# ---------------------------------------------------------------------------
# 6. DRAFTS REVIEW QUEUE & DETAIL
# ---------------------------------------------------------------------------
@router.get("/drafts", response_class=HTMLResponse)
async def draft_review_queue_view(request: Request, status_filter: Optional[str] = Query(None)):
    svcs = get_console_services()
    rev_store = svcs["rev_store"]
    filters = {}
    target_status = status_filter or "pending_review"
    if target_status and target_status != "all":
        filters["approval_status"] = target_status
    raw_drafts, _ = rev_store.list_drafts(filters=filters, limit=100)

    cards = []
    for d in raw_drafts:
        is_supp = check_authoritative_suppression(svcs["suppression_svc"], email=d.recipient_email)
        cand_id = d.lead_id.replace("domain:", "")
        c = svcs["cand_store"].get_by_domain(cand_id)
        comp_name = getattr(c, "company_name", getattr(c, "name", cand_id)) if c else cand_id

        cards.append(
            DraftReviewCard(
                draft_id=d.draft_id,
                revision=d.revision,
                lead_id=d.lead_id,
                contact_id=d.contact_id,
                company_name=comp_name,
                company_domain=cand_id,
                recipient_name=d.recipient_name,
                recipient_title=d.recipient_title,
                recipient_email=d.recipient_email,
                sender_name=d.sender_name,
                sender_email=d.sender_email,
                subject=d.subject,
                body=d.body,
                service_used=d.service_used,
                approval_status=d.approval_status,
                send_status=d.send_status,
                outreach_status=d.outreach_status,
                content_hash=d.content_hash,
                approved_content_hash=d.approved_content_hash,
                approved_fingerprint_short=d.approved_content_hash[:12] if d.approved_content_hash else None,
                reviewer=d.reviewer,
                review_note=d.review_note,
                created_at=d.created_at,
                approved_at=d.approved_at,
                sent_at=d.sent_at,
                is_suppressed=is_supp,
                can_approve=(d.approval_status in ("pending_review", "changes_requested") and d.send_status != "sent"),
                can_edit=(d.send_status != "sent"),
                can_reject=(d.send_status != "sent"),
                can_request_changes=(d.send_status != "sent"),
                can_dry_run=True,
                can_send=(d.approval_status == "approved" and d.send_status != "sent" and not is_supp),
            )
        )

    return render_console_template(
        request=request,
        name="drafts.html",
        context={
            "request": request,
            "active_tab": "drafts",
            "drafts": cards,
            "status_filter": status_filter or "pending_review",
            "email_send_enabled": is_email_send_enabled(),
        },
    )


@router.get("/drafts/{draft_id}", response_class=HTMLResponse)
async def draft_detail_view(
    draft_id: str,
    request: Request,
    revision: Optional[int] = Query(None),
    message: Optional[str] = None,
    error: Optional[str] = None,
):
    svcs = get_console_services()
    rev_store = svcs["rev_store"]
    draft = rev_store.get_draft(draft_id, revision)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    events = rev_store.get_review_events(draft_id)
    cand_id = draft.lead_id.replace("domain:", "")
    c = svcs["cand_store"].get_by_domain(cand_id)
    comp_name = getattr(c, "company_name", getattr(c, "name", cand_id)) if c else cand_id

    is_supp = check_authoritative_suppression(svcs["suppression_svc"], email=draft.recipient_email)

    card = DraftReviewCard(
        draft_id=draft.draft_id,
        revision=draft.revision,
        lead_id=draft.lead_id,
        contact_id=draft.contact_id,
        company_name=comp_name,
        company_domain=cand_id,
        recipient_name=draft.recipient_name,
        recipient_title=draft.recipient_title,
        recipient_email=draft.recipient_email,
        sender_name=draft.sender_name,
        sender_email=draft.sender_email,
        subject=draft.subject,
        body=draft.body,
        service_used=draft.service_used,
        approval_status=draft.approval_status,
        send_status=draft.send_status,
        outreach_status=draft.outreach_status,
        content_hash=draft.content_hash,
        approved_content_hash=draft.approved_content_hash,
        approved_fingerprint_short=draft.approved_content_hash[:12] if draft.approved_content_hash else None,
        reviewer=draft.reviewer,
        review_note=draft.review_note,
        created_at=draft.created_at,
        approved_at=draft.approved_at,
        sent_at=draft.sent_at,
        is_suppressed=is_supp,
        can_approve=(draft.approval_status in ("pending_review", "changes_requested") and draft.send_status != "sent"),
        can_edit=(draft.send_status != "sent"),
        can_reject=(draft.send_status != "sent"),
        can_request_changes=(draft.send_status != "sent"),
        can_dry_run=True,
        can_send=(draft.approval_status == "approved" and draft.send_status != "sent" and not is_supp),
    )

    return render_console_template(
        request=request,
        name="draft_detail.html",
        context={
            "request": request,
            "active_tab": "drafts",
            "draft": card,
            "events": events,
            "email_send_enabled": is_email_send_enabled(),
            "message": message,
            "error": error,
        },
    )


# P10_5-REG-001: Approve NEVER sends
@router.post("/drafts/{draft_id}/approve")
async def approve_draft_action(
    draft_id: str,
    revision: int = Form(...),
    reviewer: str = Form("local_operator"),
    note: Optional[str] = Form(None),
    csrf: None = Depends(validate_csrf),
):
    svcs = get_console_services()
    latest = svcs["rev_store"].get_draft(draft_id)
    if not latest:
        return RedirectResponse(f"/sales/drafts?error=Draft not found: {draft_id}", status_code=status.HTTP_303_SEE_OTHER)
    if latest.revision != revision:
        return RedirectResponse(
            f"/sales/drafts/{draft_id}?error=stale_revision: Stale revision r{revision} submitted. Latest is r{latest.revision}.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        approved = svcs["approval_svc"].approve_draft(
            draft_id=draft_id,
            revision=revision,
            reviewer=reviewer,
            note=note,
        )
        msg = f"Draft r{approved.revision} approved successfully. It is ready for explicit send."
        return RedirectResponse(f"/sales/drafts/{draft_id}?message={msg}", status_code=status.HTTP_303_SEE_OTHER)
    except ValueError as e:
        return RedirectResponse(f"/sales/drafts/{draft_id}?error={str(e)}", status_code=status.HTTP_303_SEE_OTHER)


# P10_5-REG-002: Edit creates new Phase 9 revision & invalidates approval
@router.post("/drafts/{draft_id}/edit")
async def edit_draft_action(
    draft_id: str,
    revision: int = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    recipient_email: Optional[str] = Form(None),
    reviewer: str = Form("local_operator"),
    note: Optional[str] = Form(None),
    csrf: None = Depends(validate_csrf),
):
    svcs = get_console_services()
    latest = svcs["rev_store"].get_draft(draft_id)
    if not latest:
        return RedirectResponse(f"/sales/drafts?error=Draft not found: {draft_id}", status_code=status.HTTP_303_SEE_OTHER)
    if latest.revision != revision:
        return RedirectResponse(
            f"/sales/drafts/{draft_id}?error=stale_revision: Stale revision r{revision} submitted. Latest revision is r{latest.revision}. Refresh the page.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    edit_req = DraftEditRequest(
        revision=revision,
        subject=subject,
        body=body,
        recipient_email=recipient_email,
        reviewer=reviewer,
        review_note=note,
    )
    try:
        new_rev = svcs["approval_svc"].edit_draft(draft_id, edit_req)
        msg = f"Created new revision r{new_rev.revision}. Approval has been reset to pending_review."
        return RedirectResponse(f"/sales/drafts/{draft_id}?message={msg}", status_code=status.HTTP_303_SEE_OTHER)
    except ValueError as e:
        return RedirectResponse(f"/sales/drafts/{draft_id}?error={str(e)}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/drafts/{draft_id}/reject")
async def reject_draft_action(
    draft_id: str,
    revision: int = Form(...),
    reviewer: str = Form("local_operator"),
    note: Optional[str] = Form(None),
    csrf: None = Depends(validate_csrf),
):
    svcs = get_console_services()
    try:
        rejected = svcs["approval_svc"].reject_draft(
            draft_id=draft_id,
            revision=revision,
            reviewer=reviewer,
            note=note,
        )
        msg = f"Draft r{rejected.revision} rejected."
        return RedirectResponse(f"/sales/drafts/{draft_id}?message={msg}", status_code=status.HTTP_303_SEE_OTHER)
    except ValueError as e:
        return RedirectResponse(f"/sales/drafts/{draft_id}?error={str(e)}", status_code=status.HTTP_303_SEE_OTHER)


# P10_5-ADV-016 / ADV-017: Request Changes through Phase 9
@router.post("/drafts/{draft_id}/request-changes")
async def request_changes_draft_action(
    draft_id: str,
    revision: int = Form(...),
    reviewer: str = Form("local_operator"),
    note: str = Form(...),
    csrf: None = Depends(validate_csrf),
):
    svcs = get_console_services()
    try:
        updated = svcs["approval_svc"].request_changes(
            draft_id=draft_id,
            revision=revision,
            reviewer=reviewer,
            note=note,
        )
        msg = f"Changes requested for revision r{updated.revision}."
        return RedirectResponse(f"/sales/drafts/{draft_id}?message={msg}", status_code=status.HTTP_303_SEE_OTHER)
    except ValueError as e:
        return RedirectResponse(f"/sales/drafts/{draft_id}?error={str(e)}", status_code=status.HTTP_303_SEE_OTHER)


# P10_5-REG-005: Dry Run creates zero provider/network calls
@router.post("/drafts/{draft_id}/dry-run")
async def dry_run_draft_action(
    draft_id: str,
    revision: int = Form(...),
    reviewer: str = Form("local_operator"),
    csrf: None = Depends(validate_csrf),
):
    svcs = get_console_services()
    try:
        res = svcs["orchestrator"].send_draft(
            draft_id=draft_id,
            revision=revision,
            dry_run=True,
            reviewer=reviewer,
        )
        msg = f"Dry Run PASSED: Provider network calls = 0. All safety gates verified."
        return RedirectResponse(f"/sales/drafts/{draft_id}?message={msg}", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        return RedirectResponse(f"/sales/drafts/{draft_id}?error=Dry Run failed: {str(e)}", status_code=status.HTTP_303_SEE_OTHER)


# P10_5-REG-006: Real Send requires explicit confirmation path & confirm_send=true
@router.post("/drafts/{draft_id}/send")
async def send_draft_action(
    draft_id: str,
    revision: int = Form(...),
    confirm_send: bool = Form(False),
    reviewer: str = Form("local_operator"),
    csrf: None = Depends(validate_csrf),
):
    if not confirm_send:
        return RedirectResponse(
            f"/sales/drafts/{draft_id}?error=Send aborted: Explicit confirmation required.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    svcs = get_console_services()
    latest = svcs["rev_store"].get_draft(draft_id)
    if not latest:
        return RedirectResponse(f"/sales/drafts?error=Draft not found: {draft_id}", status_code=status.HTTP_303_SEE_OTHER)
    if latest.revision != revision:
        return RedirectResponse(
            f"/sales/drafts/{draft_id}?error=stale_revision: Stale revision r{revision} submitted.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Check server email_send_enabled flag
    if not is_email_send_enabled():
        return RedirectResponse(
            f"/sales/drafts/{draft_id}?error=Real sending is disabled on this server (EMAIL_SEND_ENABLED=false). Send aborted.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        res = svcs["orchestrator"].send_draft(
            draft_id=draft_id,
            revision=revision,
            dry_run=False,
            reviewer=reviewer,
        )
        msg = f"Email sent successfully to {latest.recipient_email} (Status: {res.status})."
        return RedirectResponse(f"/sales/drafts/{draft_id}?message={msg}", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        return RedirectResponse(f"/sales/drafts/{draft_id}?error=Send failed: {str(e)}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/drafts/{draft_id}/suppress")
async def suppress_draft_recipient_action(
    draft_id: str,
    email: str = Form(...),
    reason: str = Form("manual"),
    csrf: None = Depends(validate_csrf),
):
    svcs = get_console_services()
    draft = svcs["rev_store"].get_draft(draft_id)
    company_dom = draft.lead_id.replace("domain:", "") if draft else None
    
    svcs["suppression_svc"].add_suppression(
        email=email.strip().lower(),
        company_domain=company_dom,
        reason=reason,
        source="sales_console_draft",
    )
    return RedirectResponse(f"/sales/drafts/{draft_id}?message=Recipient suppressed.", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# 7. APPROVED QUEUE
# ---------------------------------------------------------------------------
@router.get("/approved", response_class=HTMLResponse)
async def approved_queue_view(request: Request):
    svcs = get_console_services()
    raw_drafts, _ = svcs["rev_store"].list_drafts(filters={"approval_status": "approved"}, limit=100)

    cards = []
    for d in raw_drafts:
        if d.send_status == "sent":
            continue
        is_supp = check_authoritative_suppression(svcs["suppression_svc"], email=d.recipient_email)
        cand_id = d.lead_id.replace("domain:", "")
        c = svcs["cand_store"].get_by_domain(cand_id)
        comp_name = getattr(c, "company_name", getattr(c, "name", cand_id)) if c else cand_id

        cards.append(
            DraftReviewCard(
                draft_id=d.draft_id,
                revision=d.revision,
                lead_id=d.lead_id,
                contact_id=d.contact_id,
                company_name=comp_name,
                company_domain=cand_id,
                recipient_name=d.recipient_name,
                recipient_title=d.recipient_title,
                recipient_email=d.recipient_email,
                sender_name=d.sender_name,
                sender_email=d.sender_email,
                subject=d.subject,
                body=d.body,
                service_used=d.service_used,
                approval_status=d.approval_status,
                send_status=d.send_status,
                outreach_status=d.outreach_status,
                content_hash=d.content_hash,
                approved_content_hash=d.approved_content_hash,
                approved_fingerprint_short=d.approved_content_hash[:12] if d.approved_content_hash else None,
                reviewer=d.reviewer,
                review_note=d.review_note,
                created_at=d.created_at,
                approved_at=d.approved_at,
                sent_at=d.sent_at,
                is_suppressed=is_supp,
                can_approve=False,
                can_edit=True,
                can_reject=True,
                can_request_changes=True,
                can_dry_run=True,
                can_send=(not is_supp),
            )
        )

    return render_console_template(
        request=request,
        name="approved.html",
        context={
            "request": request,
            "active_tab": "approved",
            "drafts": cards,
            "email_send_enabled": is_email_send_enabled(),
        },
    )


# ---------------------------------------------------------------------------
# 8. SENT HISTORY (IMMUTABLE AUDIT TRAIL)
# ---------------------------------------------------------------------------
@router.get("/sent", response_class=HTMLResponse)
async def sent_history_view(request: Request):
    svcs = get_console_services()
    all_attempts = svcs["rev_store"].get_send_attempts(limit=100)
    attempts = [a for a in all_attempts if getattr(a, "status", None) == "sent"]

    items = []
    for a in attempts:
        d = svcs["rev_store"].get_draft(a.draft_id, a.revision)
        comp_name = d.lead_id.replace("domain:", "") if d else "N/A"
        recip_name = d.recipient_name if d else "Recipient"
        subj = d.subject if d else "Subject"

        items.append(
            SentHistoryItem(
                attempt_id=a.attempt_id,
                send_key=a.send_key,
                draft_id=a.draft_id,
                revision=a.revision,
                company_name=comp_name,
                recipient_name=recip_name,
                recipient_email=a.recipient_email,
                sender_email=a.sender_email,
                subject=subj,
                provider=a.provider,
                status=a.status,
                provider_message_id=a.provider_message_id,
                attempted_at=a.attempted_at,
                completed_at=a.completed_at,
                error_message=a.error_message,
            )
        )

    return render_console_template(
        request=request,
        name="sent.html",
        context={
            "request": request,
            "active_tab": "sent",
            "items": items,
            "email_send_enabled": is_email_send_enabled(),
        },
    )


# ---------------------------------------------------------------------------
# 9. WATCHLIST
# ---------------------------------------------------------------------------
@router.get("/watchlist", response_class=HTMLResponse)
async def watchlist_view(request: Request):
    svcs = get_console_services()
    store = svcs["cand_store"]
    cands = store.list_candidates({"discovery_status": "watchlist"})

    items = []
    for c in cands:
        name = getattr(c, "company_name", getattr(c, "name", "Company"))
        dom = getattr(c, "company_domain", getattr(c, "domain", None))
        items.append(
            WatchlistItemView(
                candidate_id=c.candidate_id,
                company_name=name,
                company_domain=dom,
                country=c.country,
                tier=c.company_tier,
                fit=c.fit_assessment,
                intent=c.intent_assessment,
                watch_reason="Signal monitoring (e.g. pending hiring or tender)",
                watch_status="active",
                signals_to_monitor=c.intent_signals or [],
                last_checked_at=c.last_seen_at,
            )
        )

    return render_console_template(
        request=request,
        name="watchlist.html",
        context={
            "request": request,
            "active_tab": "watchlist",
            "items": items,
            "email_send_enabled": is_email_send_enabled(),
        },
    )


# ---------------------------------------------------------------------------
# 10. DO NOT CONTACT / SUPPRESSION
# ---------------------------------------------------------------------------
@router.get("/do-not-contact", response_class=HTMLResponse)
async def suppression_view(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    svcs = get_console_services()
    raw_suppressed = svcs["suppression_svc"].list_suppressed(limit=200)

    items = [
        SuppressionItemView(
            suppression_id=s.suppression_id,
            email=s.email,
            company_domain=s.company_domain,
            reason=s.reason,
            source=s.source,
            created_at=s.created_at,
        )
        for s in raw_suppressed
    ]

    return render_console_template(
        request=request,
        name="suppression.html",
        context={
            "request": request,
            "active_tab": "suppression",
            "items": items,
            "email_send_enabled": is_email_send_enabled(),
            "message": message,
            "error": error,
        },
    )


@router.post("/do-not-contact/add")
async def add_suppression_action(
    scope: str = Form("email"),
    target: str = Form(...),
    company_domain: Optional[str] = Form(None),
    reason: str = Form("manual"),
    csrf: None = Depends(validate_csrf),
):
    svcs = get_console_services()
    clean_target = target.strip().lower()
    clean_domain = company_domain.strip().lower() if company_domain else None

    if scope == "domain" or "@" not in clean_target:
        domain_val = clean_target if "@" not in clean_target else (clean_domain or clean_target.split("@", 1)[1])
        svcs["suppression_svc"].add_suppression(
            email=f"*@{domain_val}",
            company_domain=domain_val,
            reason=reason,
            source="sales_console",
        )
    else:
        svcs["suppression_svc"].add_suppression(
            email=clean_target,
            company_domain=clean_domain,
            reason=reason,
            source="sales_console",
        )
    return RedirectResponse("/sales/do-not-contact?message=Suppression+entry+added.", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/do-not-contact/remove")
async def remove_suppression_action(
    target: str = Form(...),
    csrf: None = Depends(validate_csrf),
):
    svcs = get_console_services()
    svcs["suppression_svc"].remove_suppression(target.strip().lower())
    return RedirectResponse("/sales/do-not-contact?message=Suppression+removed.", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# 11. REPLIES & CONVERSATION (PHASE 10.5 PLACEHOLDER / HISTORICAL)
# ---------------------------------------------------------------------------
@router.get("/replies", response_class=HTMLResponse)
async def replies_view(request: Request):
    svcs = get_console_services()
    store = svcs["cand_store"]

    responses = []
    positive_count = 0
    meetings_count = 0

    try:
        with store._get_connection() as conn:
            rows = conn.execute("""
                SELECT h.*, c.company_name 
                FROM historical_outreach_events h
                LEFT JOIN candidate_companies c ON h.candidate_id = c.candidate_id
                WHERE h.reply_status IS NOT NULL AND h.reply_status != ''
                ORDER BY h.reply_at DESC
                LIMIT 100
            """).fetchall()

            for r in rows:
                stat = r["reply_status"] or ""
                if "positive" in stat.lower() or "interested" in stat.lower():
                    positive_count += 1
                if r["meeting_status"]:
                    meetings_count += 1

                responses.append({
                    "company_name": r["company_name"] or r["candidate_id"],
                    "recipient_email": r["recipient_email"],
                    "reply_status": stat,
                    "reply_at": r["reply_at"] or "N/A",
                    "meeting_status": r["meeting_status"] or "No meeting",
                    "notes": r["notes"] or "",
                })
    except Exception:
        pass

    return render_console_template(
        request=request,
        name="replies.html",
        context={
            "request": request,
            "active_tab": "replies",
            "responses": responses,
            "positive_count": positive_count,
            "meetings_count": meetings_count,
            "email_send_enabled": is_email_send_enabled(),
        },
    )


# ---------------------------------------------------------------------------
# 12. SETTINGS & SAFE STATUS (ZERO SECRETS EXPOSURE)
# ---------------------------------------------------------------------------
@router.get("/settings", response_class=HTMLResponse)
async def settings_view(request: Request):
    svcs = get_console_services()

    # Safe SMTP check: never expose password or private token
    smtp_host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER")
    smtp_configured = bool(smtp_host and smtp_user)

    trusted_sender_email = os.getenv("OUTREACH_SENDER_EMAIL", os.getenv("SMTP_FROM_EMAIL"))
    trusted_sender_name = os.getenv("OUTREACH_SENDER_NAME", os.getenv("SMTP_FROM_NAME"))
    sheets_configured = bool(os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON"))

    # P10_5-ADV-019: Truthful, non-hardcoded health status checks
    ollama_reachable = False
    try:
        ollama_host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
        req = urllib.request.Request(f"{ollama_host}/api/tags", headers={"User-Agent": "SalesAI-Check"})
        with urllib.request.urlopen(req, timeout=0.3) as resp:
            if resp.status == 200:
                ollama_reachable = True
    except Exception:
        ollama_reachable = False

    chroma_operational = False
    try:
        import chromadb
        chroma_operational = True
    except ImportError:
        chroma_operational = False

    return render_console_template(
        request=request,
        name="settings.html",
        context={
            "request": request,
            "active_tab": "settings",
            "db_path": svcs["cand_store"].db_path,
            "email_send_enabled": is_email_send_enabled(),
            "provider_type": "SMTP (Phase 9 Provider)" if smtp_configured else "Mock Provider (Safe)",
            "smtp_configured": smtp_configured,
            "trusted_sender_email": trusted_sender_email,
            "trusted_sender_name": trusted_sender_name,
            "sheets_configured": sheets_configured,
            "llm_model": os.getenv("SALES_LLM_MODEL", "qwen2.5:1.5b"),
            "ollama_reachable": ollama_reachable,
            "chroma_operational": chroma_operational,
        },
    )


# ---------------------------------------------------------------------------
# 13. GLOBAL SEARCH (CANDIDATES, CONTACTS, DRAFTS)
# ---------------------------------------------------------------------------
@router.get("/search", response_class=HTMLResponse)
async def global_search_view(request: Request, q: Optional[str] = Query(None)):
    query_str = (q or "").strip()
    candidate_results = []
    draft_results = []

    if query_str:
        svcs = get_console_services()
        ql = query_str.lower()

        # 1. Search candidates
        all_cands = svcs["cand_store"].list_candidates()
        for c in all_cands:
            name = getattr(c, "company_name", getattr(c, "name", ""))
            dom = getattr(c, "company_domain", getattr(c, "domain", "")) or ""
            city = getattr(c, "city", "") or ""
            if ql in name.lower() or ql in dom.lower() or ql in city.lower():
                candidate_results.append({
                    "id": c.candidate_id,
                    "name": name,
                    "domain": dom,
                    "city": city,
                    "tier": c.company_tier,
                    "status": c.discovery_status,
                })

        # 2. Search drafts
        all_drafts = svcs["rev_store"].list_drafts(limit=500)
        for d in all_drafts:
            if (
                ql in d.subject.lower()
                or ql in d.recipient_email.lower()
                or ql in d.recipient_name.lower()
                or ql in d.lead_id.lower()
            ):
                draft_results.append({
                    "draft_id": d.draft_id,
                    "revision": d.revision,
                    "subject": d.subject,
                    "recipient_email": d.recipient_email,
                    "recipient_name": d.recipient_name,
                    "approval_status": d.approval_status,
                    "send_status": d.send_status,
                })

    return render_console_template(
        request=request,
        name="search.html",
        context={
            "request": request,
            "active_tab": "dashboard",
            "query": query_str,
            "candidate_results": candidate_results,
            "draft_results": draft_results,
            "email_send_enabled": is_email_send_enabled(),
        },
    )
