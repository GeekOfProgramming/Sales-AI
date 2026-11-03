import pytest
import re
import sqlite3
from bs4 import BeautifulSoup
from starlette.requests import Request
from jinja2 import Environment, FileSystemLoader

from backend.sales_ui.router import (
    is_malformed_contact_email,
    is_suspect_job_advertisement_url,
    validate_research_finalization,
    compute_field_checksum,
    verify_candidate_field_action,
)
from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord
from sales_engine.research.research_store import ResearchSnapshotStore, ResearchSnapshot, CompanyAnalysisRecord


def test_helpers_malformed_contact_email():
    # Clean email
    assert is_malformed_contact_email("info@renerwave.com") is False
    assert is_malformed_contact_email("contact@company.it, sales@company.it") is False

    # Scraped trailing noise
    assert is_malformed_contact_email("info@renerwave.comParla") is True
    assert is_malformed_contact_email("info@renerwave.com.Per") is True
    assert is_malformed_contact_email("hello@domain.itScrivici") is True
    assert is_malformed_contact_email("not-an-email") is True


def test_helpers_suspect_job_advertisement_url():
    # Legitimate career URLs
    assert is_suspect_job_advertisement_url("https://renerwave.com/carriere") is False
    assert is_suspect_job_advertisement_url("https://renerwave.com/lavora-con-noi") is False
    assert is_suspect_job_advertisement_url("https://renerwave.com/jobs") is False

    # News / CSRD articles misclassified as jobs
    assert is_suspect_job_advertisement_url("https://renerwave.com/csrd-dal-2026-la-sostenibilita-diventa-un-obbligo-e-unopportunita-anche-per-le-pmi/") is True
    assert is_suspect_job_advertisement_url("https://renerwave.com/blog/nuova-normativa-sostenibilita") is True
    assert is_suspect_job_advertisement_url("https://renerwave.com/news/eventi-2026") is True


def test_renerwave_quarantined_verification_markup_and_non_nested_forms():
    env = Environment(loader=FileSystemLoader("backend/sales_ui/templates"))
    tmpl = env.get_template("company_research_review.html")

    cid = "cand:domain:renerwave.com"
    cand = CandidateCompany(candidate_id=cid, company_name="RENERWAVE", domain="renerwave.com")
    current_reg = LeadRegisterRecord(
        candidate_id=cid,
        company_name="RENERWAVE S.R.L.",
        official_website="https://renerwave.com/",
        business_activities="Not Found",
        contact_email_addresses="info@renerwave.comParla",
        job_advertisement_links="https://renerwave.com/csrd-dal-2026-la-sostenibilita-diventa-un-obbligo-e-unopportunita-anche-per-le-pmi/",
    )
    analysis_fields = {
        "company_name": {"value": "RENERWAVE S.R.L.", "quarantined": False, "confidence": 0.95},
        "official_website": {"value": "https://renerwave.com/", "quarantined": True, "quarantine_reason": "Excerpt mismatch", "confidence": 0.90},
        "business_activities": {"value": "Sviluppo impianti fotovoltaici", "quarantined": True, "quarantine_reason": "Excerpt not found", "confidence": 0.85},
    }

    render_ctx = {
        "candidate": cand,
        "current_record": current_reg,
        "proposed_record": current_reg,
        "analysis_fields": analysis_fields,
        "csrf_token": "test_csrf_token_xyz",
        "is_finalizable": False,
        "finalization_checks": {
            "identity_valid": True,
            "website_valid": True,
            "activities_valid": False,
            "evidence_valid": False,
            "reviews_valid": False,
            "quarantine_clean": False,
        },
        "finalization_errors": ["Quarantined AI proposal present"],
        "historical_snapshots": [],
        "research": {"evidence_items": {}, "supplier_signals": [], "career_opportunities": [], "opportunity_urls": []},
        "field_reviews": {
            "official_website": FieldReviewRecord(candidate_id=cid, field_name="official_website", approved_value="https://renerwave.com/", source_type="operator_manual", value_checksum=compute_field_checksum("https://renerwave.com/"))
        },
        "latest_analysis": CompanyAnalysisRecord(
            analysis_id="an_1",
            candidate_id=cid,
            snapshot_id="snap_1",
            provider="google",
            model="gemini-2.5-flash",
            created_at="2026-11-03T18:00:00",
            status="SUCCESS",
        ),
        "is_malformed_contact_email": is_malformed_contact_email(current_reg.contact_email_addresses),
        "is_suspect_job_advertisement": is_suspect_job_advertisement_url(current_reg.job_advertisement_links),
    }

    rendered_html = tmpl.render(render_ctx)
    soup = BeautifulSoup(rendered_html, "html.parser")

    # 1. No nested forms
    for f in soup.find_all("form"):
        assert len(f.find_all("form")) == 0, f"Found nested form inside {f}"

    # 2. Main save form exists
    save_form = soup.find("form", action=f"/sales/candidates/{cid}/research/save")
    assert save_form is not None

    # 3. Independent Business Activities verify form exists outside save_form
    ba_form = soup.find("form", id="verify-business-activities-form")
    assert ba_form is not None
    assert ba_form["action"] == f"/sales/candidates/{cid}/verify-field"
    assert ba_form["method"].upper() == "POST"
    assert ba_form.find_parent("form") is None

    # Inputs inside the review table reference form="verify-business-activities-form"
    ba_val_input = soup.find("textarea", {"form": "verify-business-activities-form", "name": "verified_value"})
    assert ba_val_input is not None

    ba_src_input = soup.find("input", {"form": "verify-business-activities-form", "name": "source_url"})
    assert ba_src_input is not None

    # 4. Check warning badges
    assert "⚠️ Trailing Scraping Noise Detected" in rendered_html
    assert "⚠️ Flagged: Non-Job Article" in rendered_html


@pytest.mark.asyncio
async def test_renerwave_verify_business_activities_action_and_finalization(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_renerwave.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", test_db)

    cand_store = CandidateStore(db_path=test_db)
    reg_store = LeadRegisterStore(db_path=test_db)
    research_store = ResearchSnapshotStore(db_path=test_db)

    cid = "cand:domain:renerwave.com"
    cand = CandidateCompany(candidate_id=cid, company_name="RENERWAVE", domain="renerwave.com")
    cand_store.save_candidate(cand)

    reg_rec = LeadRegisterRecord(
        candidate_id=cid,
        record_number=318,
        company_name="RENERWAVE S.R.L.",
        official_website="https://renerwave.com/",
        business_activities="Not Found",
    )
    reg_store.save_record(reg_rec)

    # Pre-populate field review for company_name and official_website
    rev_name = FieldReviewRecord(
        candidate_id=cid,
        field_name="company_name",
        approved_value="RENERWAVE S.R.L.",
        value_checksum=compute_field_checksum("RENERWAVE S.R.L."),
        source_type="operator_manual",
    )
    rev_web = FieldReviewRecord(
        candidate_id=cid,
        field_name="official_website",
        approved_value="https://renerwave.com/",
        value_checksum=compute_field_checksum("https://renerwave.com/"),
        source_type="operator_manual",
        evidence_url="https://renerwave.com/",
    )
    reg_store.save_field_review(rev_name)
    reg_store.save_field_review(rev_web)

    # 1. Reject evidence URL from mismatching domain
    resp_bad = await verify_candidate_field_action(
        candidate_id=cid,
        field_name="business_activities",
        verified_value="Sviluppo di impianti fotovoltaici e soluzioni di efficienza energetica.",
        source_url="https://unrelated-domain.com/chi-siamo",
        operator_note="Domain mismatch test",
        csrf=None,
    )
    assert resp_bad.status_code == 303
    assert "error=" in resp_bad.headers["location"]
    assert "does" in resp_bad.headers["location"] and "not" in resp_bad.headers["location"] and "belong" in resp_bad.headers["location"]

    # 2. Accept evidence URL from candidate's official domain
    resp_good = await verify_candidate_field_action(
        candidate_id=cid,
        field_name="business_activities",
        verified_value="Sviluppo di impianti fotovoltaici e soluzioni di efficienza energetica rinnovabile.",
        source_url="https://renerwave.com/chi-siamo/",
        operator_note="Independently verified from company Chi Siamo page",
        csrf=None,
    )
    assert resp_good.status_code == 303
    assert "message=" in resp_good.headers["location"]
    assert "error=" not in resp_good.headers["location"]

    # Check updated record and reviews
    updated_rec = reg_store.get_record(cid)
    assert updated_rec.business_activities == "Sviluppo di impianti fotovoltaici e soluzioni di efficienza energetica rinnovabile."

    reviews = reg_store.get_field_reviews(cid)
    ba_rev = reviews.get("business_activities")
    assert ba_rev is not None
    assert ba_rev.source_type == "operator_manual"
    assert ba_rev.evidence_url == "https://renerwave.com/chi-siamo/"
    assert ba_rev.value_checksum == compute_field_checksum(updated_rec.business_activities)

    # 3. Check validate_research_finalization evaluates to True
    # Suppose analysis has quarantined official_website and business_activities
    analysis = CompanyAnalysisRecord(
        analysis_id="an_test",
        candidate_id=cid,
        snapshot_id="snap_1",
        provider="google",
        model="gemini-2.5-flash",
        created_at="2026-11-03T18:00:00",
        status="SUCCESS",
        proposed_fields={
            "official_website": {"value": "https://renerwave.com/", "quarantined": True, "quarantine_reason": "Excerpt mismatch"},
            "business_activities": {"value": "Sviluppo impianti fotovoltaici", "quarantined": True, "quarantine_reason": "Excerpt not found"},
        }
    )

    is_fin, errs, checks = validate_research_finalization(
        cand=cand,
        reg_record=updated_rec,
        field_reviews=reviews,
        latest_analysis=analysis,
    )
    assert is_fin is True
    assert len(errs) == 0
    assert checks["activities_valid"] is True
    assert checks["website_valid"] is True
    assert checks["identity_valid"] is True
    assert checks["quarantine_clean"] is True
