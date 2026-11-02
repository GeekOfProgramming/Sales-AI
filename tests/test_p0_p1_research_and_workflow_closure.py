"""
Regression Test Suite for P0/P1 Research Accuracy & Workflow Integrity Closure:
- Phase 1: Company Identity Extraction P0 (Energymep vs ESAC srl, domain matching, JSON-LD vs prose, partner mentions)
- Phase 2: Candidate–Draft Isolation P0 (Strict candidate_id check, legacy fallback only when candidate_id IS NULL)
- Phase 3: Lead Register Review Semantics P1 (Edit does not implicitly mark OPERATOR_REVIEWED; explicit review preserved)
- Phase 4: Python Compatibility P1 (from typing import Set verification)
"""
import pytest
import sqlite3
import tempfile
import os
from pathlib import Path

from sales_engine.research.company_research_service import (
    extract_deterministic_facts,
    perform_company_research,
    CompanyResearchResult,
    PageFetchResult,
)
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.schemas import StoredDraft
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.leads.register_models import LeadRegisterRecord
from backend.sales_ui.timeline_service import TimelineService
from backend.sales_ui.router import evaluate_research_readiness


# ==============================================================================
# Phase 1: Company Identity Extraction P0
# ==============================================================================

def test_company_identity_energymep_partner_sentence_fragment_not_promoted():
    """
    Energymep test case:
    Candidate: Energymep (energymep.it)
    Website text: "Lo studio è stato condotto assieme alla azienda ESAC srl per analizzare l'efficienza energetica."
    Result:
    - company_name must remain "Energymep" (or candidate name)
    - company_name must NOT become "assieme alla azienda ESAC srl" or "ESAC srl"
    - legal_identity should remain "Not Found" or unverified
    - evidence must NOT have VERIFIED_LEGAL_ENTITY for ESAC srl
    """
    pages = [
        PageFetchResult(
            url="https://www.energymep.it",
            page_type="home",
            status="FETCH_SUCCESS",
            title="Energymep | Progettazione Impianti e BIM",
            text_content="""
            Benvenuti in Energymep.
            Siamo una società specializzata nella progettazione di impianti meccanici, elettrici e speciali con metodologia BIM.
            Lo studio è stato condotto assieme alla azienda ESAC srl per analizzare l'efficienza energetica degli edifici.
            Contattaci per informazioni sui nostri servizi di consulenza ingegneristica.
            """,
            raw_html="<html><body><h1>Energymep</h1><p>Lo studio è stato condotto assieme alla azienda ESAC srl</p></body></html>",
        ),
        PageFetchResult(
            url="https://www.energymep.it/contatti",
            page_type="contact",
            status="FETCH_SUCCESS",
            title="Contatti | Energymep",
            text_content="""
            Energymep
            Email: info@energymep.it
            Telefono: +39 049 1234567
            Sede: Padova, Italia
            """,
            raw_html="<html><body><p>Energymep - info@energymep.it</p></body></html>",
        ),
    ]

    result = extract_deterministic_facts(
        pages=pages,
        candidate_id="cand:domain:energymep.it",
        company_name="Energymep",
        canonical_domain="energymep.it",
    )

    # 1. Authoritative candidate name must be preserved
    assert result.company_name == "Energymep", f"Expected 'Energymep' but got '{result.company_name}'"
    assert "ESAC" not in result.company_name
    assert "assieme" not in result.company_name.lower()

    # 2. Legal name must not be falsely adopted from unrelated partner mention
    assert result.legal_name in ("Not Found", None) or "ESAC" not in result.legal_name

    # 3. Evidence items must not validate ESAC as verified legal entity for Energymep
    if "company_name" in result.evidence_items:
        assert "ESAC" not in result.evidence_items["company_name"].extracted_value


def test_company_identity_matching_domain_promoted():
    """
    When legal entity matches the domain or candidate name (e.g. BS Progetti S.r.l. on bs-eng.net),
    it SHOULD be accurately promoted with high confidence.
    """
    pages = [
        PageFetchResult(
            url="https://www.bs-eng.net",
            page_type="home",
            status="FETCH_SUCCESS",
            title="BS Progetti - Ingegneria Integrata",
            text_content="""
            BS Progetti S.r.l.
            Via Roma 10, Vicenza
            P.IVA 01234567890
            """,
            raw_html="""<html><body>
            <p>BS Progetti S.r.l. Società di Ingegneria</p>
            <footer>© 2026 BS Progetti S.r.l. - P.IVA 01234567890</footer>
            </body></html>""",
        )
    ]

    result = extract_deterministic_facts(
        pages=pages,
        candidate_id="cand:domain:bs-eng.net",
        company_name="BS Progetti",
        canonical_domain="bs-eng.net",
    )

    assert "BS Progetti S.r.l." in result.company_name
    assert result.legal_name == "BS Progetti S.r.l."
    assert result.evidence_items["company_name"].validation_status == "VERIFIED_LEGAL_ENTITY"


def test_company_identity_jsonld_matching_domain_accepted():
    """
    JSON-LD Organization matching the domain tokens is accepted.
    """
    jsonld_html = """
    <html><head>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Corporation",
      "name": "Tekna Solutions S.p.A.",
      "url": "https://www.tekna-solutions.com"
    }
    </script>
    </head><body><p>Tekna Solutions</p></body></html>
    """
    pages = [
        PageFetchResult(
            url="https://www.tekna-solutions.com",
            page_type="home",
            status="FETCH_SUCCESS",
            title="Tekna Solutions",
            text_content="Tekna Solutions Ingegneria industriale e BIM.",
            raw_html=jsonld_html,
        )
    ]

    result = extract_deterministic_facts(
        pages=pages,
        candidate_id="cand:domain:tekna-solutions.com",
        company_name="Tekna Solutions",
        canonical_domain="tekna-solutions.com",
    )

    assert result.company_name == "Tekna Solutions S.p.A."
    assert result.legal_name == "Tekna Solutions S.p.A."


# ==============================================================================
# Phase 2: Candidate–Draft Isolation P0
# ==============================================================================

@pytest.fixture
def isolated_review_db():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_review_isolation.db")
    rs = ReviewStore(db_path=db_path)
    yield rs, db_path
    try:
        os.remove(db_path)
        os.rmdir(temp_dir)
    except Exception:
        pass


def test_candidate_draft_isolation_explicit_candidate_id_never_overridden_by_lead_id(isolated_review_db):
    """
    Reproduced Case:
    - Candidate A has id "cand:domain:energymep.it", domain "energymep.it"
    - Draft "draft:test-001" has explicit candidate_id="cand:domain:acme.com" but legacy lead_id="domain:energymep.it"
    - Querying Candidate A (candidate_id="cand:domain:energymep.it", lead_ids=["domain:energymep.it"])
      MUST NOT return draft:test-001 because draft:test-001 explicitly belongs to another candidate!
    """
    rs, db_path = isolated_review_db

    # Create draft owned explicitly by Candidate X ("cand:domain:acme.com")
    draft_x = StoredDraft(
        draft_id="draft:test-acme",
        revision=1,
        lead_id="domain:energymep.it",  # Suppose lead_id had a collision or was cross-referenced
        candidate_id="cand:domain:acme.com",  # Explicit owner!
        contact_id="cont-1",
        recipient_email="jane@acme.com",
        recipient_name="Jane Doe",
        subject="pyBIM Collaboration",
        body="Hello Jane",
        content_hash="hash_test_acme_123",
        approval_status="approved",
    )
    rs.save_draft(draft_x)

    # 1. Query for Candidate Energymep with candidate_id and lead_ids
    drafts_list, total = rs.list_drafts(filters={
        "candidate_id": "cand:domain:energymep.it",
        "lead_ids": ["domain:energymep.it"]
    })
    # Must be 0 because candidate_id does not match and candidate_id is NOT NULL!
    assert len(drafts_list) == 0, f"Cross-candidate draft leaked into Energymep! Found: {drafts_list}"

    # 2. Query for Acme (the true owner)
    acme_drafts, total_acme = rs.list_drafts(filters={
        "candidate_id": "cand:domain:acme.com",
    })
    assert len(acme_drafts) == 1
    assert acme_drafts[0].draft_id == "draft:test-acme"

    # 3. Verify TimelineService respects strict isolation
    cs = CandidateStore(db_path=db_path)
    ts = TimelineService(cs, rs)
    energymep_events = ts.get_company_timeline(candidate_id="cand:domain:energymep.it")
    # No draft event should leak into Energymep timeline
    draft_events = [ev for ev in energymep_events if ev.event_type.startswith("draft")]
    assert len(draft_events) == 0, f"TimelineService leaked cross-candidate draft! Events: {draft_events}"


def test_candidate_draft_isolation_legacy_fallback_when_candidate_id_is_null(isolated_review_db):
    """
    Legacy drafts created before candidate_id column was introduced have candidate_id = NULL.
    They should safely match by valid lead_ids.
    """
    rs, db_path = isolated_review_db

    # Directly insert a legacy draft with candidate_id = NULL
    with rs._get_connection() as conn:
        conn.execute("""
            INSERT INTO drafts (
                draft_id, revision, lead_id, candidate_id, contact_id,
                recipient_name, recipient_email, subject, body,
                approval_status, send_status, outreach_status, content_hash,
                created_at, updated_at
            ) VALUES (
                'draft:legacy-001', 1, 'domain:legacy.it', NULL, 'cont-leg',
                'Mario Rossi', 'mario@legacy.it', 'Legacy Subject', 'Body',
                'pending_review', 'not_sent', 'draft_ready', 'hash_legacy_123',
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
            )
        """)
        conn.commit()

    # Query for Candidate with lead_id matching legacy draft
    results, _ = rs.list_drafts(filters={
        "candidate_id": "cand:domain:legacy.it",
        "lead_ids": ["domain:legacy.it"]
    })
    assert len(results) == 1
    assert results[0].draft_id == "draft:legacy-001"

    # Query with another candidate that does not match
    unrelated, _ = rs.list_drafts(filters={
        "candidate_id": "cand:domain:other.it",
        "lead_ids": ["domain:other.it"]
    })
    assert len(unrelated) == 0


# ==============================================================================
# Phase 3: Lead Register Review Semantics P1
# ==============================================================================

def test_lead_register_save_does_not_implicitly_promote_to_operator_reviewed():
    """
    Phase 3: Saving or editing data does NOT implicitly mark an unreviewed record as OPERATOR_REVIEWED.
    An explicit operator review action/selection is required.
    """
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_reg_semantics.db")
    reg_store = LeadRegisterStore(db_path=db_path)

    # Initial extracted unreviewed record
    initial_rec = LeadRegisterRecord(
        candidate_id="cand:test-01",
        record_number=1,
        company_name="Alpha Engineering",
        official_website="https://alpha-eng.it",
        business_activities="BIM Design",
        verification_status="EXTRACTED",
        provenance_source="local_research_mvp",
    )
    reg_store.save_record(initial_rec)

    # Simulate saving edit without explicit verification_status override
    existing = reg_store.get_record("cand:test-01")
    assert existing.verification_status == "EXTRACTED"

    # Updating profile fields
    updated_rec = LeadRegisterRecord(
        candidate_id="cand:test-01",
        record_number=1,
        company_name="Alpha Engineering Srl",
        official_website="https://alpha-eng.it",
        business_activities="BIM Design and Engineering",
        verification_status=existing.verification_status,  # Preserved!
        provenance_source="operator_edited",
    )
    reg_store.save_record(updated_rec)

    reloaded = reg_store.get_record("cand:test-01")
    assert reloaded.verification_status == "EXTRACTED", "Status should not implicitly flip to OPERATOR_REVIEWED!"


def test_research_readiness_requires_explicit_reviewed_status():
    """
    Phase 3: evaluate_research_readiness returns 'review_required' for EXTRACTED records,
    and 'research_ready' only when explicitly OPERATOR_REVIEWED/VERIFIED with minimum info & evidence.
    """
    class MockCand:
        candidate_id = "cand:test-02"
        company_name = "Beta Engineering"
        company_domain = "beta-eng.it"
        business_activities = "Civil Engineering"

    # Case A: Record is EXTRACTED with evidence -> review_required
    extracted_rec = LeadRegisterRecord(
        candidate_id="cand:test-02",
        record_number=2,
        company_name="Beta Engineering",
        official_website="https://beta-eng.it",
        business_activities="Civil Engineering",
        verification_status="EXTRACTED",
        evidence_urls=["https://beta-eng.it"],
        provenance_source="local_research_mvp",
    )
    readiness_extracted = evaluate_research_readiness(MockCand(), prefetched_reg_record=extracted_rec, has_prefetched=True)
    assert readiness_extracted["research_status"] == "review_required"
    assert readiness_extracted["verification_status"] == "EXTRACTED"

    # Case B: Explicitly OPERATOR_REVIEWED with minimum info & evidence -> research_ready
    reviewed_rec = LeadRegisterRecord(
        candidate_id="cand:test-02",
        record_number=2,
        company_name="Beta Engineering S.r.l.",
        official_website="https://beta-eng.it",
        business_activities="Civil Engineering",
        verification_status="OPERATOR_REVIEWED",
        evidence_urls=["https://beta-eng.it"],
        provenance_source="operator_edited",
    )
    readiness_reviewed = evaluate_research_readiness(MockCand(), prefetched_reg_record=reviewed_rec, has_prefetched=True)
    assert readiness_reviewed["research_status"] == "research_ready"
    assert readiness_reviewed["verification_status"] == "OPERATOR_REVIEWED"


# ==============================================================================
# Phase 4: Python Compatibility P1
# ==============================================================================

def test_python_compatibility_router_imports():
    """
    Verify that backend.sales_ui.router imports cleanly and has Set imported from typing.
    """
    import backend.sales_ui.router as router_mod
    assert hasattr(router_mod, "Set")
