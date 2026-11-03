"""
sales_engine/benchmark/stage05a_scorer.py
Deterministic, Auditable Benchmark Scorer for Stage 05A LLM Research Benchmark.
Evaluates model outputs against reference ground-truth facts and evidence rules.

Scoring Rubric (100 points total):
1. Factual extraction accuracy (35 points):
   - Legal name (5 pts)
   - P.IVA checksum & value (5 pts)
   - Address / City (5 pts)
   - Phone number (5 pts)
   - Email address (5 pts)
   - BIM / Engineering capabilities (5 pts)
   - Leadership extraction (Andrea Berro / Gianluca Sartori) (5 pts)
2. Evidence grounding (25 points):
   - Source evidence ID existence in dataset (5 pts)
   - Source URL correctness (5 pts)
   - Verbatim supporting excerpt verification against raw text (10 pts)
   - Provenance completeness (5 pts)
3. Hallucination prevention (20 points):
   - No fake P.IVA or unverified company name (5 pts)
   - Founders/Directors correctly NOT marked as purchasing decision-maker without proof (5 pts)
   - External supplier intake not fabricated into active outsourcing demand (5 pts)
   - Absence of unsupported claims in provenance (5 pts)
4. Unknown & hypothesis handling (10 points):
   - Priority level is Priority B (5 pts)
   - Checksum validity distinguished from official registry verified (official_registry_verified=False) (5 pts)
5. Schema compliance (10 points):
   - Strict Draft-07 validation with zero violations (10 pts)

Non-evaluated models receive status NOT_EVALUATED and are never assigned an arbitrary 0 score.
"""

from __future__ import annotations

import re
import hashlib
from typing import Dict, Any, List, Optional, Tuple

from sales_engine.benchmark.stage05a_schema import validate_stage05a_output


SCORER_VERSION = "stage05a-1.0.0"
RUBRIC_VERSION = "stage05a-rubric-20261101"

# Versioned reference fact-pack for BS Progetti (cand:domain:bs-eng.net)
BS_PROGETTI_REFERENCE_GROUND_TRUTH = {
    "candidate_id": "cand:domain:bs-eng.net",
    "official_domain": "bs-eng.net",
    "legal_name": "BS Progetti S.r.l.",
    "acceptable_legal_names": ["BS Progetti S.r.l.", "BS Progetti s.r.l.", "BS Progetti Srl", "BS Progetti srl"],
    "p_iva": "04095420271",
    "registered_city": "Pianiga",
    "registered_address_snippet": "Via Roma 130",
    "phone": "+39 041 5195480",
    "acceptable_phones": ["+39 041 5195480", "041 5195480", "+390415195480", "0415195480"],
    "email": "info@bs-eng.net",
    "acceptable_emails": ["info@bs-eng.net"],
    "founders": ["Andrea Berro", "Gianluca Sartori"],
    "expected_priority": "Priority B",
    "expected_role_type": "DIRECTOR_OR_FOUNDER",
    "bim_keywords": ["bim", "multidisciplinare", "ingegneria"],
    "supplier_link_snippets": ["collapse-1-3936", "fornitori"],
    "job_link_snippets": ["lavora-con-noi"],
}


def verify_italian_piva(piva_str: Optional[str]) -> bool:
    """Verifies Italian Partita IVA format (11 digits) and Luhn/Agenzia delle Entrate checksum."""
    if not piva_str:
        return False
    clean = re.sub(r"\D", "", piva_str)
    if len(clean) != 11:
        return False
    digits = [int(c) for c in clean]
    s1 = sum(digits[i] for i in range(0, 10, 2))
    s2 = 0
    for i in range(1, 10, 2):
        double = digits[i] * 2
        s2 += double if double < 10 else double - 9
    total = s1 + s2
    control = (10 - (total % 10)) % 10
    return digits[10] == control


def score_stage05a_output(
    parsed_json_data: Any,
    raw_evidence_text: str,
    ground_truth: Dict[str, Any] = BS_PROGETTI_REFERENCE_GROUND_TRUTH,
    compact_dataset_sha256: str = "",
) -> Dict[str, Any]:
    """
    Computes deterministic, auditable benchmark score out of 100 points.
    Returns complete breakdown with point allocations, deductions, and violation explanations.
    """
    if parsed_json_data is None:
        return {
            "scorer_version": SCORER_VERSION,
            "rubric_version": RUBRIC_VERSION,
            "dataset_sha256": compact_dataset_sha256,
            "status": "NOT_EVALUATED",
            "total_score": None,
            "breakdown": {},
            "deductions": ["No model output provided (status is NOT_EVALUATED)."],
        }

    # Step 1: Schema Compliance (10 pts)
    is_valid_schema, schema_violations, data = validate_stage05a_output(parsed_json_data)
    schema_score = 10.0 if is_valid_schema else max(0.0, 10.0 - (len(schema_violations) * 2.5))
    deductions: List[str] = []
    if not is_valid_schema:
        deductions.append(f"Schema violations (-{10.0 - schema_score:.1f} pts): {', '.join(schema_violations[:3])}")

    # Helper to check excerpt grounding against raw_evidence_text
    def check_excerpt_grounding(field_obj: Any) -> Tuple[bool, str]:
        if not isinstance(field_obj, dict):
            return False, "Not an object"
        excerpt = field_obj.get("supporting_excerpt")
        if not excerpt or not isinstance(excerpt, str) or len(excerpt.strip()) < 5:
            return False, "Missing or empty supporting excerpt"
        # Check if snippet exists verbatim (ignoring whitespace differences)
        clean_excerpt = re.sub(r"[^\w\s]", "", excerpt).strip().lower()
        clean_raw = re.sub(r"[^\w\s]", "", raw_evidence_text).strip().lower()
        sub_len = min(30, len(clean_excerpt))
        if clean_excerpt[:sub_len] in clean_raw:
            return True, "Grounded"
        return False, f"Excerpt '{excerpt[:40]}...' not found in raw evidence text"

    # Step 2: Factual Extraction Accuracy (35 pts, 7 fields @ 5 pts each)
    factual_score = 0.0
    factual_details = {}

    # 2.1 Legal name (5 pts)
    comp_name_obj = data.get("company_name", {})
    comp_name_val = comp_name_obj.get("proposed_value") if isinstance(comp_name_obj, dict) else None
    if comp_name_val and any(a.lower() in comp_name_val.lower() for a in ground_truth["acceptable_legal_names"]):
        factual_score += 5.0
        factual_details["company_name"] = 5.0
    else:
        deductions.append(f"Legal name mismatch: expected '{ground_truth['legal_name']}', got '{comp_name_val}' (-5.0 pts)")
        factual_details["company_name"] = 0.0

    # 2.2 P.IVA (5 pts)
    legal_id_obj = data.get("legal_identity", {})
    piva_val = legal_id_obj.get("proposed_value") if isinstance(legal_id_obj, dict) else None
    if piva_val and ground_truth["p_iva"] in piva_val and verify_italian_piva(piva_val):
        factual_score += 5.0
        factual_details["legal_identity_piva"] = 5.0
    else:
        deductions.append(f"P.IVA mismatch or invalid checksum: expected '{ground_truth['p_iva']}', got '{piva_val}' (-5.0 pts)")
        factual_details["legal_identity_piva"] = 0.0

    # 2.3 Address / City (5 pts)
    geo_obj = data.get("geographic_location", {})
    city_val = geo_obj.get("city") if isinstance(geo_obj, dict) else None
    addr_val = geo_obj.get("proposed_value") if isinstance(geo_obj, dict) else None
    city_match = city_val and ground_truth["registered_city"].lower() in city_val.lower()
    addr_match = addr_val and (ground_truth["registered_city"].lower() in addr_val.lower() or ground_truth["registered_address_snippet"].lower() in addr_val.lower())
    if city_match or addr_match:
        factual_score += 5.0
        factual_details["geographic_location"] = 5.0
    else:
        deductions.append(f"Geographic location mismatch: city '{city_val}', address '{addr_val}' does not contain '{ground_truth['registered_city']}' (-5.0 pts)")
        factual_details["geographic_location"] = 0.0

    # 2.4 Phone number (5 pts)
    phone_obj = data.get("telephone_numbers", {})
    phone_val = phone_obj.get("proposed_value") if isinstance(phone_obj, dict) else None
    if phone_val and any(p in re.sub(r"[\s\-\(\)]", "", phone_val) for p in [re.sub(r"[\s\-\(\)]", "", x) for x in ground_truth["acceptable_phones"]]):
        factual_score += 5.0
        factual_details["telephone_numbers"] = 5.0
    else:
        deductions.append(f"Telephone number mismatch: expected '{ground_truth['phone']}', got '{phone_val}' (-5.0 pts)")
        factual_details["telephone_numbers"] = 0.0

    # 2.5 Email address (5 pts)
    email_obj = data.get("contact_email_addresses", {})
    email_val = email_obj.get("proposed_value") if isinstance(email_obj, dict) else None
    if email_val and any(e.lower() in email_val.lower() for e in ground_truth["acceptable_emails"]):
        factual_score += 5.0
        factual_details["contact_email_addresses"] = 5.0
    else:
        deductions.append(f"Email mismatch: expected '{ground_truth['email']}', got '{email_val}' (-5.0 pts)")
        factual_details["contact_email_addresses"] = 0.0

    # 2.6 Business activities / BIM (5 pts)
    biz_obj = data.get("business_activities", {})
    biz_val = str(biz_obj.get("proposed_value", "")) + " " + str(biz_obj.get("bim_capabilities", "")) if isinstance(biz_obj, dict) else ""
    if any(k in biz_val.lower() for k in ground_truth["bim_keywords"]):
        factual_score += 5.0
        factual_details["business_activities"] = 5.0
    else:
        deductions.append(f"Business activities missing BIM/engineering keywords (-5.0 pts)")
        factual_details["business_activities"] = 0.0

    # 2.7 Leadership extraction (5 pts)
    resp_obj = data.get("responsible_person", {})
    resp_val = resp_obj.get("proposed_value") if isinstance(resp_obj, dict) else None
    if resp_val and any(f.lower() in resp_val.lower() for f in ground_truth["founders"]):
        factual_score += 5.0
        factual_details["responsible_person"] = 5.0
    else:
        # If marked as UNVERIFIED or NOT_VERIFIED, partial 2.5 pts if not hallucinated
        if resp_val in [None, "NOT_VERIFIED", "UNVERIFIED"]:
            factual_score += 2.5
            factual_details["responsible_person"] = 2.5
            deductions.append(f"Founders {ground_truth['founders']} not identified, marked as UNVERIFIED (-2.5 pts)")
        else:
            deductions.append(f"Responsible person hallucinated or mismatch: got '{resp_val}' (-5.0 pts)")
            factual_details["responsible_person"] = 0.0

    # Step 3: Evidence Grounding (25 pts)
    grounding_score = 0.0
    grounding_details = {}

    # 3.1 Source Evidence IDs existence (5 pts)
    ev_ids_checked = []
    valid_ev_ids = []
    for k in ["company_name", "legal_identity", "geographic_location", "telephone_numbers", "official_website"]:
        f_obj = data.get(k)
        if isinstance(f_obj, dict):
            ev_id = f_obj.get("source_evidence_id")
            if ev_id:
                ev_ids_checked.append(ev_id)
                if ev_id in raw_evidence_text:
                    valid_ev_ids.append(ev_id)
    if ev_ids_checked and len(valid_ev_ids) == len(ev_ids_checked):
        grounding_score += 5.0
        grounding_details["evidence_id_validity"] = 5.0
    elif valid_ev_ids:
        partial = 5.0 * (len(valid_ev_ids) / max(1, len(ev_ids_checked)))
        grounding_score += partial
        grounding_details["evidence_id_validity"] = round(partial, 1)
        deductions.append(f"Invalid evidence IDs cited: {set(ev_ids_checked) - set(valid_ev_ids)} (-{5.0 - partial:.1f} pts)")
    else:
        grounding_details["evidence_id_validity"] = 0.0
        deductions.append(f"No valid evidence IDs found (-5.0 pts)")

    # 3.2 Source URLs correctness (5 pts)
    urls_checked = 0
    urls_valid = 0
    for k in ["company_name", "legal_identity", "geographic_location", "official_website", "contact_email_addresses"]:
        f_obj = data.get(k)
        if isinstance(f_obj, dict):
            url = f_obj.get("source_url")
            if url and isinstance(url, str):
                urls_checked += 1
                if ground_truth["official_domain"] in url and url.startswith("http"):
                    urls_valid += 1
    if urls_checked > 0 and urls_valid == urls_checked:
        grounding_score += 5.0
        grounding_details["source_url_validity"] = 5.0
    elif urls_valid > 0:
        partial = 5.0 * (urls_valid / urls_checked)
        grounding_score += partial
        grounding_details["source_url_validity"] = round(partial, 1)
        deductions.append(f"Some source URLs invalid or not matching domain (-{5.0 - partial:.1f} pts)")
    else:
        grounding_details["source_url_validity"] = 0.0
        deductions.append("No valid source URLs matching official domain (-5.0 pts)")

    # 3.3 Verbatim supporting excerpt verification against raw text (10 pts)
    excerpts_checked = 0
    excerpts_grounded = 0
    for k in ["company_name", "legal_identity", "geographic_location", "telephone_numbers", "contact_email_addresses"]:
        f_obj = data.get(k)
        if isinstance(f_obj, dict):
            excerpts_checked += 1
            is_gr, reason = check_excerpt_grounding(f_obj)
            if is_gr:
                excerpts_grounded += 1
            else:
                deductions.append(f"Field '{k}' excerpt ungrounded: {reason}")
    if excerpts_checked > 0:
        ex_ratio = excerpts_grounded / excerpts_checked
        pts = 10.0 * ex_ratio
        grounding_score += pts
        grounding_details["verbatim_excerpts"] = round(pts, 1)
    else:
        grounding_details["verbatim_excerpts"] = 0.0

    # 3.4 Provenance completeness (5 pts)
    prov_obj = data.get("provenance_and_verification", {})
    if isinstance(prov_obj, dict) and prov_obj.get("system_derived_provenance") and isinstance(prov_obj.get("overall_confidence_score"), (int, float)):
        grounding_score += 5.0
        grounding_details["provenance_completeness"] = 5.0
    else:
        grounding_details["provenance_completeness"] = 0.0
        deductions.append("Provenance object missing or incomplete (-5.0 pts)")

    # Step 4: Hallucination Prevention (20 pts, 4 checks @ 5 pts each)
    hallucination_score = 0.0
    hallucination_details = {}

    # 4.1 No fake P.IVA (5 pts)
    if legal_id_obj.get("piva_checksum_valid") is True and piva_val and not verify_italian_piva(piva_val):
        deductions.append("Hallucinated valid checksum for invalid P.IVA (-5.0 pts)")
        hallucination_details["piva_integrity"] = 0.0
    else:
        hallucination_score += 5.0
        hallucination_details["piva_integrity"] = 5.0

    # 4.2 Founders/Directors correctly NOT marked as purchasing decision-maker (5 pts)
    role_type = resp_obj.get("role_type") if isinstance(resp_obj, dict) else None
    if role_type == "PURCHASING_DECISION_MAKER":
        deductions.append("Hallucinated founder/manager as PURCHASING_DECISION_MAKER without procurement proof (-5.0 pts)")
        hallucination_details["decision_maker_restraint"] = 0.0
    else:
        hallucination_score += 5.0
        hallucination_details["decision_maker_restraint"] = 5.0

    # 4.3 Supplier intake not fabricated into active outsourcing demand (5 pts)
    desc_obj = data.get("descriptions_and_notes", {})
    notes_val = str(desc_obj.get("proposed_value", "")) + " " + str(desc_obj.get("work_package_hypothesis", "")) if isinstance(desc_obj, dict) else ""
    fake_demand_terms = ["active tender", "guaranteed subcontract", "outsourcing contract open", "immediate tender hiring"]
    if any(t in notes_val.lower() for t in fake_demand_terms):
        deductions.append("Fabricated active tender/subcontracting contract from vendor portal (-5.0 pts)")
        hallucination_details["subcontracting_restraint"] = 0.0
    else:
        hallucination_score += 5.0
        hallucination_details["subcontracting_restraint"] = 5.0

    # 4.4 Absence of unsupported claims (5 pts)
    unsupported = prov_obj.get("unsupported_claims_detected", []) if isinstance(prov_obj, dict) else []
    if isinstance(unsupported, list) and len(unsupported) > 0:
        deductions.append(f"Model self-reported or detected unsupported claims: {unsupported[:2]} (-5.0 pts)")
        hallucination_details["unsupported_claims_audit"] = 0.0
    else:
        hallucination_score += 5.0
        hallucination_details["unsupported_claims_audit"] = 5.0

    # Step 5: Unknown & Hypothesis Handling (10 pts, 2 checks @ 5 pts each)
    hypothesis_score = 0.0
    hypothesis_details = {}

    # 5.1 Priority level is Priority B (vendor intake portal without confirmed active project outsourcing) (5 pts)
    prio_obj = data.get("priority_level", {})
    prio_val = prio_obj.get("proposed_value") if isinstance(prio_obj, dict) else None
    if prio_val == ground_truth["expected_priority"]:
        hypothesis_score += 5.0
        hypothesis_details["priority_b_calibration"] = 5.0
    else:
        deductions.append(f"Priority calibration mismatch: expected '{ground_truth['expected_priority']}', got '{prio_val}' (-5.0 pts)")
        hypothesis_details["priority_b_calibration"] = 0.0

    # 5.2 Checksum validity distinguished from official registry verified (official_registry_verified=False) (5 pts)
    reg_verified = legal_id_obj.get("official_registry_verified") if isinstance(legal_id_obj, dict) else None
    if reg_verified is False:
        hypothesis_score += 5.0
        hypothesis_details["official_registry_distinction"] = 5.0
    elif reg_verified is True:
        deductions.append("Conflated local P.IVA checksum with official government registry verification (set official_registry_verified=True) (-5.0 pts)")
        hypothesis_details["official_registry_distinction"] = 0.0
    else:
        # None or null
        hypothesis_score += 2.5
        hypothesis_details["official_registry_distinction"] = 2.5

    total_score = round(schema_score + factual_score + grounding_score + hallucination_score + hypothesis_score, 1)

    return {
        "scorer_version": SCORER_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "dataset_sha256": compact_dataset_sha256,
        "status": "EVALUATED",
        "total_score": total_score,
        "max_score": 100.0,
        "section_scores": {
            "schema_compliance": round(schema_score, 1),
            "factual_extraction": round(factual_score, 1),
            "evidence_grounding": round(grounding_score, 1),
            "hallucination_prevention": round(hallucination_score, 1),
            "unknown_and_hypothesis": round(hypothesis_score, 1),
        },
        "details": {
            "factual": factual_details,
            "grounding": grounding_details,
            "hallucination": hallucination_details,
            "hypothesis": hypothesis_details,
        },
        "deductions": deductions,
    }
