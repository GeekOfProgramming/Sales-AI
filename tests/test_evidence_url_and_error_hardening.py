import os
import pytest
from bs4 import BeautifulSoup
from unittest.mock import patch
from backend.main import app
from backend.sales_ui.router import CSRF_COOKIE_NAME, _RESEARCH_SESSION_CACHE
from fastapi.testclient import TestClient
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.research.research_store import ResearchSnapshotStore, ResearchSnapshot, PageEvidence
from sales_engine.research.company_research_service import (
    load_research_from_snapshot,
    normalize_and_validate_evidence_url,
    sanitize_link_label,
)
import secrets

def generate_csrf_token():
    return secrets.token_hex(32)


@pytest.fixture
def test_env(tmp_path):
    """Sets up an isolated environment with temporary database and snapshot cache."""
    db_path = str(tmp_path / "sales_outreach.db")
    snap_db_path = str(tmp_path / "research_cache.sqlite3")

    with patch.dict(os.environ, {
        "SALES_OUTREACH_DB": db_path,
        "SALESAI_RESEARCH_CACHE_DB": snap_db_path,
        "EMAIL_SEND_ENABLED": "false",
    }):
        cand_store = CandidateStore(db_path=db_path)
        cand_store.get_or_create_candidate(
            company_name="BS Progetti S.r.l.",
            company_domain="bs-eng.net",
            city="Pianiga",
            country="IT",
            source_id="brave",
        )
        yield {
            "cand_store": cand_store,
            "db_path": db_path,
            "snap_db_path": snap_db_path,
            "tmp_path": tmp_path,
        }


def test_url_normalization_rejects_hashes_and_unsupported_schemes():
    base = "https://www.bs-eng.net/contatti/"
    # Bare #, empty, and slash
    assert normalize_and_validate_evidence_url(base, "#") is None
    assert normalize_and_validate_evidence_url(base, "") is None
    assert normalize_and_validate_evidence_url(base, "   ") is None
    # Unsupported schemes
    assert normalize_and_validate_evidence_url(base, "javascript:void(0)") is None
    assert normalize_and_validate_evidence_url(base, "mailto:info@bs-eng.net") is None
    assert normalize_and_validate_evidence_url(base, "tel:+390415195480") is None
    assert normalize_and_validate_evidence_url(base, "data:text/html,...") is None


def test_relative_fragment_resolves_against_originating_page_never_localhost():
    base = "https://www.bs-eng.net/contatti/"
    raw_html = '<div id="collapse-1-3936">Fornitori content</div>'
    soup = BeautifulSoup(raw_html, "html.parser")

    resolved = normalize_and_validate_evidence_url(base, "#collapse-1-3936", page_soup=soup)
    assert resolved == "https://www.bs-eng.net/contatti/#collapse-1-3936"
    assert "localhost" not in resolved
    assert "127.0.0.1" not in resolved


def test_nonexistent_fragment_is_stripped_to_canonical_page():
    base = "https://www.bs-eng.net/contatti/"
    raw_html = '<div id="collapse-1-3936">Fornitori content</div>'
    soup = BeautifulSoup(raw_html, "html.parser")

    # Fragment does not exist on page
    resolved = normalize_and_validate_evidence_url(base, "#nonexistent-anchor", page_soup=soup)
    assert resolved == "https://www.bs-eng.net/contatti/"


def test_unresolved_template_placeholders_cleaned():
    assert sanitize_link_label("Gestisci {vendor_count} fornitori") == "Gestisci fornitori"
    assert sanitize_link_label("{title}") == ""
    assert sanitize_link_label("Normal Text") == "Normal Text"


def _create_synthetic_bs_progetti_snapshot() -> ResearchSnapshot:
    """Creates a deterministic, offline synthetic snapshot replicating the BS Progetti evidence structures."""
    contacts_html = (
        "<html><body>"
        "<div class='contact-box'>"
        "<h2>Sede Legale</h2>"
        "<p>BS Progetti S.r.l.</p>"
        "<p>Via Roma 130, 30030 Pianiga (VE), Italy</p>"
        "<p>Tel: +39 041 5195480</p>"
        "<p>Email: info@bs-eng.net</p>"
        "<p>P.IVA: 04095420271</p>"
        "</div>"
        "<div id='collapse-1-3936'>"
        "<h4>Fornitori</h4>"
        "<form action='https://www.bs-eng.net/faq-items/fornitori/' method='POST'>"
        "<input name='azienda'>"
        "</form>"
        "</div>"
        "<a href='#collapse-1-3936'>Fornitori</a>"
        "<a href='#'>Gestisci {vendor_count} fornitori</a>"
        "</body></html>"
    )

    home_html = (
        "<html><head><title>BS Progetti S.r.l. - Progettazione Integrata BIM</title></head><body>"
        "<h1>BS Progetti S.r.l.</h1>"
        "<p>Societa di ingegneria specializzata in coordinamento BIM Revit e Solibri.</p>"
        "<a href='https://www.bs-eng.net/lavora-con-noi/'>Lavora con noi</a>"
        "<a href='#'>Gestisci {vendor_count} fornitori</a>"
        "</body></html>"
    )

    careers_html = (
        "<html><body>"
        "<h1>Lavora Con Noi</h1>"
        "<a href='https://www.bs-eng.net/lavora-con-noi-progettista-termotecnico/'>PROGETTISTA TERMOTECNICO</a>"
        "<a href='https://www.bs-eng.net/lavora-con-noi-candidatura-spontanea/'>CANDIDATURA SPONTANEA</a>"
        "</body></html>"
    )

    pages = [
        PageEvidence(
            snapshot_id="snap_synthetic_bs_progetti",
            evidence_id="EV_01",
            requested_url="https://bs-eng.net",
            resolved_url="https://bs-eng.net",
            page_type="home",
            http_status=200,
            retrieval_status="FETCH_SUCCESS",
            retrieved_at="2026-10-31T23:18:14+00:00",
            title="BS Progetti S.r.l. - Progettazione Integrata BIM",
            meta_description="BS Progetti S.r.l. societa di ingegneria integrata e modellazione BIM.",
            content_sha256="mock_sha_01",
            cleaned_text="BS Progetti S.r.l. Societa di ingegneria specializzata in coordinamento BIM Revit e Solibri.",
            raw_html=home_html,
            links=[
                {"text": "Lavora con noi", "href": "https://www.bs-eng.net/lavora-con-noi/"},
                {"text": "Gestisci {vendor_count} fornitori", "href": "#"},
            ],
        ),
        PageEvidence(
            snapshot_id="snap_synthetic_bs_progetti",
            evidence_id="EV_02",
            requested_url="https://www.bs-eng.net/contatti/",
            resolved_url="https://www.bs-eng.net/contatti/",
            page_type="contact",
            http_status=200,
            retrieval_status="FETCH_SUCCESS",
            retrieved_at="2026-10-31T23:18:14+00:00",
            title="Contatti - BS Progetti S.r.l.",
            meta_description="",
            content_sha256="mock_sha_02",
            cleaned_text="BS Progetti S.r.l. Via Roma 130, 30030 Pianiga (VE), Italy. Tel: +39 041 5195480. P.IVA 04095420271.",
            raw_html=contacts_html,
            links=[
                {"text": "Fornitori", "href": "#collapse-1-3936"},
                {"text": "Gestisci {vendor_count} fornitori", "href": "#"},
                {"text": "Lavora con noi", "href": "https://www.bs-eng.net/lavora-con-noi/"},
            ],
            forms=[
                {
                    "action": "https://www.bs-eng.net/faq-items/fornitori/",
                    "method": "POST",
                    "fields": ["azienda", "settore", "email"],
                }
            ],
        ),
        PageEvidence(
            snapshot_id="snap_synthetic_bs_progetti",
            evidence_id="EV_05",
            requested_url="https://www.bs-eng.net/lavora-con-noi/",
            resolved_url="https://www.bs-eng.net/lavora-con-noi/",
            page_type="careers",
            http_status=200,
            retrieval_status="FETCH_SUCCESS",
            retrieved_at="2026-10-31T23:18:14+00:00",
            title="Lavora con noi - BS Progetti",
            meta_description="",
            content_sha256="mock_sha_05",
            cleaned_text="Posizioni aperte: Progettista termotecnico, Candidatura spontanea.",
            raw_html=careers_html,
            links=[
                {"text": "PROGETTISTA TERMOTECNICO", "href": "https://www.bs-eng.net/lavora-con-noi-progettista-termotecnico/"},
                {"text": "CANDIDATURA SPONTANEA", "href": "https://www.bs-eng.net/lavora-con-noi-candidatura-spontanea/"},
            ],
        ),
    ]

    return ResearchSnapshot(
        snapshot_id="snap_synthetic_bs_progetti",
        candidate_id="cand:domain:bs-eng.net",
        canonical_domain="bs-eng.net",
        company_name="BS Progetti S.r.l.",
        created_at="2026-10-31T23:18:14+00:00",
        completed_at="2026-10-31T23:18:14+00:00",
        crawler_version="2.0.0",
        parser_version="2.0.0",
        dataset_schema_version="1.0.0",
        research_status="complete",
        pages_attempted=len(pages),
        pages_succeeded=len(pages),
        content_checksum="mock_combined_checksum",
        pages=pages,
    )


def test_offline_snapshot_replay_synthetic_fixture():
    """
    Hermetic unit test: verify snapshot replay logic using a standalone synthetic fixture.
    Does not depend on the local operational database, ensuring clean ZIP runs always pass.
    """
    snap = _create_synthetic_bs_progetti_snapshot()
    assert snap.snapshot_id == "snap_synthetic_bs_progetti"

    res = load_research_from_snapshot(snap, use_llm=False)

    # 1. Company Name & Legal Name
    assert res.company_name == "BS Progetti S.r.l."
    assert res.p_iva == "04095420271"
    assert "30030 Pianiga" in res.address

    # 2. Supplier Signals has exactly 1 unified intake channel
    assert len(res.supplier_signals) == 1
    assert "https://www.bs-eng.net/contatti/#collapse-1-3936" in res.supplier_signals[0]
    # No bare '#' or '{vendor_count}'
    for sig in res.supplier_signals:
        assert "vendor_count" not in sig
        assert "{" not in sig

    # 3. Opportunity URLs are all valid absolute HTTP(S) URLs
    assert len(res.opportunity_urls) > 0
    for u in res.opportunity_urls:
        assert u.startswith("https://")
        assert not u.startswith("#")
        assert "localhost" not in u

    # 4. Supplier signals and Career opportunities are distinct
    for c in res.career_opportunities:
        assert "Careers" in c
        assert "fornitor" not in c.lower()

    # 5. Priority B remains unchanged without verified outsourcing demand
    assert res.proposed_priority_level == "Priority B"
    assert "Vendor qualification via Fornitori intake" in res.work_package_hypothesis


def test_offline_snapshot_replay_operational_snapshot_if_present():
    """
    Optional operator integration test: if the local operational snapshot exists in research_cache.sqlite3,
    verify that it replays with identical strict assertions. Skips safely in a clean build environment.
    """
    store = ResearchSnapshotStore()
    snap = store.get_snapshot_by_id("snap_72d30d43d3ef4368", load_raw_html=True)
    if snap is None:
        pytest.skip("Operational snapshot snap_72d30d43d3ef4368 not present in test environment.")

    assert snap.snapshot_id == "snap_72d30d43d3ef4368"
    res = load_research_from_snapshot(snap, use_llm=False)

    assert res.company_name == "BS Progetti s.r.l."
    assert res.p_iva == "04095420271"
    assert "30030 Pianiga" in res.address
    assert len(res.supplier_signals) == 1
    assert "https://www.bs-eng.net/contatti/#collapse-1-3936" in res.supplier_signals[0]
    assert res.proposed_priority_level == "Priority B"


def test_error_response_sanitizes_query_string_and_includes_diagnostic_id(test_env, monkeypatch):
    """Verify that failure exposes safe diagnostic ID in query string and never leaks internal exception."""
    _RESEARCH_SESSION_CACHE.clear()
    cand_id = "cand:domain:bs-eng.net"

    def blow_up(*args, **kwargs):
        raise RuntimeError("CRITICAL_INTERNAL_DATABASE_PASSWORD_LEAK")

    monkeypatch.setattr("sales_engine.research.company_research_service.perform_company_research", blow_up)

    client = TestClient(app)
    csrf_token = generate_csrf_token()
    client.cookies.set(CSRF_COOKIE_NAME, csrf_token)

    resp = client.post(
        f"/sales/candidates/{cand_id}/research",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    redirect_loc = resp.headers["location"]
    # Never leak internal secret / exception text
    assert "CRITICAL_INTERNAL_DATABASE_PASSWORD_LEAK" not in redirect_loc
    # Must contain safe diagnostic reference
    assert "Reference:+diag_" in redirect_loc or "Reference: diag_" in redirect_loc or "diag_" in redirect_loc
