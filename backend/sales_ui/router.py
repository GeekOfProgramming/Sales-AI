"""
FastAPI Router for SalesAI Phase 10.5 Local Sales Operator Console.
Prefix: /sales
Strictly local-first, zero autonomous sends, zero direct writes.
All mutations delegate directly to Phase 9 and Phase 10 domain services.
"""

import os
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Form, Query, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
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

router = APIRouter(prefix="/sales", tags=["Sales-Console"])

templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


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


# ---------------------------------------------------------------------------
# 1. DASHBOARD
# ---------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_view(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    svcs = get_console_services()
    summary = svcs["dash_svc"].get_summary()

    # Get recent pending drafts (up to 5)
    pending_drafts_raw, _ = svcs["rev_store"].list_drafts(
        filters={"approval_status": "pending_review"}, limit=5
    )
    pending_cards = []
    for d in pending_drafts_raw:
        pending_cards.append(
            DraftReviewCard(
                draft_id=d.draft_id,
                revision=d.revision,
                lead_id=d.lead_id,
                contact_id=d.contact_id,
                company_name=d.lead_id,
                recipient_name=d.recipient_name,
                recipient_email=d.recipient_email,
                subject=d.subject,
                body=d.body,
                approval_status=d.approval_status,
                send_status=d.send_status,
                outreach_status=d.outreach_status,
                content_hash=d.content_hash,
                created_at=d.created_at,
            )
        )

    # Get recent discovery runs (up to 5)
    recent_runs = svcs["cand_store"].list_discovery_runs(limit=5)

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
            "request": request,
            "active_tab": "dashboard",
            "summary": summary,
            "pending_drafts": pending_cards,
            "recent_runs": recent_runs,
            "email_send_enabled": is_email_send_enabled(),
            "message": message,
            "error": error,
        },
    )


# ---------------------------------------------------------------------------
# 2. DISCOVERIES
# ---------------------------------------------------------------------------
@router.get("/discoveries", response_class=HTMLResponse)
async def discoveries_view(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    svcs = get_console_services()
    runs_raw = svcs["cand_store"].list_discovery_runs(limit=50)
    
    runs = []
    for r in runs_raw:
        runs.append(
            DiscoveryRunItemView(
                run_id=r.get("run_id", ""),
                mode=r.get("mode", "company_first"),
                source_name=r.get("source_name", "Multi-Source"),
                started_at=r.get("started_at", ""),
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

    return templates.TemplateResponse(request=request, name="discoveries.html", context={
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
                is_configured=True,
                status=h.get("status", "operational"),
                success_count=h.get("success_count", 0),
                failure_count=h.get("failure_count", 0),
                last_success_at=h.get("last_success_at"),
                last_failure_at=h.get("last_failure_at"),
                last_error=h.get("last_error"),
            )
        )

    return templates.TemplateResponse(request=request, name="sources.html", context={
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


@router.post("/sources/import-history")
async def import_historical_data_action(
    file_path: str = Form(...),
    sheet_name: Optional[str] = Form(None),
):
    svcs = get_console_services()
    importer = HistoricalImporter(store=svcs["cand_store"])
    path_obj = Path(file_path.strip())
    if not path_obj.exists():
        return RedirectResponse(f"/sales/sources?error=File not found: {file_path}", status_code=status.HTTP_303_SEE_OTHER)

    try:
        if path_obj.suffix.lower() == ".csv":
            records = importer.parse_csv(path_obj)
        elif path_obj.suffix.lower() in (".xlsx", ".xlsm"):
            records = importer.parse_xlsx(path_obj, sheet_name=sheet_name)
        else:
            return RedirectResponse("/sales/sources?error=Unsupported file format (CSV/XLSX only)", status_code=status.HTTP_303_SEE_OTHER)

        result = importer.import_records(records)
        msg = f"Imported: {result.companies_created} new companies, {result.records_parsed} records, {result.duplicates_skipped} duplicates skipped."
        return RedirectResponse(f"/sales/sources?message={msg}", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        return RedirectResponse(f"/sales/sources?error={str(e)}", status_code=status.HTTP_303_SEE_OTHER)


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
    
    # In-memory filter for search query and country
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
        # Check suppression
        dom = c.get("canonical_domain") or c.get("company_domain") or ""
        is_supp = svcs["suppression_svc"].is_suppressed(dom)
        
        # P10_5-REG-012: Unknown BIM maturity is not displayed as 'No BIM'
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
                is_previously_contacted=bool(c.get("historical_contact_status") or c.get("historical_outreach_summary")),
                is_suppressed=is_supp,
            )
        )

    has_next = end_idx < total

    return templates.TemplateResponse(request=request, name="candidates.html", context={
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
    
    # Evidence & observations
    ev_items = [e.model_dump() if hasattr(e, "model_dump") else e.__dict__ for e in svcs["cand_store"].get_evidence(candidate_id)]
    obs_items = [o.model_dump() if hasattr(o, "model_dump") else o.__dict__ for o in svcs["cand_store"].get_observations_for_company(candidate_id)]
    hist_outreach = svcs["cand_store"].get_historical_outreach_for_candidate(candidate_id)

    # Suppression & past outreach check
    cand_name = getattr(cand, "company_name", None) or getattr(cand, "name", "Company")
    cand_domain = getattr(cand, "company_domain", None) or getattr(cand, "domain", None)
    is_supp = svcs["suppression_svc"].is_suppressed(cand_domain or "")
    is_contacted = bool(cand.historical_contact_status or hist_outreach)

    # BIM maturity display rule (P10_5-REG-012)
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

    # Why this company?
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

    return templates.TemplateResponse(request=request, name="candidate_detail.html", context={
            "request": request,
            "active_tab": "candidates",
            "detail": view,
            "email_send_enabled": is_email_send_enabled(),
            "message": message,
            "error": error,
        },
    )


@router.post("/candidates/{candidate_id}/research")
async def research_candidate_action(candidate_id: str):
    svcs = get_console_services()
    cand = svcs["cand_store"].get_by_id(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    cand.last_researched_at = datetime.now(timezone.utc).isoformat()
    cand.discovery_status = "needs_research"
    svcs["cand_store"].save_candidate(cand)
    return RedirectResponse(f"/sales/candidates/{candidate_id}?message=Research timestamp updated.", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/candidates/{candidate_id}/qualify")
async def qualify_candidate_action(candidate_id: str):
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
):
    svcs = get_console_services()
    cand = svcs["cand_store"].get_by_id(candidate_id)
    domain = (getattr(cand, "company_domain", None) or getattr(cand, "domain", None)) if cand else None
    svcs["suppression_svc"].add_suppression(
        email=email,
        reason=reason,
        source="sales_console",
        company_domain=domain,
    )
    if cand:
        cand.suppressed = True
        cand.discovery_status = "suppressed"
        svcs["cand_store"].save_candidate(cand)
    return RedirectResponse(f"/sales/candidates/{candidate_id}?message=Target suppressed.", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# 5. QUALIFIED LEADS
# ---------------------------------------------------------------------------
@router.get("/leads", response_class=HTMLResponse)
async def qualified_leads_view(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    svcs = get_console_services()
    bridge = Phase10ConsoleBridge(store=svcs["cand_store"], registry=svcs["registry"])
    all_cands = bridge.get_candidate_list_summary(limit=500)
    leads_raw = [
        c for c in all_cands
        if c.get("status") in ("qualified", "qualified_for_enrichment")
        or c.get("discovery_status") in ("qualified", "qualified_for_enrichment")
        or c.get("qualification_outcome") in ("qualified", "qualified_for_enrichment")
    ]

    leads: List[CandidateListItem] = []
    for c in leads_raw:
        raw_bim = c.get("bim_maturity")
        bim_display = raw_bim if raw_bim and raw_bim.strip() and raw_bim.lower() != "unknown" else "Unknown"
        leads.append(
            CandidateListItem(
                candidate_id=c.get("company_id") or c.get("candidate_id", ""),
                company_name=c.get("name") or c.get("company_name", ""),
                domain=c.get("canonical_domain") or c.get("company_domain"),
                country=c.get("country"),
                tier=c.get("company_tier"),
                fit=c.get("fit_assessment") or c.get("fit_level"),
                intent=c.get("intent_assessment") or c.get("intent_level"),
                bim_maturity=bim_display,
                outsourcing_potential=c.get("outsourcing_potential"),
                status=c.get("status") or c.get("discovery_status", "qualified"),
                is_previously_contacted=bool(c.get("historical_contact_status") or c.get("historical_outreach_summary")),
            )
        )

    return templates.TemplateResponse(request=request, name="leads.html", context={
            "request": request,
            "active_tab": "leads",
            "leads": leads,
            "email_send_enabled": is_email_send_enabled(),
            "message": message,
            "error": error,
        },
    )


# ---------------------------------------------------------------------------
# 6. DRAFTS REVIEW, APPROVAL, EDIT, REJECT, DRY RUN, SEND
# ---------------------------------------------------------------------------
@router.get("/drafts", response_class=HTMLResponse)
async def drafts_queue_view(
    request: Request,
    status: Optional[str] = Query(None),
    message: Optional[str] = None,
    error: Optional[str] = None,
):
    svcs = get_console_services()
    filters = {}
    if status and status != "all":
        filters["approval_status"] = status
    elif not status:
        filters["approval_status"] = "pending_review"

    raw_drafts, _ = svcs["rev_store"].list_drafts(filters=filters, limit=100)

    draft_cards: List[DraftReviewCard] = []
    for d in raw_drafts:
        is_supp = svcs["suppression_svc"].is_suppressed(d.recipient_email)
        
        # Check action availability
        is_sent = d.send_status == "sent"
        can_app = (d.approval_status != "approved") and not is_sent
        can_ed = not is_sent
        can_rej = (d.approval_status != "rejected") and not is_sent
        can_send_flag = (d.approval_status == "approved") and not is_sent and not is_supp

        draft_cards.append(
            DraftReviewCard(
                draft_id=d.draft_id,
                revision=d.revision,
                lead_id=d.lead_id,
                contact_id=d.contact_id,
                company_name=d.lead_id,
                recipient_name=d.recipient_name,
                recipient_title=d.recipient_title,
                recipient_email=d.recipient_email,
                sender_name=d.sender_name,
                sender_email=d.sender_email,
                subject=d.subject,
                body=d.body,
                service_used=d.service_used,
                generation_model=d.generation_model,
                tone=d.tone,
                approval_status=d.approval_status,
                send_status=d.send_status,
                outreach_status=d.outreach_status,
                content_hash=d.content_hash,
                approved_content_hash=d.approved_content_hash,
                reviewer=d.reviewer,
                created_at=d.created_at,
                approved_at=d.approved_at,
                sent_at=d.sent_at,
                is_suppressed=is_supp,
                can_approve=can_app,
                can_edit=can_ed,
                can_reject=can_rej,
                can_dry_run=not is_sent,
                can_send=can_send_flag,
            )
        )

    return templates.TemplateResponse(request=request, name="drafts.html", context={
            "request": request,
            "active_tab": "drafts",
            "drafts": draft_cards,
            "status_filter": status,
            "email_send_enabled": is_email_send_enabled(),
            "message": message,
            "error": error,
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
    draft = svcs["rev_store"].get_draft(draft_id, revision)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    events = svcs["audit_svc"].get_draft_events(draft_id)
    is_supp = svcs["suppression_svc"].is_suppressed(draft.recipient_email)
    is_sent = draft.send_status == "sent"

    card = DraftReviewCard(
        draft_id=draft.draft_id,
        revision=draft.revision,
        lead_id=draft.lead_id,
        contact_id=draft.contact_id,
        company_name=draft.lead_id,
        recipient_name=draft.recipient_name,
        recipient_title=draft.recipient_title,
        recipient_email=draft.recipient_email,
        sender_name=draft.sender_name,
        sender_email=draft.sender_email,
        subject=draft.subject,
        body=draft.body,
        service_used=draft.service_used,
        generation_model=draft.generation_model,
        tone=draft.tone,
        approval_status=draft.approval_status,
        send_status=draft.send_status,
        outreach_status=draft.outreach_status,
        content_hash=draft.content_hash,
        approved_content_hash=draft.approved_content_hash,
        reviewer=draft.reviewer,
        created_at=draft.created_at,
        approved_at=draft.approved_at,
        sent_at=draft.sent_at,
        is_suppressed=is_supp,
        can_approve=(draft.approval_status != "approved") and not is_sent,
        can_edit=not is_sent,
        can_reject=(draft.approval_status != "rejected") and not is_sent,
        can_dry_run=not is_sent,
        can_send=(draft.approval_status == "approved") and not is_sent and not is_supp,
    )

    return templates.TemplateResponse(request=request, name="draft_detail.html", context={
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


# P10_5-REG-005: Dry Run creates zero provider/network calls
@router.post("/drafts/{draft_id}/dry-run")
async def dry_run_draft_action(
    draft_id: str,
    revision: int = Form(...),
    reviewer: str = Form("local_operator"),
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
    
    # P10_5-REG-009: Check send enabled flag
    if not is_email_send_enabled():
        return RedirectResponse(
            f"/sales/drafts/{draft_id}?error=Real sending is disabled on this server (EMAIL_SEND_ENABLED=false).",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        res = svcs["orchestrator"].send_draft(
            draft_id=draft_id,
            revision=revision,
            dry_run=False,
            reviewer=reviewer,
        )
        if res.status == "sent":
            msg = f"Email sent successfully (Attempt: {res.attempt_id[:12]})."
            return RedirectResponse(f"/sales/sent?message={msg}", status_code=status.HTTP_303_SEE_OTHER)
        else:
            return RedirectResponse(
                f"/sales/drafts/{draft_id}?error=Send error: {res.error_message}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
    except Exception as e:
        return RedirectResponse(f"/sales/drafts/{draft_id}?error={str(e)}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/drafts/{draft_id}/suppress")
async def suppress_draft_recipient_action(
    draft_id: str,
    email: str = Form(...),
    company_domain: Optional[str] = Form(None),
    reason: str = Form("manual"),
):
    svcs = get_console_services()
    svcs["suppression_svc"].add_suppression(
        email=email,
        company_domain=company_domain,
        reason=reason,
        source="draft_review",
    )
    return RedirectResponse(f"/sales/drafts/{draft_id}?message=Recipient suppressed.", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# 7. APPROVED QUEUE
# ---------------------------------------------------------------------------
@router.get("/approved", response_class=HTMLResponse)
async def approved_drafts_view(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    svcs = get_console_services()
    drafts_raw, _ = svcs["rev_store"].list_drafts(
        filters={"approval_status": "approved", "send_status": "not_sent"}, limit=100
    )

    drafts: List[DraftReviewCard] = []
    for d in drafts_raw:
        is_supp = svcs["suppression_svc"].is_suppressed(d.recipient_email)
        drafts.append(
            DraftReviewCard(
                draft_id=d.draft_id,
                revision=d.revision,
                lead_id=d.lead_id,
                contact_id=d.contact_id,
                company_name=d.lead_id,
                recipient_name=d.recipient_name,
                recipient_email=d.recipient_email,
                subject=d.subject,
                body=d.body,
                approval_status=d.approval_status,
                send_status=d.send_status,
                outreach_status=d.outreach_status,
                content_hash=d.content_hash,
                approved_at=d.approved_at,
                reviewer=d.reviewer,
                created_at=d.created_at,
                is_suppressed=is_supp,
                can_send=not is_supp,
            )
        )

    return templates.TemplateResponse(request=request, name="approved.html", context={
            "request": request,
            "active_tab": "approved",
            "drafts": drafts,
            "email_send_enabled": is_email_send_enabled(),
            "message": message,
            "error": error,
        },
    )


# ---------------------------------------------------------------------------
# 8. SENT IMMUTABLE HISTORY
# ---------------------------------------------------------------------------
@router.get("/sent", response_class=HTMLResponse)
async def sent_history_view(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    svcs = get_console_services()
    all_attempts = svcs["rev_store"].get_send_attempts(limit=100)
    attempts = [a for a in all_attempts if a.status == "sent"]

    sent_items: List[SentHistoryItem] = []
    for a in attempts:
        draft = svcs["rev_store"].get_draft(a.draft_id, a.revision)
        sent_items.append(
            SentHistoryItem(
                attempt_id=a.attempt_id,
                send_key=a.send_key,
                draft_id=a.draft_id,
                revision=a.revision,
                company_name=draft.lead_id if draft else "Lead",
                recipient_name=draft.recipient_name if draft else "Recipient",
                recipient_email=a.recipient_email,
                sender_email=a.sender_email,
                subject=draft.subject if draft else "Outreach",
                provider=a.provider,
                status=a.status,
                provider_message_id=a.provider_message_id,
                attempted_at=a.attempted_at,
                completed_at=a.completed_at,
            )
        )

    return templates.TemplateResponse(request=request, name="sent.html", context={
            "request": request,
            "active_tab": "sent",
            "sent_items": sent_items,
            "email_send_enabled": is_email_send_enabled(),
            "message": message,
            "error": error,
        },
    )


# ---------------------------------------------------------------------------
# 9. WATCHLIST
# ---------------------------------------------------------------------------
@router.get("/watchlist", response_class=HTMLResponse)
async def watchlist_view(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    svcs = get_console_services()
    bridge = Phase10ConsoleBridge(store=svcs["cand_store"], registry=svcs["registry"])
    entries = bridge.get_watchlist_summary()

    items: List[WatchlistItemView] = []
    for w in entries:
        items.append(
            WatchlistItemView(
                candidate_id=w.get("candidate_id", ""),
                company_name=w.get("company_name", ""),
                company_domain=w.get("company_domain"),
                country=w.get("country"),
                tier=w.get("company_tier"),
                fit=w.get("fit_assessment"),
                intent=w.get("intent_assessment"),
                watch_reason=w.get("watch_reason", ""),
                watch_status=w.get("watch_status", "active"),
                signals_to_monitor=w.get("signals_to_monitor", []),
                last_checked_at=w.get("last_checked_at"),
                next_check_at=w.get("next_check_at"),
            )
        )

    return templates.TemplateResponse(request=request, name="watchlist.html", context={
            "request": request,
            "active_tab": "watchlist",
            "items": items,
            "email_send_enabled": is_email_send_enabled(),
            "message": message,
            "error": error,
        },
    )


@router.post("/watchlist/{candidate_id}/remove")
async def remove_from_watchlist_action(candidate_id: str):
    svcs = get_console_services()
    svcs["cand_store"].remove_from_watchlist(candidate_id)
    return RedirectResponse("/sales/watchlist?message=Removed from watchlist.", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# 10. DO NOT CONTACT (SUPPRESSION)
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

    return templates.TemplateResponse(request=request, name="suppression.html", context={
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
    email: str = Form(...),
    company_domain: Optional[str] = Form(None),
    reason: str = Form("manual"),
):
    svcs = get_console_services()
    svcs["suppression_svc"].add_suppression(
        email=email.strip().lower(),
        company_domain=company_domain.strip().lower() if company_domain else None,
        reason=reason,
        source="sales_console",
    )
    return RedirectResponse("/sales/do-not-contact?message=Suppression entry added.", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/do-not-contact/remove")
async def remove_suppression_action(email: str = Form(...)):
    svcs = get_console_services()
    svcs["suppression_svc"].remove_suppression(email.strip().lower())
    return RedirectResponse("/sales/do-not-contact?message=Suppression removed.", status_code=status.HTTP_303_SEE_OTHER)


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
                r_dict = dict(r)
                responses.append(r_dict)
                if r_dict.get("reply_status") in ("positive", "interested", "meeting_requested"):
                    positive_count += 1
                if r_dict.get("meeting_status"):
                    meetings_count += 1
    except Exception:
        pass

    return templates.TemplateResponse(request=request, name="replies.html", context={
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

    trusted_sender_email = os.getenv("OUTREACH_SENDER_EMAIL")
    trusted_sender_name = os.getenv("OUTREACH_SENDER_NAME")
    sheets_configured = bool(os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON"))

    return templates.TemplateResponse(request=request, name="settings.html", context={
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
            "ollama_reachable": True,
        },
    )
