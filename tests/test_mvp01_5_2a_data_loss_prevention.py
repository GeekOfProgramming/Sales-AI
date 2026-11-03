"""
Focused regression tests for MVP-01.5.2a & MVP-01.5.2b data-loss prevention:
1. Real HTTP UI Edit -> Repeat pilot save (single and Save All) preserves all operator-edited 13 fields.
2. Repeat research on existing candidate preserves manual APPROVED/REJECTED decisions and outreach permissions.
3. Ordinary descriptions & notes editing preserves manual review rationale audit trail.
4. DNC and historical contact status / outreach events remain intact across research and register edits.
5. Repeat saves do not duplicate candidates or child observations.
"""

import os
import io
import csv
import openpyxl
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany, CompanyTier, DiscoveryStatus, QualificationOutcome
from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.research.pilot_research import (
    get_pilot_research_profiles,
    save_pilot_candidate_to_store,
)


def test_real_http_edit_and_repeat_pilot_save(tmp_path):
    """
    CRITICAL DEFECT MVP-01.5.2b REGRESSION:
    Real HTTP POST /sales/register/{candidate_id}/update followed by repeat pilot save (and Save All)
    must NEVER overwrite operator-edited Lead Register fields.
    """
    test_db = str(tmp_path / "real_http_edit_repeat_save.db")

    with patch.dict(os.environ, {"SALES_OUTREACH_DB": test_db, "EMAIL_SEND_ENABLED": "false"}):
        with TestClient(app) as client:
            # 1. Fetch CSRF token
            resp = client.get("/sales/research/pilot")
            assert resp.status_code == 200
            csrf_token = resp.cookies.get("sales_csrf_token")

            # 2. Save curated pilot candidate incide.it via real HTTP endpoint
            save_resp = client.post(
                "/sales/research/pilot/save-single",
                data={"domain": "incide.it", "csrf_token": csrf_token},
                cookies={"sales_csrf_token": csrf_token},
                follow_redirects=True
            )
            assert save_resp.status_code == 200

            cand_id = "cand:domain:incide.it"
            cand_store = CandidateStore(db_path=test_db)
            reg_store = LeadRegisterStore(db_path=test_db)

            initial_cand = cand_store.get_by_id(cand_id)
            assert initial_cand is not None
            initial_reg = reg_store.get_record(cand_id)
            assert initial_reg is not None

            # 3. Edit 13-field record through the REAL HTTP endpoint
            edit_resp = client.post(
                f"/sales/register/{cand_id}/update",
                data={
                    "company_name": "Incide Engineering S.r.l. (Operator Verified)",
                    "priority_level": "A",
                    "priority_rationale": "Key structural partner for Veneto infrastructure",
                    "legal_identity": "P.IVA 03528250284",
                    "geographic_location": "Sarmeola di Rubano, Padova, Italy",
                    "telephone_numbers": "+39 049 8976755",
                    "business_activities": "Ingegneria strutturale, progettazione BIM, facciate continue",
                    "official_website": "https://www.incide.it",
                    "responsible_person": "Ing. Gianluca Vallerini (Managing Director)",
                    "descriptions_and_notes": "HAND-WRITTEN OPERATOR NOTE: Confidential meeting held with engineering director.\nFollow-up scheduled.",
                    "job_advertisement_links": "https://www.incide.it/progettista-bim-strutture/",
                    "other_links_and_references": "https://www.incide.it/progetti | Revit | Tekla | Navisworks",
                    "contact_email_addresses": "direct.vallerini@incide.invalid",
                    "csrf_token": csrf_token
                },
                cookies={"sales_csrf_token": csrf_token},
                follow_redirects=True
            )
            assert edit_resp.status_code == 200

            # 4. Reload from fresh store instance and assert provenance is operator_edited
            del reg_store
            store_after_edit = LeadRegisterStore(db_path=test_db)
            edited_reg = store_after_edit.get_record(cand_id)
            assert edited_reg.provenance_source == "operator_edited"
            assert edited_reg.descriptions_and_notes == "HAND-WRITTEN OPERATOR NOTE: Confidential meeting held with engineering director.\nFollow-up scheduled."
            assert edited_reg.contact_email_addresses == "direct.vallerini@incide.invalid"
            assert edited_reg.responsible_person == "Ing. Gianluca Vallerini (Managing Director)"

            # 5. Repeat single save of the same pilot lead via real HTTP endpoint
            repeat_single_resp = client.post(
                "/sales/research/pilot/save-single",
                data={"domain": "incide.it", "csrf_token": csrf_token},
                cookies={"sales_csrf_token": csrf_token},
                follow_redirects=True
            )
            assert repeat_single_resp.status_code == 200

            # 6. Verify edits are strictly preserved after repeat single save
            del store_after_edit
            store_after_single = LeadRegisterStore(db_path=test_db)
            reg_after_single = store_after_single.get_record(cand_id)
            assert reg_after_single.descriptions_and_notes == "HAND-WRITTEN OPERATOR NOTE: Confidential meeting held with engineering director.\nFollow-up scheduled."
            assert reg_after_single.contact_email_addresses == "direct.vallerini@incide.invalid"
            assert reg_after_single.responsible_person == "Ing. Gianluca Vallerini (Managing Director)"
            assert reg_after_single.company_name == "Incide Engineering S.r.l. (Operator Verified)"

            # 7. Repeat via Save All endpoint
            save_all_resp = client.post(
                "/sales/research/pilot/save-all",
                data={"csrf_token": csrf_token},
                cookies={"sales_csrf_token": csrf_token},
                follow_redirects=True
            )
            assert save_all_resp.status_code == 200

            # 8. Verify edits are strictly preserved after Save All
            del store_after_single
            store_after_all = LeadRegisterStore(db_path=test_db)
            reg_after_all = store_after_all.get_record(cand_id)
            assert reg_after_all.descriptions_and_notes == "HAND-WRITTEN OPERATOR NOTE: Confidential meeting held with engineering director.\nFollow-up scheduled."
            assert reg_after_all.contact_email_addresses == "direct.vallerini@incide.invalid"
            assert reg_after_all.responsible_person == "Ing. Gianluca Vallerini (Managing Director)"
            assert reg_after_all.company_name == "Incide Engineering S.r.l. (Operator Verified)"

            # 9. Verify observations not duplicated
            obs = cand_store.get_observations_for_candidate(cand_id)
            assert len(obs) == 1, f"Expected 1 observation for pilot, found {len(obs)}"


def test_research_new_preserves_existing_approval_and_permissions(tmp_path):
    """
    CRITICAL BUG 2 REGRESSION:
    Submitting research on an existing candidate must NEVER implicitly change approval,
    rejection, DNC status, or cold_outreach_allowed permissions.
    """
    test_db = str(tmp_path / "research_preserves_permissions.db")

    with patch.dict(os.environ, {"SALES_OUTREACH_DB": test_db, "EMAIL_SEND_ENABLED": "false"}):
        with TestClient(app) as client:
            resp = client.get("/sales/register")
            csrf_token = resp.cookies.get("sales_csrf_token")

            # Setup existing candidate with manual APPROVED and cold_outreach_allowed=True
            cand_store = CandidateStore(db_path=test_db)
            cand, _ = cand_store.get_or_create_candidate("Approved Studio", "approved-studio.it", country="Italy")
            cand.manual_review_decision = "APPROVED"
            cand.cold_outreach_allowed = True
            cand.historical_contact_status = "uncontacted"
            cand.manual_review_notes = "Special approval by executive operator"
            cand_store.save_candidate(cand)

            # Submitting research on the same company/domain
            with patch("sales_engine.research.lead_researcher.extract_website_public_facts") as mock_facts:
                mock_facts.return_value = {
                    "canonical_domain": "approved-studio.it",
                    "source_url": "https://www.approved-studio.it",
                    "company_name": "Approved Studio",
                    "telephone_numbers": "+39 02 1234567",
                    "contact_emails": "info@approved-studio.it",
                    "vat_number": "01234567890",
                    "meta_description": "Progettazione BIM",
                    "page_title": "Approved Studio Site",
                    "status": "FETCH_SUCCESS",
                    "error_message": None,
                }
                research_resp = client.post(
                    "/sales/register/research-new",
                    data={
                        "company_name": "Approved Studio",
                        "website_url": "https://www.approved-studio.it",
                        "priority_level": "A",
                        "operator_notes": "Routine re-research",
                        "csrf_token": csrf_token
                    },
                    cookies={"sales_csrf_token": csrf_token},
                    follow_redirects=True
                )
                assert research_resp.status_code == 200

            # Verify permissions and manual decisions remained intact
            reloaded_cand = cand_store.get_by_id(cand.candidate_id)
            assert reloaded_cand.manual_review_decision == "APPROVED", "manual_review_decision was reset!"
            assert reloaded_cand.cold_outreach_allowed is True, "cold_outreach_allowed was reset to False!"
            assert reloaded_cand.historical_contact_status == "uncontacted"
            assert reloaded_cand.manual_review_notes == "Special approval by executive operator"


def test_research_new_preserves_rejected_and_dnc_candidate(tmp_path):
    """
    CRITICAL BUG 2 REGRESSION PART B:
    Rejected/DNC/Suppressed candidate retains rejection and sending restrictions when re-researched.
    """
    test_db = str(tmp_path / "research_preserves_rejected.db")

    with patch.dict(os.environ, {"SALES_OUTREACH_DB": test_db, "EMAIL_SEND_ENABLED": "false"}):
        with TestClient(app) as client:
            resp = client.get("/sales/register")
            csrf_token = resp.cookies.get("sales_csrf_token")

            cand_store = CandidateStore(db_path=test_db)
            cand, _ = cand_store.get_or_create_candidate("Rejected Firm", "rejected-firm.it")
            cand.manual_review_decision = "REJECTED"
            cand.cold_outreach_allowed = False
            cand.suppressed = True
            cand.historical_contact_status = "dnc"
            cand.manual_review_notes = "Explicit Do Not Contact request on 2026-05-10"
            cand_store.save_candidate(cand)

            with patch("sales_engine.research.lead_researcher.extract_website_public_facts") as mock_facts:
                mock_facts.return_value = {
                    "canonical_domain": "rejected-firm.it",
                    "source_url": "https://www.rejected-firm.it",
                    "company_name": "Rejected Firm",
                    "telephone_numbers": "+39 06 998877",
                    "contact_emails": "office@rejected-firm.it",
                    "vat_number": "11223344556",
                    "meta_description": "General architectural services",
                    "page_title": "Rejected Firm Site",
                    "status": "FETCH_SUCCESS",
                    "error_message": None,
                }
                research_resp = client.post(
                    "/sales/register/research-new",
                    data={
                        "company_name": "Rejected Firm",
                        "website_url": "https://www.rejected-firm.it",
                        "priority_level": "C",
                        "operator_notes": "Checking if firm changed website",
                        "csrf_token": csrf_token
                    },
                    cookies={"sales_csrf_token": csrf_token},
                    follow_redirects=True
                )
                assert research_resp.status_code == 200

            reloaded_cand = cand_store.get_by_id(cand.candidate_id)
            assert reloaded_cand.manual_review_decision == "REJECTED"
            assert reloaded_cand.cold_outreach_allowed is False
            assert reloaded_cand.suppressed is True
            assert reloaded_cand.historical_contact_status == "dnc"
            assert reloaded_cand.manual_review_notes == "Explicit Do Not Contact request on 2026-05-10"


def test_editing_13_field_record_preserves_manual_review_notes(tmp_path):
    """
    CRITICAL BUG 3 REGRESSION:
    Editing descriptions_and_notes in the 13-field register must NEVER overwrite
    the candidate's manual_review_notes audit trail (e.g. rejection reason).
    """
    test_db = str(tmp_path / "edit_notes_preserves_audit.db")

    with patch.dict(os.environ, {"SALES_OUTREACH_DB": test_db, "EMAIL_SEND_ENABLED": "false"}):
        with TestClient(app) as client:
            resp = client.get("/sales/register")
            csrf_token = resp.cookies.get("sales_csrf_token")

            # Setup candidate with distinct manual review reason
            cand_store = CandidateStore(db_path=test_db)
            cand, _ = cand_store.get_or_create_candidate("Competitor Engineering", "competitor-eng.it")
            cand.manual_review_decision = "REJECTED"
            rejection_reason = "CRITICAL AUDIT: Firm is a direct software vendor competitor, do not pitch."
            cand.manual_review_notes = rejection_reason
            cand.cold_outreach_allowed = False
            cand_store.save_candidate(cand)

            # Operator edits ordinary company description in 13-field register
            edit_resp = client.post(
                f"/sales/register/{cand.candidate_id}/update",
                data={
                    "company_name": "Competitor Engineering S.r.l.",
                    "official_website": "https://competitor-eng.it",
                    "priority_level": "C",
                    "descriptions_and_notes": "Engineering firm based in Milan. Offers Revit plugin solutions.",
                    "csrf_token": csrf_token
                },
                cookies={"sales_csrf_token": csrf_token},
                follow_redirects=True
            )
            assert edit_resp.status_code == 200

            # Verify register has new description, but CandidateStore retains original manual rejection reason
            reg_store = LeadRegisterStore(db_path=test_db)
            reg_rec = reg_store.get_record(cand.candidate_id)
            assert reg_rec.descriptions_and_notes == "Engineering firm based in Milan. Offers Revit plugin solutions."

            reloaded_cand = cand_store.get_by_id(cand.candidate_id)
            assert reloaded_cand.manual_review_notes == rejection_reason, "manual_review_notes was overwritten by ordinary descriptions!"
            assert reloaded_cand.manual_review_decision == "REJECTED"
