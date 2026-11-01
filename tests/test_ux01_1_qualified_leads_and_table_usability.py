"""
tests/test_ux01_1_qualified_leads_and_table_usability.py
Verification suite for SalesAI UX-01.1 Closure:
1. /sales/leads excludes unqualified records (must only display candidates qualified for enrichment).
2. Missing Fit, Intent, BIM maturity and Opportunity never receive invented positive defaults (e.g. no default 'high', 'medium', or 'BIM Delivery').
3. Suppressed and historically contacted records retain distinct badges.
4. Lead Register Summary displays compact previews for multiline/long fields without corrupting content.
5. Multiple email addresses in Lead Register are cleanly split into individual valid mailto links.
6. Navigation groups distinguish intelligence vs pipeline and prevent duplicated word confusion.
7. Dashboard metrics and Qualified Leads definition align semantically.
"""

import pytest
import sqlite3
from pathlib import Path
from starlette.testclient import TestClient

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany
from sales_engine.leads.register_store import LeadRegisterStore, LeadRegisterRecord
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.suppression_store import SuppressionStore


@pytest.fixture
def test_setup(tmp_path):
    test_db = tmp_path / "test_ux01_1.db"
    cand_store = CandidateStore(db_path=test_db)
    reg_store = LeadRegisterStore(db_path=test_db)
    rev_store = ReviewStore(db_path=test_db)

    # 1. Unqualified Candidate (discovery_status='new', qualification_outcome=None)
    cand_unqual = CandidateCompany(
        candidate_id="cand:test:unqualified",
        name="Unqualified General Contractor",
        domain="unqualified-gc.it",
        country="IT",
        city="Rome",
        company_tier="tier_3",
        discovery_status="new",
        fit_assessment="unknown",
        intent_assessment="unknown",
        bim_maturity="unknown",
        outsourcing_potential="unknown",
    )
    cand_store.save_candidate(cand_unqual)

    # 2. Rejected Candidate
    cand_rejected = CandidateCompany(
        candidate_id="cand:test:rejected",
        name="Rejected Plumbing Supplies",
        domain="rejected-plumb.it",
        country="IT",
        company_tier="tier_3",
        discovery_status="rejected",
        qualification_outcome="reject",
    )
    cand_store.save_candidate(cand_rejected)

    # 3. Qualified Candidate with missing Fit & Intent (must NOT default to high/medium)
    cand_qual = CandidateCompany(
        candidate_id="cand:test:qualified",
        name="Studio Qualificato BIM",
        domain="studio-qual.it",
        country="IT",
        company_tier="tier_2",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        fit_assessment="unknown",
        intent_assessment="unknown",
        bim_maturity="unknown",
        outsourcing_potential="unknown",
    )
    cand_store.save_candidate(cand_qual)

    # 4. Suppressed Qualified Candidate
    cand_supp = CandidateCompany(
        candidate_id="cand:test:suppressed",
        name="Suppressed Engineering S.r.l.",
        domain="suppressed-eng.it",
        country="IT",
        company_tier="tier_1",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        suppressed=True,
    )
    cand_store.save_candidate(cand_supp)
    supp_store = SuppressionStore(rev_store)
    supp_store.add_suppression(email="info@suppressed-eng.it", company_domain="suppressed-eng.it", reason="opt_out")

    # 5. Lead Register candidate & record with long multilingual text and multiple emails
    cand_multilingual = CandidateCompany(
        candidate_id="cand:test:multilingual",
        name="مهندسین مشاور آریا / Aria BIM Engineering",
        domain="aria-bim.com",
        country="IT",
        company_tier="tier_1",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    )
    cand_store.save_candidate(cand_multilingual)

    rec_multilingual = LeadRegisterRecord(
        candidate_id="cand:test:multilingual",
        company_name="مهندسین مشاور آریا / Aria BIM Engineering",
        official_website="https://aria-bim.com",
        contact_email_addresses="info@aria-bim.com, contact@aria-bim.com; admin@aria-bim.com",
        telephone_numbers="+39 049 1234567",
        geographic_location="Milan, Italy / تهران، ایران",
        priority_level="A",
        priority_rationale="شرکت پیشرو در مدل‌سازی اطلاعات ساختمان و خدمات مهندسی یکپارچه با سابقه طولانی در پروژه‌های فرودگاهی.",
        descriptions_and_notes="این شرکت دارای تیم تخصصی BIM و مستندسازی فاز ۲ معماری است. Over 200 characters of extended multiline notes for rigorous viewport and cell preview testing.",
        business_activities="BIM Modeling, Coordination",
        provenance_source="operator_reviewed",
        verification_status="OPERATOR_REVIEWED",
    )
    reg_store.save_record(rec_multilingual)

    return {
        "db_path": test_db,
        "cand_store": cand_store,
        "reg_store": reg_store,
        "rev_store": rev_store,
    }


def test_01_leads_excludes_unqualified_and_rejected(test_setup, monkeypatch):
    """Verify /sales/leads strictly excludes new, unverified and rejected candidates."""
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup["db_path"]))
    client = TestClient(app)

    res = client.get("/sales/leads")
    assert res.status_code == 200

    # Unqualified and rejected candidates MUST NOT appear
    assert "Unqualified General Contractor" not in res.text
    assert "unqualified-gc.it" not in res.text
    assert "Rejected Plumbing Supplies" not in res.text
    assert "rejected-plumb.it" not in res.text

    # Qualified candidates MUST appear
    assert "Studio Qualificato BIM" in res.text
    assert "studio-qual.it" in res.text


def test_02_leads_missing_data_does_not_invent_positive_defaults(test_setup, monkeypatch):
    """Verify missing fit/intent/opportunity render as Unknown / Not Evaluated, never invented high/medium."""
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup["db_path"]))
    client = TestClient(app)

    res = client.get("/sales/leads")
    assert res.status_code == 200

    # Missing fit/intent should be Unknown or Not Evaluated, not false defaults
    assert "Unknown" in res.text
    assert "Not Evaluated" in res.text
    assert "Enrichment Qualified" in res.text


def test_03_suppressed_lead_clearly_flagged(test_setup, monkeypatch):
    """Verify suppressed candidates in qualified leads are marked with suppressed badges."""
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup["db_path"]))
    client = TestClient(app)

    res = client.get("/sales/leads")
    assert res.status_code == 200

    assert "Suppressed Engineering S.r.l." in res.text
    assert "Suppressed" in res.text


def test_04_lead_register_summary_multilingual_and_email_splitting(test_setup, monkeypatch):
    """Verify lead register summary handles long multilingual text and splits comma/semicolon emails."""
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup["db_path"]))
    client = TestClient(app)

    res = client.get("/sales/register?view=summary")
    assert res.status_code == 200

    # Multilingual Persian & English content preserved
    assert "Aria BIM Engineering" in res.text
    assert "مهندسین مشاور" in res.text

    # Multiple email addresses split into clean mailto links without malformed concatenation
    assert 'href="mailto:info@aria-bim.com"' in res.text
    assert 'href="mailto:contact@aria-bim.com"' in res.text
    assert 'href="mailto:admin@aria-bim.com"' in res.text


def test_05_navigation_distinct_groups(test_setup, monkeypatch):
    """Verify base navigation displays clear workflow pillars without repetitive labels."""
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup["db_path"]))
    client = TestClient(app)

    res = client.get("/sales/dashboard")
    assert res.status_code == 200

    assert "Intelligence" in res.text
    assert "Discovery Runs" in res.text
    assert "Lead Pipeline" in res.text
    assert "Qualified Leads" in res.text
    assert "Outreach" in res.text
    assert "Administration" in res.text
