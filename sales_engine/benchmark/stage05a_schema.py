"""
sales_engine/benchmark/stage05a_schema.py
Strict JSON Schema and Output Specification for Stage 05A Multi-Provider LLM Research Benchmark.
Aligned 1:1 with the historical pyBIM 13-field LeadRegisterRecord and SalesAI Evidence Models.
Provides full server-side validation using jsonschema with path-level violation reporting
and root-wrapper normalization.
"""

from __future__ import annotations

import json
from typing import Dict, Any, List, Tuple, Optional
import jsonschema


# Canonical 13-field JSON Schema Draft-07 specification
STAGE_05A_OUTPUT_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Stage05A_LeadRegisterEnrichment",
    "type": "object",
    "required": [
        "company_name",
        "priority_level",
        "priority_rationale",
        "legal_identity",
        "geographic_location",
        "telephone_numbers",
        "business_activities",
        "official_website",
        "responsible_person",
        "descriptions_and_notes",
        "job_advertisement_links",
        "other_links_and_references",
        "contact_email_addresses",
        "provenance_and_verification",
    ],
    "properties": {
        "company_name": {
            "type": "object",
            "required": ["proposed_value", "verification_status", "source_evidence_id", "source_url", "supporting_excerpt"],
            "properties": {
                "proposed_value": {"type": ["string", "null"]},
                "verification_status": {"type": "string", "enum": ["VERIFIED_FACT", "UNVERIFIED", "NOT_VERIFIED", "INCORRECT"]},
                "source_evidence_id": {"type": ["string", "null"]},
                "source_url": {"type": ["string", "null"]},
                "supporting_excerpt": {"type": ["string", "null"]},
                "uncertainty_indicators": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "priority_level": {
            "type": "object",
            "required": ["proposed_value", "verification_status", "source_evidence_id", "source_url", "supporting_excerpt"],
            "properties": {
                "proposed_value": {"type": "string", "enum": ["Priority A", "Priority B", "Priority C", "NOT_VERIFIED"]},
                "verification_status": {"type": "string", "enum": ["COMMERCIAL_HYPOTHESIS", "VERIFIED_FACT", "UNVERIFIED", "NOT_VERIFIED"]},
                "source_evidence_id": {"type": ["string", "null"]},
                "source_url": {"type": ["string", "null"]},
                "supporting_excerpt": {"type": ["string", "null"]},
                "uncertainty_indicators": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "priority_rationale": {
            "type": "object",
            "required": ["proposed_value", "verification_status", "source_evidence_id", "source_url", "supporting_excerpt"],
            "properties": {
                "proposed_value": {"type": ["string", "null"]},
                "verification_status": {"type": "string", "enum": ["COMMERCIAL_HYPOTHESIS", "VERIFIED_FACT", "UNVERIFIED", "NOT_VERIFIED"]},
                "source_evidence_id": {"type": ["string", "null"]},
                "source_url": {"type": ["string", "null"]},
                "supporting_excerpt": {"type": ["string", "null"]},
                "uncertainty_indicators": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "legal_identity": {
            "type": "object",
            "required": ["proposed_value", "verification_status", "source_evidence_id", "source_url", "supporting_excerpt"],
            "properties": {
                "proposed_value": {"type": ["string", "null"]},
                "piva_checksum_valid": {"type": ["boolean", "null"]},
                "official_registry_verified": {"type": ["boolean", "null"]},
                "verification_status": {"type": "string", "enum": ["VERIFIED_FACT", "UNVERIFIED", "NOT_VERIFIED"]},
                "source_evidence_id": {"type": ["string", "null"]},
                "source_url": {"type": ["string", "null"]},
                "supporting_excerpt": {"type": ["string", "null"]},
                "uncertainty_indicators": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "geographic_location": {
            "type": "object",
            "required": ["proposed_value", "verification_status", "source_evidence_id", "source_url", "supporting_excerpt"],
            "properties": {
                "proposed_value": {"type": ["string", "null"]},
                "city": {"type": ["string", "null"]},
                "country": {"type": ["string", "null"]},
                "verification_status": {"type": "string", "enum": ["VERIFIED_FACT", "UNVERIFIED", "NOT_VERIFIED"]},
                "source_evidence_id": {"type": ["string", "null"]},
                "source_url": {"type": ["string", "null"]},
                "supporting_excerpt": {"type": ["string", "null"]},
                "uncertainty_indicators": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "telephone_numbers": {
            "type": "object",
            "required": ["proposed_value", "verification_status", "source_evidence_id", "source_url", "supporting_excerpt"],
            "properties": {
                "proposed_value": {"type": ["string", "null"]},
                "verification_status": {"type": "string", "enum": ["VERIFIED_FACT", "UNVERIFIED", "NOT_VERIFIED"]},
                "source_evidence_id": {"type": ["string", "null"]},
                "source_url": {"type": ["string", "null"]},
                "supporting_excerpt": {"type": ["string", "null"]},
                "uncertainty_indicators": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "business_activities": {
            "type": "object",
            "required": ["proposed_value", "verification_status", "source_evidence_id", "source_url", "supporting_excerpt"],
            "properties": {
                "proposed_value": {"type": ["string", "null"]},
                "bim_capabilities": {"type": ["string", "null"]},
                "verification_status": {"type": "string", "enum": ["VERIFIED_FACT", "COMMERCIAL_HYPOTHESIS", "UNVERIFIED", "NOT_VERIFIED"]},
                "source_evidence_id": {"type": ["string", "null"]},
                "source_url": {"type": ["string", "null"]},
                "supporting_excerpt": {"type": ["string", "null"]},
                "uncertainty_indicators": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "official_website": {
            "type": "object",
            "required": ["proposed_value", "verification_status", "source_evidence_id", "source_url", "supporting_excerpt"],
            "properties": {
                "proposed_value": {"type": ["string", "null"]},
                "verification_status": {"type": "string", "enum": ["VERIFIED_FACT", "UNVERIFIED", "NOT_VERIFIED"]},
                "source_evidence_id": {"type": ["string", "null"]},
                "source_url": {"type": ["string", "null"]},
                "supporting_excerpt": {"type": ["string", "null"]},
                "uncertainty_indicators": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "responsible_person": {
            "type": "object",
            "required": ["proposed_value", "role_type", "verification_status", "source_evidence_id", "source_url", "supporting_excerpt"],
            "properties": {
                "proposed_value": {"type": ["string", "null"]},
                "role_type": {"type": "string", "enum": ["PURCHASING_DECISION_MAKER", "DIRECTOR_OR_FOUNDER", "TECHNICAL_CONTACT", "UNVERIFIED_STAFF", "NOT_VERIFIED"]},
                "verification_status": {"type": "string", "enum": ["VERIFIED_FACT", "UNVERIFIED", "NOT_VERIFIED"]},
                "source_evidence_id": {"type": ["string", "null"]},
                "source_url": {"type": ["string", "null"]},
                "supporting_excerpt": {"type": ["string", "null"]},
                "uncertainty_indicators": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "descriptions_and_notes": {
            "type": "object",
            "required": ["proposed_value", "work_package_hypothesis", "verification_status", "source_evidence_id", "source_url", "supporting_excerpt"],
            "properties": {
                "proposed_value": {"type": ["string", "null"]},
                "work_package_hypothesis": {"type": ["string", "null"]},
                "verification_status": {"type": "string", "enum": ["COMMERCIAL_HYPOTHESIS", "VERIFIED_FACT", "UNVERIFIED", "NOT_VERIFIED"]},
                "source_evidence_id": {"type": ["string", "null"]},
                "source_url": {"type": ["string", "null"]},
                "supporting_excerpt": {"type": ["string", "null"]},
                "uncertainty_indicators": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "job_advertisement_links": {
            "type": "object",
            "required": ["proposed_value", "urls", "verification_status", "source_evidence_id", "source_url", "supporting_excerpt"],
            "properties": {
                "proposed_value": {"type": ["string", "null"]},
                "urls": {"type": "array", "items": {"type": "string"}},
                "verification_status": {"type": "string", "enum": ["VERIFIED_FACT", "UNVERIFIED", "NOT_VERIFIED"]},
                "source_evidence_id": {"type": ["string", "null"]},
                "source_url": {"type": ["string", "null"]},
                "supporting_excerpt": {"type": ["string", "null"]},
                "uncertainty_indicators": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "other_links_and_references": {
            "type": "object",
            "required": ["proposed_value", "supplier_intake_urls", "portfolio_urls", "verification_status", "source_evidence_id", "source_url", "supporting_excerpt"],
            "properties": {
                "proposed_value": {"type": ["string", "null"]},
                "supplier_intake_urls": {"type": "array", "items": {"type": "string"}},
                "portfolio_urls": {"type": "array", "items": {"type": "string"}},
                "verification_status": {"type": "string", "enum": ["VERIFIED_FACT", "UNVERIFIED", "NOT_VERIFIED"]},
                "source_evidence_id": {"type": ["string", "null"]},
                "source_url": {"type": ["string", "null"]},
                "supporting_excerpt": {"type": ["string", "null"]},
                "uncertainty_indicators": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "contact_email_addresses": {
            "type": "object",
            "required": ["proposed_value", "verification_status", "source_evidence_id", "source_url", "supporting_excerpt"],
            "properties": {
                "proposed_value": {"type": ["string", "null"]},
                "verification_status": {"type": "string", "enum": ["VERIFIED_FACT", "UNVERIFIED", "NOT_VERIFIED"]},
                "source_evidence_id": {"type": ["string", "null"]},
                "source_url": {"type": ["string", "null"]},
                "supporting_excerpt": {"type": ["string", "null"]},
                "uncertainty_indicators": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "provenance_and_verification": {
            "type": "object",
            "required": ["system_derived_provenance", "overall_confidence_score", "unsupported_claims_detected"],
            "properties": {
                "system_derived_provenance": {"type": "string"},
                "overall_confidence_score": {"type": "number"},
                "unsupported_claims_detected": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


BENCHMARK_SYSTEM_PROMPT = """You are a strict, factual engineering CRM research analyst for SalesAI.
Your mission is to enrich a 13-field Lead Register record for an engineering firm strictly using the provided website evidence dataset.

OUTPUT CONTRACT:
Output MUST be a single valid JSON object strictly matching the requested schema. Output JSON only, with zero markdown commentary.

RULES:
1. Ground every extracted fact in the provided document evidence (cite exact Evidence ID like EV_01, EV_02, source URL, and exact verbatim supporting excerpt).
2. For any field without conclusive evidence in the text, return proposed_value as null or "NOT_VERIFIED" and set verification_status to "NOT_VERIFIED".
3. NEVER hallucinate or invent company names, P.IVA, phone numbers, addresses, personal names, decision makers, or external subcontracting demand.
4. Distinguish clearly between internal recruitment (e.g. hiring an employee) and external supplier intake / subcontracting.
5. A supplier portal or vendor form alone represents vendor onboarding infrastructure, NOT confirmed active subcontracting demand. Standalone intake without verified active project outsourcing remains Priority B.
6. Checksum validity of Italian P.IVA does not equal official government business registry verification (set official_registry_verified=false unless official chamber of commerce check is explicitly present).
7. Distinguish founders, technical managers, and actual purchasing decision-makers. Do not label staff as purchasing decision-makers without explicit proof.
"""


def normalize_root_wrapper(data: Any) -> Tuple[Any, Optional[str]]:
    """
    Unwraps common top-level packaging containers (e.g., 'lead_register_enrichment', 'result', 'data')
    only when unambiguous and where the inner dictionary contains canonical lead fields.
    Returns (normalized_dict, container_name_or_None).
    """
    if not isinstance(data, dict):
        return data, None

    known_wrappers = ["lead_register_enrichment", "lead_register", "enrichment", "result", "response", "data"]
    for kw in known_wrappers:
        if kw in data and isinstance(data[kw], dict) and len(data) == 1:
            inner = data[kw]
            # Check if inner looks like a candidate payload
            if any(k in inner for k in ["company_name", "priority_level", "legal_identity"]):
                return inner, kw

    return data, None


def validate_stage05a_output(raw_json_data: Any) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Validates a parsed JSON payload against the canonical STAGE_05A_OUTPUT_SCHEMA.
    Handles unambiguous root container unwrapping.
    Returns:
    - is_valid (bool)
    - list of formatted violation strings (e.g., "company_name.proposed_value: None is not of type 'string'")
    - normalized_data (dict)
    """
    normalized_data, unwrapped_key = normalize_root_wrapper(raw_json_data)

    validator = jsonschema.Draft7Validator(STAGE_05A_OUTPUT_SCHEMA)
    errors = sorted(validator.iter_errors(normalized_data), key=lambda e: e.path)

    violations = []
    for err in errors:
        path = ".".join(str(p) for p in err.path) or "root"
        violations.append(f"{path}: {err.message}")

    is_valid = len(violations) == 0
    return is_valid, violations, normalized_data
